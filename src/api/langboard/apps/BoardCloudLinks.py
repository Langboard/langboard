"""Bounded, read-only hyperlink projection from native stored Markdown."""

from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
from json import dumps, loads
from time import time
from urllib.parse import urlsplit, urlunsplit
from langboard_shared.core.db import DbSession
from langboard_shared.core.routing import ApiException
from langboard_shared.core.utils.Encryptor import Encryptor
from langboard_shared.domain.models import Card, ProjectAssignedUser, ProjectWiki, ProjectWikiAssignedUser
from langboard_shared.domain.services.CardVisibilityPolicy import card_visibility_scope
from langboard_shared.Env import Env
from markdown_it import MarkdownIt
from sqlalchemy import Text, and_, case, cast, func, or_, select


# Normal native imports permit one million content characters. Allow their
# escaped JSON storage representation, but never transfer arbitrary board bodies.
MAX_SOURCE_STORAGE_CHARS = 8 * 1024 * 1024
MAX_CONTENT_CHARS = 1_000_000
MAX_REQUEST_STORAGE_CHARS = 16 * 1024 * 1024
MAX_PAGE_BYTES = 96 * 1024
SOURCE_BATCH = 8
MAX_SCANNED_SOURCES = 32
CURSOR_TTL = 3600


def safe_link(value):
    if not isinstance(value, str) or not 0 < len(value) <= 2048:
        return None
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        parsed.port  # Validate malformed ports without fetching anything.
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return None


class _HTMLLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append((href, ""))


def stored_links(markdown):
    """Use the existing native Markdown parser; code and images are not links."""
    found = {}
    for block in MarkdownIt("commonmark").parse(markdown):
        tokens = block.children or [block]
        for index, token in enumerate(tokens):
            candidates = []
            if token.type == "link_open":
                text = []
                for child in tokens[index + 1 :]:
                    if child.type == "link_close":
                        break
                    if child.type in {"text", "code_inline"}:
                        text.append(child.content)
                label = "".join(text)[:240]
                candidates.append((token.attrGet("href"), label))
            elif token.type in {"html_inline", "html_block"}:
                parser = _HTMLLinks()
                parser.feed(token.content)
                candidates.extend(parser.links)
            for value, label in candidates:
                url = safe_link(value)
                if url and url not in found:
                    found[url] = label or url[:240]
    return list(found.items())


def _scope(actor, project, channel):
    return ["board-cloud-links:1", str(actor.id), str(project.id), channel.value]


def _decode(cursor, scope):
    if not cursor:
        return {"after": None, "offset": 0, "pending": False, "digest": None, "issued": int(time())}
    try:
        if len(cursor) > 4096:
            raise ValueError()
        state = loads(Encryptor.decrypt(cursor, Env.JWT_SECRET_KEY))
        if state["scope"] != scope or not 0 <= time() - state["issued"] <= CURSOR_TTL:
            raise ValueError()
        stamp, kind, identifier = state["after"]
        if kind not in {"card", "wiki"} or type(identifier) is not int or identifier <= 0:
            raise ValueError()
        if type(state["offset"]) is not int or not 0 <= state["offset"] <= MAX_CONTENT_CHARS:
            raise ValueError()
        if type(state["pending"]) is not bool:
            raise ValueError()
        if state["pending"] and (not isinstance(state["digest"], str) or len(state["digest"]) != 64):
            raise ValueError()
        state["after"] = (datetime.fromisoformat(stamp), kind, identifier)
        return state
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise ApiException.BadRequest_400() from None


def _source_query(project, context, actor, kind):
    source, body = (Card, Card.description) if kind == "card" else (ProjectWiki, ProjectWiki.content)
    if kind == "card":
        audience = and_(card_visibility_scope(context), or_(Card.source_type.is_(None), Card.source_uid.is_(None)))
    else:
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
        audience = or_(ProjectWiki.is_public.is_(True), actor.is_admin, project.owner_id == actor.id, assigned)
    return source, cast(body, Text), and_(source.project_id == project.id, source.deleted_at.is_(None), audience)


def _candidates(project, context, actor, after, inclusive=False, count=SOURCE_BATCH, *, suffix=""):
    rows = []
    with DbSession.use(readonly=False) as db:
        for kind in ("card", "wiki"):
            source, body, audience = _source_query(project, context, actor, kind)
            query = select(
                source.id, source.created_at, source.updated_at, func.substr(source.title, 1, 240), func.length(body)
            ).where(audience)
            if after:
                stamp, last_kind, last_id = after
                query = query.where(
                    or_(
                        source.created_at < stamp,
                        and_(
                            source.created_at == stamp,
                            or_(
                                kind + suffix < last_kind,
                                and_(kind + suffix == last_kind, source.id <= last_id if inclusive else source.id < last_id),
                            ),
                        ),
                    )
                )
            rows.extend(
                (kind, *row)
                for row in db.exec(query.order_by(source.created_at.desc(), source.id.desc()).limit(count)).all()
            )
    return sorted(rows, key=lambda row: (row[2], row[0], int(row[1])), reverse=True)[:count]


def _read_source(project, context, actor, kind, identifier, *, remaining_chars):
    source, body, audience = _source_query(project, context, actor, kind)
    with DbSession.use(readonly=False) as db:
        # Reapply source ACL and storage length inside SQL before transferring a body.
        return db.exec(
            select(
                source.id,
                source.created_at,
                source.updated_at,
                func.substr(source.title, 1, 240),
                case((func.length(body) <= min(MAX_SOURCE_STORAGE_CHARS, remaining_chars), body), else_=None),
                func.length(body),
            )
            .where(audience, source.id == identifier)
            .limit(1)
        ).first()


def _source(project, kind, row):
    uid, board = row[0].to_short_code(), f"/board/{project.get_uid()}"
    return {
        "project_uid": project.get_uid(),
        f"{kind}_uid": uid,
        "title": row[3],
        "board_url": board,
        "url": f"{board}/{uid}" if kind == "card" else f"{board}/wiki/{uid}",
    }


def _item(project, kind, row, url, label):
    return {
        "id": f"{kind}_link:{row[0].to_short_code()}:{sha256(url.encode()).hexdigest()}",
        "name": label[:240],
        "url": url,
        "media_type": "text/uri-list",
        "kind": "link",
        "folder_id": "links",
        "created_at": row[1].isoformat(),
        "updated_at": row[2].isoformat(),
        "source": _source(project, kind, row),
    }


def read_board_links(service, project, context, actor, channel, *, limit, cursor=None):
    from .BoardCloud import _resolve_cloud_context

    scope = _scope(actor, project, channel)
    state = _decode(cursor, scope)
    after, offset, pending, digest = state["after"], state["offset"], state["pending"], state["digest"]
    items, limitations, emitted = [], [], {}
    response_bytes, read_chars, examined = 2048 + MAX_SCANNED_SOURCES * 1536, 0, 0
    exhausted = False
    while len(items) < limit and examined < MAX_SCANNED_SOURCES:
        rows = _candidates(project, context, actor, after, pending)
        if not rows:
            exhausted = True
            break
        stopped = False
        for kind, identifier, created, updated, title, size in rows:
            if read_chars + min(size or 0, MAX_SOURCE_STORAGE_CHARS) > MAX_REQUEST_STORAGE_CHARS:
                stopped = True
                break
            project, context, actor = _resolve_cloud_context(service, actor, project.get_uid(), channel)
            row = _read_source(
                project, context, actor, kind, identifier,
                remaining_chars=MAX_REQUEST_STORAGE_CHARS - read_chars,
            )
            examined += 1
            key = (created, kind, int(identifier))
            if row is None:
                after, offset, pending, digest = key, 0, False, None
                continue
            raw = row[4]
            # A concurrent edit may grow the body after candidate metadata was read.
            # Defer it before transfer/parsing and retain the previous cursor.
            if raw is None and (row[5] or 0) <= MAX_SOURCE_STORAGE_CHARS:
                stopped = True
                break
            emitted[(kind, identifier)] = row[2]
            resumed = offset if pending and key == after else 0
            if pending and key == after and sha256((raw or "").encode()).hexdigest() != digest:
                raise ApiException.BadRequest_400()
            after = key
            if raw is None:
                limitations.append({"reason": "source_too_large", "source": _source(project, kind, row)})
                offset, pending, digest = 0, False, None
                continue
            read_chars += len(raw)
            value = loads(raw)
            if isinstance(value, str):
                value = loads(value)
            markdown = value.get("content", "") if isinstance(value, dict) else ""
            if len(markdown) > MAX_CONTENT_CHARS:
                limitations.append({"reason": "source_too_large", "source": _source(project, kind, row)})
                offset, pending, digest = 0, False, None
                continue
            links = stored_links(markdown)
            for index in range(resumed, len(links)):
                item = _item(project, kind, row, *links[index])
                item_bytes = len(dumps(item, ensure_ascii=False).encode())
                if len(items) == limit or response_bytes + item_bytes > MAX_PAGE_BYTES:
                    offset, pending, digest, stopped = index, True, sha256(raw.encode()).hexdigest(), True
                    break
                items.append(item)
                response_bytes += item_bytes
            else:
                offset, pending, digest = 0, False, None
            if stopped or len(items) == limit or examined == MAX_SCANNED_SOURCES:
                stopped = True
                break
        if stopped:
            break
    # Current grants can change while parsing. Recheck every emitted source without
    # transferring its body again; changed bodies require a fresh first page.
    project, context, actor = _resolve_cloud_context(service, actor, project.get_uid(), channel)
    denied = set()
    with DbSession.use(readonly=False) as db:
        for (kind, identifier), version in emitted.items():
            source, _, audience = _source_query(project, context, actor, kind)
            current = db.exec(select(source.updated_at).where(audience, source.id == identifier).limit(1)).first()
            if current is None:
                denied.add((kind, identifier.to_short_code()))
            elif current[0] != version:
                raise ApiException.BadRequest_400()

    def readable(entry):
        provenance = entry["source"]
        return not any(provenance.get(f"{kind}_uid") == uid for kind, uid in denied)

    items = [entry for entry in items if readable(entry)]
    limitations = [entry for entry in limitations if readable(entry)]
    # Metadata-only lookahead; never assert completion merely because a budget ended.
    has_more = pending or bool(_candidates(project, context, actor, after, count=1))
    if exhausted:
        has_more = False
    next_cursor = (
        Encryptor.encrypt(
            dumps(
                {
                    "scope": scope,
                    "after": [after[0].isoformat(), after[1], after[2]],
                    "offset": offset,
                    "pending": pending,
                    "digest": digest,
                    "issued": state["issued"],
                }
            ),
            Env.JWT_SECRET_KEY,
        )
        if has_more
        else None
    )
    return {
        "items": items,
        "folders": [{"id": "links", "name": "Links"}],
        "total": None,
        "total_is_exact": False,
        "recent_limit": 20,
        "mime_type_source": "stored_hyperlink",
        "has_more": has_more,
        "next_cursor": next_cursor,
        "capabilities": {"hyperlinks": True, "upload": False, "replace": False, "all_recent": "attachments_and_links"},
        "limitations": limitations,
        "limitation_count": len(limitations),
        "scan_limits": {
            "source_storage_chars": MAX_SOURCE_STORAGE_CHARS,
            "source_content_chars": MAX_CONTENT_CHARS,
            "response_bytes": MAX_PAGE_BYTES,
            "sources_per_page": MAX_SCANNED_SOURCES,
        },
    }


def _mixed_decode(cursor, scope):
    if not cursor:
        return {"after": None, "offset": 0, "pending": False, "digest": None, "remaining": 20, "issued": int(time())}
    try:
        if len(cursor) > 4096:
            raise ValueError()
        state = loads(Encryptor.decrypt(cursor, Env.JWT_SECRET_KEY))
        if state["scope"] != scope or not 0 <= time() - state["issued"] <= CURSOR_TTL:
            raise ValueError()
        stamp, kind, identifier = state["after"]
        if kind not in {"card_attachment", "wiki_attachment", "project_attachment", "card_link", "wiki_link"}:
            raise ValueError()
        if type(identifier) is not int or identifier <= 0:
            raise ValueError()
        if type(state["offset"]) is not int or not 0 <= state["offset"] <= MAX_CONTENT_CHARS:
            raise ValueError()
        if type(state["remaining"]) is not int or not 0 <= state["remaining"] <= 20:
            raise ValueError()
        if type(state["pending"]) is not bool or (state["pending"] and not kind.endswith("_link")):
            raise ValueError()
        if state["pending"] and (not isinstance(state["digest"], str) or len(state["digest"]) != 64):
            raise ValueError()
        state["after"] = (datetime.fromisoformat(stamp), kind, identifier)
        return state
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise ApiException.BadRequest_400() from None


def _mixed_candidates(project, context, actor, after, pending, count):
    from .BoardCloud import _candidates as attachment_candidates

    rows = [
        ((attachment.created_at, kind + "_attachment", int(attachment.id)), (attachment, source, kind))
        for attachment, source, kind in attachment_candidates(project, context, actor, after, count, suffix="_attachment")
    ]
    rows.extend(
        ((row[2], row[0] + "_link", int(row[1])), row)
        for row in _candidates(project, context, actor, after, pending, count=count, suffix="_link")
    )
    return sorted(rows, key=lambda row: row[0], reverse=True)[:count]


def read_board_mixed(service, project, context, actor, channel, *, view, limit, cursor=None):
    """Merge immutable native source creation keys, resuming links within a source.

    A source's deduplicated links retain Markdown occurrence order. The complete
    order is (creation time, source kind, native id, occurrence), newest first
    for the first three components. Cursors contain positions and hashes only.
    """
    from .BoardCloud import _SCAN_LIMIT, _read_attachment_item, _resolve_cloud_context

    scope = ["board-cloud-mixed:1", str(actor.id), str(project.id), channel.value, view]
    state = _mixed_decode(cursor, scope)
    after, offset, pending, digest = state["after"], state["offset"], state["pending"], state["digest"]
    target = min(limit, state["remaining"]) if view == "recent" else limit
    items, limitations, attachments, sources = [], [], [], {}
    response_bytes, read_chars, examined, scanned_sources = 2048 + MAX_SCANNED_SOURCES * 1536, 0, 0, 0
    stopped = False
    while len(items) < target and examined < _SCAN_LIMIT and not stopped:
        candidates = _mixed_candidates(project, context, actor, after, pending, SOURCE_BATCH)
        if not candidates:
            offset, pending, digest = 0, False, None
            break
        for key, candidate in candidates:
            if len(items) >= target or examined >= _SCAN_LIMIT:
                stopped = True
                break
            project, context, actor = _resolve_cloud_context(service, actor, project.get_uid(), channel)
            if key[1].endswith("_attachment"):
                attachment, source, kind = candidate
                item = _read_attachment_item(service, actor, project, attachment, source, kind, channel)
                if item is not None:
                    size = len(dumps(item, ensure_ascii=False).encode())
                    if response_bytes + size > MAX_PAGE_BYTES:
                        if not items:
                            raise ApiException.BadRequest_400()
                        stopped = True
                        break
                    items.append(item)
                    attachments.append((attachment, source, kind))
                    response_bytes += size
                examined += 1
                after, offset, pending, digest = key, 0, False, None
                continue
            kind, identifier, created, updated, title, size = candidate
            if scanned_sources >= MAX_SCANNED_SOURCES:
                stopped = True
                break
            if read_chars + min(size or 0, MAX_SOURCE_STORAGE_CHARS) > MAX_REQUEST_STORAGE_CHARS:
                stopped = True
                break
            row = _read_source(project, context, actor, kind, identifier,
                               remaining_chars=MAX_REQUEST_STORAGE_CHARS - read_chars)
            examined += 1
            scanned_sources += 1
            if row is None:
                after, offset, pending, digest = key, 0, False, None
                continue
            raw = row[4]
            if raw is None and (row[5] or 0) <= MAX_SOURCE_STORAGE_CHARS:
                stopped = True
                break
            resumed = offset if pending and key == after else 0
            if pending and key == after and sha256((raw or "").encode()).hexdigest() != digest:
                raise ApiException.BadRequest_400()
            after = key
            if raw is None:
                sources[(kind, identifier)] = row[2]
                limitations.append({"reason": "source_too_large", "source": _source(project, kind, row)})
                offset, pending, digest = 0, False, None
                continue
            read_chars += len(raw)
            value = loads(raw)
            if isinstance(value, str):
                value = loads(value)
            markdown = value.get("content", "") if isinstance(value, dict) else ""
            if len(markdown) > MAX_CONTENT_CHARS:
                sources[(kind, identifier)] = row[2]
                limitations.append({"reason": "source_too_large", "source": _source(project, kind, row)})
                offset, pending, digest = 0, False, None
                continue
            links = stored_links(markdown)
            for index in range(resumed, len(links)):
                item = _item(project, kind, row, *links[index])
                size = len(dumps(item, ensure_ascii=False).encode())
                if len(items) == target or response_bytes + size > MAX_PAGE_BYTES:
                    offset, pending, digest = index, True, sha256(raw.encode()).hexdigest()
                    stopped = True
                    break
                sources[(kind, identifier)] = row[2]
                items.append(item)
                response_bytes += size
            else:
                offset, pending, digest = 0, False, None
            if stopped:
                break
    # Resolve current board/channel grants again and revalidate every earlier
    # emitted source and attachment after the final traversal lookup.
    project, context, actor = _resolve_cloud_context(service, actor, project.get_uid(), channel)
    current_attachments = {}
    for attachment, source, kind in attachments:
        item = _read_attachment_item(service, actor, project, attachment, source, kind, channel)
        if item is not None:
            current_attachments[item["id"]] = item
    project, context, actor = _resolve_cloud_context(service, actor, project.get_uid(), channel)
    denied = set()
    source_versions = {(kind, source.id): None for _, source, kind in attachments if kind != "project"}
    source_versions.update(sources)
    with DbSession.use(readonly=False) as db:
        for (kind, identifier), version in source_versions.items():
            source, _, audience = _source_query(project, context, actor, kind)
            current = db.exec(select(source.updated_at).where(audience, source.id == identifier).limit(1)).first()
            if current is None:
                denied.add((kind, identifier.to_short_code()))
            elif version is not None and current[0] != version:
                raise ApiException.BadRequest_400()

    def readable(entry):
        return not any(entry["source"].get(f"{kind}_uid") == uid for kind, uid in denied)

    items = [current_attachments[entry["id"]] if entry["kind"] != "link" else entry
             for entry in items if readable(entry) and (entry["kind"] == "link" or entry["id"] in current_attachments)]
    limitations = [entry for entry in limitations if readable(entry)]
    remaining = state["remaining"] - len(items) if view == "recent" else state["remaining"]
    has_more = (view != "recent" or remaining > 0) and (
        pending or bool(_mixed_candidates(project, context, actor, after, False, 1))
    )
    next_cursor = None
    if has_more:
        next_cursor = Encryptor.encrypt(dumps({
            "scope": scope, "after": [after[0].isoformat(), after[1], after[2]],
            "offset": offset, "pending": pending, "digest": digest,
            "remaining": remaining, "issued": state["issued"],
        }), Env.JWT_SECRET_KEY)
    response = {
        "items": items,
        "folders": [{"id": name.lower(), "name": name} for name in ("Inbox", "Documents", "Media", "Links")],
        "total": None, "total_is_exact": False, "recent_limit": 20,
        "mime_type_source": "filename_extension", "has_more": has_more, "next_cursor": next_cursor,
        "capabilities": {"hyperlinks": True, "upload": service.project_attachment.can_upload(actor, project),
                         "replace": True, "links_view": True, "all_recent": "attachments_and_links"},
        "limitations": limitations, "limitation_count": len(limitations),
        "scan_limits": {"source_storage_chars": MAX_SOURCE_STORAGE_CHARS, "source_content_chars": MAX_CONTENT_CHARS,
                        "response_bytes": MAX_PAGE_BYTES, "sources_per_page": MAX_SCANNED_SOURCES,
                        "candidates_per_page": _SCAN_LIMIT},
    }

    if len(dumps(response, ensure_ascii=False).encode()) > MAX_PAGE_BYTES:
        raise ApiException.BadRequest_400()
    return response
