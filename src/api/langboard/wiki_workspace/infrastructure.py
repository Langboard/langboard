"""Native authorized wiki storage, bounded searches and existing activity snapshots."""

from json import dumps, loads
from typing import Any
from fastmcp.exceptions import AuthorizationError
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.exceptions.WikiContentConflict import WikiContentConflict
from langboard_shared.core.utils.Encryptor import Encryptor
from langboard_shared.domain.models import (
    ProjectAssignedUser,
    ProjectWiki,
    ProjectWikiActivity,
    ProjectWikiAssignedUser,
    User,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.ProjectAttachmentService import current_authority
from langboard_shared.Env import Env
from langboard_shared.helpers import InfraHelper
from sqlalchemy import JSON, cast, func, or_, select
from .domain import WikiRepository, WikiSnapshot, content_page


class NativeWikiRepository(WikiRepository):
    """Reuse native wiki permission, content and history semantics."""

    def __init__(self, user: User, service: DomainService) -> None:
        self.user = user
        self.service = service
        self._generations: dict[str, int] = {}

    def _wiki(self, project_uid: str, wiki_uid: str) -> ProjectWiki:
        project = self.service.project.get_by_id_like(project_uid)
        wiki = self.service.project_wiki.get_by_id_like(wiki_uid)
        if project is None or wiki is None or wiki.project_id != project.id:
            raise ValueError("Wiki not found in project")
        if self.service.project_wiki.convert_to_api_response(self.user, project, wiki)["forbidden"]:
            raise AuthorizationError("This wiki is private and not assigned to the current user")
        return wiki

    def snapshot(self, project_uid: str, wiki_uid: str) -> WikiSnapshot:
        """Check current per-wiki visibility before returning exact Markdown."""
        wiki = self._wiki(project_uid, wiki_uid)
        self._generations[wiki_uid] = wiki.editor_generation
        return WikiSnapshot(wiki.get_uid(), wiki.title, wiki.content.content)

    def linked_revisions(self, project_uid: str, wiki_uids: list[str]) -> dict[str, str]:
        """Batch current visible link revisions; previously returned links grant no access."""
        if not wiki_uids:
            return {}
        project = self.service.project.get_by_id_like(project_uid)
        if project is None:
            raise ValueError("Project not found")
        ids = [InfraHelper.convert_id(uid) for uid in wiki_uids]
        statement = SqlBuilder.select.columns(
            ProjectWiki.column("id"), ProjectWiki.column("title"), ProjectWiki.column("content")
        ).where(
            (ProjectWiki.column("project_id") == project.id)
            & ProjectWiki.column("deleted_at").is_(None)
            & ProjectWiki.column("id").in_(ids)
        )
        # Match visible_linked_wikis: private links require assignment or admin.
        if not self.user.is_admin:
            assigned = select(ProjectWikiAssignedUser.project_wiki_id).where(
                ProjectWikiAssignedUser.user_id == self.user.id
            )
            statement = statement.where(
                or_(ProjectWiki.column("is_public").is_(True), ProjectWiki.column("id").in_(assigned))
            )
        with DbSession.use(readonly=False) as db:
            rows = db.exec(statement).all()
        revisions = {
            row[0].to_short_code(): WikiSnapshot(row[0].to_short_code(), row[1], row[2].content).revision
            for row in rows
        }
        if set(revisions) != set(wiki_uids):
            raise AuthorizationError("Linked wiki is no longer readable; purge cached card context")
        return revisions

    def append(self, project_uid: str, wiki_uid: str, before: str, after: str) -> None:
        """Use native events/history and a locked content-only conditional save."""
        wiki = self._wiki(project_uid, wiki_uid)
        if not self.service.project_wiki.is_assigned(self.user, wiki):
            raise AuthorizationError("Current user cannot edit this private wiki")
        result = self.service.project_wiki.update(
            self.user,
            project_uid,
            wiki,
            {"content": EditorContentModel(content=after)},
            expected_content=before,
            expected_generation=self._generations.get(wiki_uid),
            advance_generation=True,
        )
        if result is None:
            raise ValueError("Wiki not found")

    def replace(self, project_uid: str, wiki_uid: str, before: str, after: str) -> None:
        """Use the same native optimistic save for exact multi-hunk edits."""

        self.append(project_uid, wiki_uid, before, after)

    def delete(self, project_uid: str, wiki_uid: str, before: str) -> None:
        """Reauthorize and reject stale reviewed content before native soft deletion."""

        wiki = self._wiki(project_uid, wiki_uid)
        if wiki.content.content != before:
            raise WikiContentConflict("Wiki changed after review; read it again before deleting")
        if not self.service.project_wiki.delete(self.user, project_uid, wiki):
            raise ValueError("Wiki not found")

    def list_wikis(self, project_uid: str, query: str, after_uid: str | None, limit: int) -> dict[str, Any]:
        """Filter permissions in SQL before literal search, paging or snippets."""
        if not 1 <= limit <= 50 or len(query) > 1000:
            raise ValueError("limit must be 1..50 and query at most 1000 characters")
        project = self.service.project.get_by_id_like(project_uid)
        if project is None:
            raise ValueError("Project not found")
        columns = [ProjectWiki.column("id"), ProjectWiki.column("title"), ProjectWiki.column("is_public")]
        if query:
            columns.append(ProjectWiki.column("content"))
        statement = self._readable_list_statement(project, self.user, columns)
        if after_uid:
            statement = statement.where(ProjectWiki.column("id") > InfraHelper.convert_id(after_uid))
        if query:
            # Native ModelColumnType stores a JSON string containing the model JSON.
            # Decode that wrapper before searching; do not search escaped serialization.
            content_column = ProjectWiki.column("content")
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                content_text = func.json_extract(func.json_extract(content_column, "$"), "$.content")
            else:
                content_text = cast(cast(content_column, JSON)[()].as_string(), JSON)["content"].as_string()
            statement = statement.where(
                or_(
                    ProjectWiki.column("title").contains(query, autoescape=True),
                    content_text.contains(query, autoescape=True),
                )
            )
        # Body searches and snippets must observe the committed secrecy fence.
        # Metadata-only enumeration may continue to use the readonly engine.
        with DbSession.use(readonly=not bool(query)) as db:
            rows = db.exec(statement.order_by(ProjectWiki.column("id").asc()).limit(limit + 1)).all()
        items = []
        for row in rows[:limit]:
            item = {"wiki_uid": row[0].to_short_code(), "title": row[1], "is_public": row[2]}
            if query:
                text = row[3].content
                index = text.find(query)
                start = max(0, index - 100)
                item.update(
                    {"snippet": text[start : start + 300], "snippet_offset": start, "content_match": index >= 0}
                )
            items.append(item)
        return {
            "items": items,
            "next_cursor": items[-1]["wiki_uid"] if len(rows) > limit else None,
            "query": query,
            "search_mode": "literal",
            "limit": limit,
        }

    @staticmethod
    def _readable_list_statement(project, user, columns):
        statement = SqlBuilder.select.columns(*columns).where(
            (ProjectWiki.column("project_id") == project.id) & ProjectWiki.column("deleted_at").is_(None)
        )
        if not user.is_admin and project.owner_id != user.id:
            assigned = (
                select(ProjectWikiAssignedUser.project_wiki_id)
                .join(ProjectAssignedUser, ProjectAssignedUser.id == ProjectWikiAssignedUser.project_assigned_id)
                .where(
                    ProjectWikiAssignedUser.user_id == user.id,
                    ProjectAssignedUser.user_id == user.id,
                    ProjectAssignedUser.project_id == project.id,
                )
            )
            statement = statement.where(
                or_(ProjectWiki.column("is_public").is_(True), ProjectWiki.column("id").in_(assigned))
            )
        return statement

    def list_summaries(self, project_uid: str, cursor: str | None, limit: int = 25) -> dict[str, Any]:
        """Read one body-free page using current authority and an actor/project-bound cursor."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be 1..50")
        with DbSession.use(readonly=False) as db:
            authority = current_authority(db, self.user, project_uid)
            if authority is None:
                raise AuthorizationError("Current user cannot read this project")
            user, project, _ = authority
            scope = ["wiki-summaries:1", user.get_uid(), project.get_uid()]
            after = None
            if cursor is not None:
                try:
                    if not cursor or len(cursor) > 2048:
                        raise ValueError("Invalid cursor length")
                    payload = loads(Encryptor.decrypt(cursor, Env.JWT_SECRET_KEY))
                    if payload["scope"] != scope or not isinstance(payload["after"], str):
                        raise ValueError("Invalid cursor scope")
                    after = InfraHelper.convert_id(payload["after"])
                    if after <= 0 or after.to_short_code() != payload["after"]:
                        raise ValueError("Invalid cursor position")
                except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
                    raise ValueError("Invalid wiki summary cursor") from None
            statement = self._readable_list_statement(
                project, user, [ProjectWiki.column("id"), ProjectWiki.column("title"), ProjectWiki.column("is_public")]
            )
            if after is not None:
                statement = statement.where(ProjectWiki.column("id") > after)
            rows = db.exec(statement.order_by(ProjectWiki.column("id").asc()).limit(limit + 1)).all()
        items = [{"wiki_uid": row[0].to_short_code(), "title": row[1], "is_public": row[2]} for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = Encryptor.encrypt(dumps({"scope": scope, "after": items[-1]["wiki_uid"]}), Env.JWT_SECRET_KEY)
        return {"items": items, "next_cursor": next_cursor, "limit": limit}

    def revisions(self, project_uid: str, wiki_uid: str, before_uid: str | None, limit: int) -> dict[str, Any]:
        """List existing audit revisions without emitting unbounded body snapshots."""
        wiki = self._wiki(project_uid, wiki_uid)
        if not 1 <= limit <= 50:
            raise ValueError("limit must be 1..50")
        changes = ProjectWikiActivity.column("activity_history")["changes"]
        statement = SqlBuilder.select.columns(
            ProjectWikiActivity.column("id"),
            ProjectWikiActivity.column("created_at"),
            ProjectWikiActivity.column("activity_type"),
            changes["before"]["content"].as_string().is_not(None),
            changes["after"]["content"].as_string().is_not(None),
        ).where(
            (ProjectWikiActivity.column("project_id") == wiki.project_id)
            & (ProjectWikiActivity.column("project_wiki_id") == wiki.id)
        )
        if before_uid:
            statement = statement.where(ProjectWikiActivity.column("id") < InfraHelper.convert_id(before_uid))
        with DbSession.use(readonly=True) as db:
            rows = db.exec(statement.order_by(ProjectWikiActivity.column("id").desc()).limit(limit + 1)).all()
        items = []
        for revision_id, created_at, activity_type, has_before, has_after in rows[:limit]:
            items.append(
                {
                    "revision_uid": revision_id.to_short_code(),
                    "created_at": created_at,
                    "activity_type": activity_type.value,
                    "content_sides": [
                        side for side, present in (("before", has_before), ("after", has_after)) if present
                    ],
                }
            )
        return {
            "items": items,
            "next_cursor": items[-1]["revision_uid"] if len(rows) > limit else None,
            "history_source": "native_activity_log",
            "history_may_be_pending": True,
        }

    def revision_page(
        self, project_uid: str, wiki_uid: str, revision_uid: str, side: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        """Reauthorize the current wiki before reading an old content snapshot."""
        wiki = self._wiki(project_uid, wiki_uid)
        if side not in ("before", "after"):
            raise ValueError("side must be before or after")
        # Historical bodies must observe redaction committed on primary, just like the current body.
        with DbSession.use(readonly=False) as db:
            activity = db.exec(
                SqlBuilder.select.table(ProjectWikiActivity)
                .where(
                    (ProjectWikiActivity.column("id") == InfraHelper.convert_id(revision_uid))
                    & (ProjectWikiActivity.column("project_id") == wiki.project_id)
                    & (ProjectWikiActivity.column("project_wiki_id") == wiki.id)
                )
                .limit(1)
            ).first()
        if activity is None:
            raise ValueError("Revision not found in this wiki")
        content = activity.activity_history.get("changes", {}).get(side, {}).get("content")
        if not isinstance(content, dict) or not isinstance(content.get("content"), str):
            raise ValueError("This activity has no stored content snapshot; do not infer a historical body")
        snapshot = WikiSnapshot(wiki_uid, wiki.title, content["content"])
        return {
            **content_page(snapshot, f"{project_uid}/{wiki_uid}/{revision_uid}/{side}", cursor, limit),
            "revision_uid": revision_uid,
            "side": side,
            "title_source": "current_wiki",
        }
