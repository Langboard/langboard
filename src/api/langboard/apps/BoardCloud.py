"""Read-only board file projection over committed native attachments."""

from json import dumps, loads
from mimetypes import guess_type
from pathlib import PurePath
from time import time
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.routing import ApiException
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import StorageName
from langboard_shared.core.utils.Encryptor import Encryptor
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    ProjectAssignedUser,
    ProjectAttachment,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
    User,
)
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.CardVisibilityPolicy import card_visibility_scope
from langboard_shared.Env import Env
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import defer


_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
}


def _item(attachment, project, source, source_type):
    filename = attachment.filename
    media_type = guess_type(filename)[0] or "application/octet-stream"
    kind = "media" if media_type.split("/", 1)[0] in {"image", "audio", "video"} else "file"
    folder = (
        "media"
        if kind == "media"
        else "documents"
        if PurePath(filename).suffix.lower() in _DOCUMENT_EXTENSIONS
        else "inbox"
    )
    if source_type == "project":
        folder = attachment.folder_id
    project_uid, source_uid = project.get_uid(), source.get_uid()
    board_url = f"/board/{project_uid}"
    source_url = (
        board_url if source_type == "project"
        else f"{board_url}/{source_uid}" if source_type == "card"
        else f"{board_url}/wiki/{source_uid}"
    )
    item = {
        "id": f"{source_type}_attachment:{attachment.get_uid()}",
        "attachment_uid": attachment.get_uid(),
        "name": filename,
        "url": attachment.file.path,
        "media_type": media_type,
        "kind": kind,
        "folder_id": folder,
        "created_at": attachment.created_at.isoformat(),
        "updated_at": attachment.updated_at.isoformat(),
        "source": {
            "kind": source_type,
            "project_uid": project_uid,
            **({f"{source_type}_uid": source_uid} if source_type != "project" else {}),
            "title": source.title,
            "board_url": board_url,
            "url": source_url,
        },
    }

    return item


_CURSOR_TTL = 3600
_CANDIDATE_BATCH = 200
_SCAN_LIMIT = 500


def _cursor_scope(actor, project, channel, view):
    return ["board-cloud:2", str(actor.id), str(project.id), channel.value, view]


def _decode_cursor(cursor, scope):
    try:
        if len(cursor) > 4096:
            raise ValueError("Oversized cursor")
        data = loads(Encryptor.decrypt(cursor, Env.JWT_SECRET_KEY))
        if data["scope"] != scope or not 0 <= time() - data["issued"] <= _CURSOR_TTL:
            raise ValueError("Invalid cursor scope or expiry")
        stamp, kind, attachment_id = data["after"]
        if kind not in {"card", "wiki", "project"} or not isinstance(attachment_id, int):
            raise ValueError("Invalid candidate")
        if not isinstance(data["remaining"], int) or not 0 <= data["remaining"] <= 20:
            raise ValueError("Invalid recent allowance")
        from datetime import datetime

        data["after"] = (datetime.fromisoformat(stamp), kind, attachment_id)
        return data
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ApiException.BadRequest_400() from None


def _candidates(project, context, actor, after, count, *, suffix=""):
    """Fetch three bounded streams; SQL applies native source visibility first."""
    assigned = (
        select(ProjectWikiAssignedUser.id)
        .join(ProjectAssignedUser, ProjectAssignedUser.id == ProjectWikiAssignedUser.project_assigned_id)
        .where(
            ProjectWikiAssignedUser.project_wiki_id == ProjectWiki.id,
            ProjectWikiAssignedUser.user_id == actor.id,
            ProjectAssignedUser.project_id == project.id,
            ProjectAssignedUser.user_id == actor.id,
        )
        .exists()
    )
    streams = []
    with DbSession.use(readonly=False) as db:
        for kind, attachment, source, parent, scope in (
            (
                "card",
                CardAttachment,
                Card,
                CardAttachment.card_id,
                and_(card_visibility_scope(context), or_(Card.source_type.is_(None), Card.source_uid.is_(None))),
            ),
            (
                "wiki",
                ProjectWikiAttachment,
                ProjectWiki,
                ProjectWikiAttachment.project_wiki_id,
                or_(ProjectWiki.is_public.is_(True), actor.is_admin, project.owner_id == actor.id, assigned),
            ),
        ):
            query = (
                SqlBuilder.select.tables(attachment, source)
                .join(source, source.id == parent)
                .where(
                    source.project_id == project.id,
                    source.deleted_at.is_(None),
                    scope,
                )
            )
            query = query.options(
                *(
                    [defer(Card.description, raiseload=True), defer(Card.ai_description, raiseload=True)]
                    if kind == "card" else [defer(ProjectWiki.content, raiseload=True)]
                )
            )
            if after is not None:
                stamp, last_kind, last_id = after
                query = query.where(
                    or_(
                        attachment.created_at < stamp,
                        and_(
                            attachment.created_at == stamp,
                            or_(kind + suffix < last_kind, and_(kind + suffix == last_kind, attachment.id < last_id)),
                        ),
                    )
                )
            rows = db.exec(query.order_by(attachment.created_at.desc(), attachment.id.desc()).limit(count)).all()
            streams.extend((row[0], row[1], kind) for row in rows)
        query = SqlBuilder.select.table(ProjectAttachment).where(ProjectAttachment.project_id == project.id)
        if after is not None:
            stamp, last_kind, last_id = after
            query = query.where(
                or_(
                    ProjectAttachment.created_at < stamp,
                    and_(
                        ProjectAttachment.created_at == stamp,
                        or_("project" + suffix < last_kind, and_("project" + suffix == last_kind, ProjectAttachment.id < last_id)),
                    ),
                )
            )
        attachments = db.exec(
            query.order_by(ProjectAttachment.created_at.desc(), ProjectAttachment.id.desc()).limit(count)
        ).all()
        streams.extend((attachment, project, "project") for attachment in attachments)
    return sorted(streams, key=lambda row: (row[0].created_at, row[2], int(row[0].id)), reverse=True)[:count]


def _resolve_cloud_context(service, actor, project_uid, channel):
    try:
        resolved = service.card.resolve_visibility_context(project_uid, actor, channel)
    except (TypeError, ValueError):
        raise ApiException.NotFound_404() from None
    if resolved is None or not resolved[1].project_member:
        raise ApiException.NotFound_404()
    project, context = resolved
    with DbSession.use(readonly=False) as db:
        current_actor = db.exec(
            SqlBuilder.select.table(User).where(User.id == actor.id).execution_options(populate_existing=True)
        ).first()
        role = db.exec(
            SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id
            ).execution_options(populate_existing=True)
        ).first()
        if (
            current_actor is None
            or current_actor.deleted_at is not None
            or not current_actor.activated_at
            or (not current_actor.is_admin and (role is None or not role.is_granted(ProjectRoleAction.Read)))
        ):
            raise ApiException.NotFound_404()
    return project, context, current_actor


def _read_attachment_item(service, current_actor, project, attachment, source, kind, channel):
    file = attachment.file
    if kind == "card":
        if file.storage_name != StorageName.CardAttachment.value:
            return None
        owner = service.card_attachment.resolve_file_owner(file.storage_type, file.storage_name, file.filename)
        if owner is None or owner.id != attachment.id or owner.card_id != source.id or owner.file != file:
            return None
        readable = service.card.resolve_readable_card(project, source, current_actor, channel, metadata_only=True)
        if readable is None or readable[1].is_linked_resource:
            return None
        source = readable[1]
    elif kind == "project":
        if file.storage_name != StorageName.ProjectAttachment.value:
            return None
        owner = service.project_attachment.resolve_readable_file(
            current_actor, file.storage_type, file.storage_name, file.filename
        )
        if owner is None or owner.id != attachment.id or owner.project_id != project.id or owner.file != file:
            return None
        with DbSession.use(readonly=False) as db:
            owner = db.exec(
                SqlBuilder.select.table(ProjectAttachment).where(ProjectAttachment.id == owner.id)
                .execution_options(populate_existing=True)
            ).first()
        if (
            owner is None or owner.project_id != project.id or owner.file != file
            or not service.project_attachment.can_upload(current_actor, project)
        ):
            return None
        source = project
    else:
        if file.storage_name != StorageName.Wiki.value:
            return None
        owner = service.project_wiki.resolve_readable_file(
            current_actor, file.storage_type, file.storage_name, file.filename, metadata_only=True
        )
        if (
            owner is None
            or owner.id != attachment.id
            or owner.project_wiki_id != source.id
            or owner.file != file
        ):
            return None
        with DbSession.use(readonly=False) as db:
            source = db.exec(
                SqlBuilder.select.table(ProjectWiki).where(ProjectWiki.id == owner.project_wiki_id)
                .options(defer(ProjectWiki.content, raiseload=True))
                .execution_options(populate_existing=True)
            ).first()
        if source is None or source.project_id != project.id:
            return None
    if kind in {"card", "wiki"}:
        # Native resolution can overlap a committed replacement or deletion.
        model, parent = (CardAttachment, "card_id") if kind == "card" else (ProjectWikiAttachment, "project_wiki_id")
        with DbSession.use(readonly=False) as db:
            owner = db.exec(
                SqlBuilder.select.table(model).where(model.id == attachment.id)
                .execution_options(populate_existing=True)
            ).first()
        if owner is None or getattr(owner, parent) != source.id or owner.file != file:
            return None
    item = _item(owner, project, source, kind)
    if kind == "project":
        item["can_replace"] = service.project_attachment.can_replace(current_actor, project, owner)
    return item


def read_board_cloud(
    service, actor, project_uid, *, channel=CollaborationChannel.Api, view="all", limit=100, cursor=None
):
    """Bounded traversal over native attachments, with current exact authorization.

    Recent means the first 20 visible items in immutable creation order. Counts
    remain unknown. Empty filtered pages may carry a continuation. Native project
    assets retain persisted folders; all/recent include stored hyperlinks.
    """
    if view not in {"all", "recent", "files", "media", "links"}:
        raise ValueError("Unsupported cloud view")
    limit = max(1, min(200, limit))
    project, context, current_actor = _resolve_cloud_context(service, actor, project_uid, channel)
    if view == "links":
        from .BoardCloudLinks import read_board_links

        return read_board_links(service, project, context, current_actor, channel, limit=limit, cursor=cursor)
    if view in {"all", "recent"}:
        from .BoardCloudLinks import read_board_mixed

        return read_board_mixed(service, project, context, current_actor, channel, view=view, limit=limit, cursor=cursor)
    scope = _cursor_scope(actor, project, channel, view)
    state = _decode_cursor(cursor, scope) if cursor else {"after": None, "remaining": 20, "issued": int(time())}
    after, remaining = state["after"], state["remaining"]
    items, examined, exhausted = [], 0, False
    emitted = []
    target = min(limit, remaining) if view == "recent" else limit
    while len(items) < target and examined < _SCAN_LIMIT:
        count = min(_CANDIDATE_BATCH, _SCAN_LIMIT - examined)
        candidates = _candidates(project, context, current_actor, after, count)
        if not candidates:
            exhausted = True
            break
        for attachment, source, kind in candidates:
            examined += 1
            after = (attachment.created_at, kind, int(attachment.id))
            item = _read_attachment_item(service, current_actor, project, attachment, source, kind, channel)
            if item is None:
                continue
            if view in {"files", "media"} and item["kind"] != ("media" if view == "media" else "file"):
                continue
            items.append(item)
            emitted.append((attachment, source, kind))
            if len(items) == target:
                break
        else:
            if len(candidates) < count:
                exhausted = True
                break
    # Recheck the entire bounded response after traversal. A later source lookup
    # can overlap revocation or replacement of an item already projected above.
    project, context, current_actor = _resolve_cloud_context(service, actor, project_uid, channel)
    items = [
        item for attachment, source, kind in emitted
        if (item := _read_attachment_item(service, current_actor, project, attachment, source, kind, channel)) is not None
        and (view not in {"files", "media"} or item["kind"] == ("media" if view == "media" else "file"))
    ]
    if view == "recent":
        remaining -= len(items)
    has_more = not exhausted and after is not None and (view != "recent" or remaining > 0)
    next_cursor = None
    if has_more:
        next_cursor = Encryptor.encrypt(
            dumps(
                {
                    "scope": scope,
                    "after": [after[0].isoformat(), after[1], after[2]],
                    "remaining": remaining,
                    "issued": state["issued"],
                }
            ),
            Env.JWT_SECRET_KEY,
        )
    return {
        "items": items,
        "folders": [{"id": name.lower(), "name": name} for name in ("Inbox", "Documents", "Media")],
        "total": None,
        "total_is_exact": False,
        "recent_limit": 20,
        "mime_type_source": "filename_extension",
        "has_more": has_more,
        "next_cursor": next_cursor,
        "capabilities": {
            "hyperlinks": False,
            "upload": service.project_attachment.can_upload(current_actor, project),
            "replace": True,
            "links_view": True,
            "all_recent": "attachments_and_links",
        },
    }
