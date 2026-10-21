"""Authorize, snapshot and persist comment compaction without altering sources."""

import asyncio
import json
from datetime import timezone
from sqlalchemy import Text, and_, cast, literal, or_, select, tuple_, union_all
from ....ai.comment_compaction import CommentCompactionSnapshot
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.security.CollaborationChannel import CollaborationChannel
from ....core.types import SafeDateTime, SnowflakeID
from ....Env import Env
from ....helpers import InfraHelper
from ....infrastructure.repositories.factory.CardCommentCompactionRepository import POLICY_VERSION, provider_revision
from ...models import Card, CardComment, CardCommentCompaction, InternalBot, ProjectActivity, ProjectRole, User
from ...models.ProjectActivity import ProjectActivityType
from ...models.ProjectRole import ProjectRoleAction
from .CardService import CardService
from .InternalBotService import InternalBotService


class CommentCompactionError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def request_compaction_draft(binding: InternalBot, snapshot: CommentCompactionSnapshot):
    """Only an authenticated internal Graph request receives the in-memory sources."""
    from httpx import AsyncClient
    from ....core.security.GraphServiceAuth import graph_service_headers

    settings = json.loads(binding.value)
    if not isinstance(settings, dict) or not settings.get("agent_llm"):
        raise ValueError("Unconfigured compaction provider")
    async with AsyncClient(timeout=Env.AI_REQUEST_TIMEOUT + 5, follow_redirects=False) as client:
        async with client.stream("POST", f"{Env.DEFAULT_GRAPH_URL.rstrip('/')}/api/v1/graph/comments/compaction-draft",
                                 headers=graph_service_headers(), json={"model_settings": settings,
                                 "sources": snapshot.model_dump(mode="json")["sources"]}) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16_384):
                content.extend(chunk)
                if len(content) > 64_000:
                    raise CommentCompactionError("invalid_result")
    try:
        result = json.loads(content)
        if not isinstance(result, dict) or result.get("generated") is not True or result.get("status") != "draft":
            raise ValueError("Compaction generation failed")
        if result.get("source_revision") != snapshot.source_revision():
            raise ValueError("Compaction snapshot mismatch")
        return snapshot.validate_draft_json(json.dumps(result.get("draft")))
    except (ValueError, TypeError):
        raise CommentCompactionError("invalid_result") from None


class CardCommentCompactionService(BaseDomainService):
    @staticmethod
    def name() -> str:
        return "card_comment_compaction"

    def _authorize(self, user, project, card, channel, *, write=False):
        if not isinstance(user, User):
            raise CommentCompactionError("not_found", 404)
        resolved = self._get_service(CardService).resolve_readable_card(project, card, user, channel)
        if resolved is None:
            raise CommentCompactionError("not_found", 404)
        project, card, _ = resolved
        with DbSession.use(readonly=False) as db:
            actor = db.exec(SqlBuilder.select.table(User).where(User.id == user.id)).first()
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project.id, ProjectRole.user_id == user.id,
            )).first()
        needed = [ProjectRoleAction.Read, ProjectRoleAction.CardUpdate] if write else [ProjectRoleAction.Read]
        if not actor or not actor.activated_at or actor.deleted_at is not None:
            raise CommentCompactionError("not_found", 404)
        if not actor.is_admin and (role is None or not role.is_granted(needed)):
            raise CommentCompactionError("forbidden", 403)
        return project, card

    @staticmethod
    def _events(project, card):
        comments = select(literal("comment").label("kind"), CardComment.id.label("id"),
            CardComment.created_at.label("created_at"), CardComment.updated_at.label("updated_at"),
            cast(CardComment.content, Text).label("payload"), CardComment.user_id.label("user_id"),
            CardComment.bot_id.label("bot_id"), CardComment.deleted_at.is_(None).label("active"),
        ).where(CardComment.card_id == card.id)
        moves = select(literal("activity").label("kind"), ProjectActivity.id.label("id"),
            ProjectActivity.created_at, ProjectActivity.updated_at, cast(ProjectActivity.activity_history, Text),
            ProjectActivity.user_id, ProjectActivity.bot_id, literal(True),
        ).where(ProjectActivity.project_id == project.id, ProjectActivity.card_id == card.id,
                ProjectActivity.activity_type == ProjectActivityType.CardMoved)
        return union_all(comments, moves).subquery()

    def _snapshot(self, project, card, *, after=None, refs=None):
        """Read identities, timestamps and payloads in one primary SQL statement."""
        events = self._events(project, card)
        query = select(events).where(events.c.active)
        with DbSession.use(readonly=False) as db:
            if refs is not None:
                identities = [self._identity(ref) for ref in refs]
                query = query.where(tuple_(events.c.kind, events.c.id).in_(identities))
            elif after:
                kind, uid = self._identity(after)
                anchor = db.exec(select(events.c.created_at, events.c.id, events.c.kind).where(
                    events.c.kind == kind, events.c.id == uid,
                )).first()
                if anchor is None:
                    raise CommentCompactionError("invalid_cursor", 400)
                query = query.where(or_(events.c.created_at > anchor[0],
                    and_(events.c.created_at == anchor[0], events.c.id > anchor[1]),
                    and_(events.c.created_at == anchor[0], events.c.id == anchor[1], events.c.kind > anchor[2])))
            rows = db.exec(query.order_by(events.c.created_at, events.c.id, events.c.kind).limit(101)).all()
        if refs is not None and len(rows) != len(refs):
            raise CommentCompactionError("source_changed")
        if not rows:
            raise CommentCompactionError("no_sources")
        sources = []
        for kind, uid, created, updated, payload, user_id, bot_id, _ in rows[:100]:
            body = json.loads(payload)
            author = f"user:{SnowflakeID(user_id).to_short_code()}" if user_id else f"bot:{SnowflakeID(bot_id).to_short_code()}" if bot_id else None
            if kind == "activity":
                body = {"from_column": body.get("from_column"), "to_column": body.get("column")}
            text = json.dumps({"author_ref": author, "content": body}, ensure_ascii=False, sort_keys=True)
            sources.append({"evidence_ref": f"{kind}:{SnowflakeID(uid).to_short_code()}",
                            "created_at": _utc(created), "updated_at": _utc(updated), "text": text})
        try:
            return CommentCompactionSnapshot(sources=sources), len(rows) > 100
        except ValueError:
            raise CommentCompactionError("range_too_large", 413) from None

    @staticmethod
    def _identity(ref):
        kind, separator, uid = ref.partition(":")
        if not separator or kind not in {"comment", "activity"} or not uid or len(uid) > 32:
            raise CommentCompactionError("invalid_cursor", 400)
        try:
            return kind, InfraHelper.convert_id(uid)
        except (ValueError, TypeError):
            raise CommentCompactionError("invalid_cursor", 400) from None

    def get(self, user, project, card, uid, *, channel=CollaborationChannel.Api):
        with DbSession.atomic() as db:
            project, card = self._authorize(user, project, card, channel)
            try:
                record_id = InfraHelper.convert_id(uid)
            except (ValueError, TypeError):
                raise CommentCompactionError("not_found", 404) from None
            record = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
                CardCommentCompaction.id == record_id, CardCommentCompaction.card_id == card.id,
            )).first()
            if record is None or (record.user_id != user.id and record.status != "completed"):
                raise CommentCompactionError("not_found", 404)
            result = {"uid": record.get_uid(), "status": record.status}
            if record.status == "running" and (record.lease_expires_at is None or _utc(record.lease_expires_at) <= SafeDateTime.now()):
                return {**result, "status": "retry_required", "reason": "lease_expired"}
            if record.status != "completed":
                return {**result, "error_code": record.error_code}
            if record.policy_version != POLICY_VERSION:
                return {**result, "status": "invalidated", "reason": "policy_changed"}
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            if not binding or binding.id != record.internal_bot_id or provider_revision(binding) != record.provider_revision:
                return {**result, "status": "invalidated", "reason": "provider_changed"}
            refs = [source["evidence_ref"] for source in record.source_manifest]
            try:
                snapshot, _ = self._snapshot(project, card, refs=refs)
            except CommentCompactionError:
                return {**result, "status": "invalidated", "reason": "source_changed"}
            if snapshot.source_revision() != record.source_revision:
                return {**result, "status": "invalidated", "reason": "source_changed"}
            try:
                draft = snapshot.validate_draft_json(json.dumps(record.draft)).model_dump(mode="json")
            except (ValueError, TypeError):
                return {**result, "status": "invalidated", "reason": "invalid_result"}
            last = snapshot.sources[-1]
            kind, uid = self._identity(last.evidence_ref)
            events = self._events(project, card)
            has_more = db.exec(select(events.c.id).where(events.c.active, or_(
                events.c.created_at > last.created_at,
                and_(events.c.created_at == last.created_at, events.c.id > uid),
                and_(events.c.created_at == last.created_at, events.c.id == uid, events.c.kind > kind),
            )).limit(1)).first() is not None
            return {**result, "draft": draft, "evidence_refs": refs, "range_end": refs[-1],
                    "source_revision": record.source_revision, "has_more_sources": has_more}

    def get_page(self, user, project, card, *, before=None, channel=CollaborationChannel.Api):
        """Expose bounded verified blocks and the caller's in-flight request IDs."""
        with DbSession.atomic() as db:
            project, card = self._authorize(user, project, card, channel)
            project_id, card_id = project.id, card.id
            query = SqlBuilder.select.table(CardCommentCompaction).where(
                CardCommentCompaction.card_id == card_id,
                or_(CardCommentCompaction.status == "completed", CardCommentCompaction.user_id == user.id),
            )
            if before:
                try:
                    cursor_id = InfraHelper.convert_id(before)
                except (ValueError, TypeError):
                    raise CommentCompactionError("invalid_cursor", 400) from None
                cursor = db.exec(query.where(CardCommentCompaction.id == cursor_id)).first()
                if cursor is None:
                    raise CommentCompactionError("invalid_cursor", 400)
                query = query.where(or_(CardCommentCompaction.created_at < cursor.created_at,
                    and_(CardCommentCompaction.created_at == cursor.created_at, CardCommentCompaction.id < cursor.id)))
            records = db.exec(query.order_by(CardCommentCompaction.created_at.desc(), CardCommentCompaction.id.desc()).limit(6)).all()
        page = records[:5]
        return {"records": [self.get(user, project_id, card_id, record.id, channel=channel) for record in page],
                "next_cursor": page[-1].get_uid() if len(records) > 5 else None}

    def cancel(self, user, project, card, uid, *, channel=CollaborationChannel.Api):
        with DbSession.atomic() as db:
            project, card = self._authorize(user, project, card, channel, write=True)
            try:
                record_id = InfraHelper.convert_id(uid)
            except (ValueError, TypeError):
                raise CommentCompactionError("not_found", 404) from None
            record = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
                CardCommentCompaction.id == record_id, CardCommentCompaction.card_id == card.id,
                CardCommentCompaction.user_id == user.id,
            )).first()
            if record is None:
                raise CommentCompactionError("not_found", 404)
            self.repo.card_comment_compaction.cancel(record)
        return self.get(user, project, card, record_id, channel=channel)

    async def generate(self, user, project, card, *, after=None, retry=False, channel=CollaborationChannel.Api):
        if DbSession.has_active_transaction():
            raise CommentCompactionError("transaction_not_supported")
        project_id, card_id = InfraHelper.convert_id(project), InfraHelper.convert_id(card)
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(Card).where(Card.id == InfraHelper.convert_id(card)).with_for_update()).first()
            project, card = self._authorize(user, project, current, channel, write=True)
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            if binding is None:
                raise CommentCompactionError("model_unconfigured")
            snapshot, has_more = self._snapshot(project, card, after=after)
            record = self.repo.card_comment_compaction.reserve(card, user, binding, snapshot)
            claimed = self.repo.card_comment_compaction.claim(record, retry=retry,
                lease_seconds=min(3600, Env.AI_REQUEST_TIMEOUT + 30))
        if claimed is not None:
            try:
                draft = await request_compaction_draft(binding, snapshot)
            except asyncio.CancelledError:
                self.repo.card_comment_compaction.fail(record, claimed.attempt, "cancelled")
                raise
            except CommentCompactionError:
                self.repo.card_comment_compaction.fail(record, claimed.attempt, "invalid_result")
            except Exception:
                self.repo.card_comment_compaction.fail(record, claimed.attempt, "provider_error")
            else:
                try:
                    with DbSession.atomic():
                        fresh_project, fresh_card = self._authorize(user, project_id, card_id, channel, write=True)
                        fresh_binding = self._get_service(InternalBotService).get_comment_compaction_binding()
                        if not fresh_binding or fresh_binding.id != binding.id or provider_revision(fresh_binding) != provider_revision(binding):
                            raise CommentCompactionError("provider_changed")
                        fresh_snapshot, _ = self._snapshot(fresh_project, fresh_card, refs=[s.evidence_ref for s in snapshot.sources])
                        if fresh_snapshot.source_revision() != snapshot.source_revision():
                            raise CommentCompactionError("source_changed")
                        self.repo.card_comment_compaction.complete(record, claimed.attempt, fresh_snapshot, fresh_binding, draft)
                except CommentCompactionError as error:
                    code = error.code if error.code in {"source_changed", "provider_changed"} else "permission_changed"
                    self.repo.card_comment_compaction.fail(record, claimed.attempt, code)
        # Recheck visibility and source validity before returning any derived text.
        result = self.get(user, project_id, card_id, record.id, channel=channel)
        return {"has_more_sources": has_more, **result}
