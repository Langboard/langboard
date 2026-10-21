"""Authorize, snapshot and persist comment compaction without altering sources."""

import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy import Text, and_, cast, func, literal, or_, select, tuple_, union_all
from ....ai.comment_compaction import CommentCompactionSnapshot
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.security.CollaborationChannel import CollaborationChannel
from ....core.types import SafeDateTime, SnowflakeID
from ....Env import Env
from ....helpers import InfraHelper
from ....infrastructure.repositories.factory.CardCommentCompactionRepository import (
    POLICY_VERSION,
    provider_revision,
    request_key,
)
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
            actor = db.exec(SqlBuilder.select.table(User).where(User.id == user.id)
                            .execution_options(populate_existing=True)).first()
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project.id, ProjectRole.user_id == user.id,
            ).execution_options(populate_existing=True)).first()
        needed = [ProjectRoleAction.Read, ProjectRoleAction.CardUpdate] if write else [ProjectRoleAction.Read]
        if not actor or not actor.activated_at or actor.deleted_at is not None:
            raise CommentCompactionError("not_found", 404)
        if not actor.is_admin and (role is None or not role.is_granted(needed)):
            raise CommentCompactionError("forbidden", 403)
        can_generate = actor.is_admin or bool(role and role.is_granted([ProjectRoleAction.Read, ProjectRoleAction.CardUpdate]))
        return project, card, can_generate

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

    def _snapshot(self, project, card, *, after=None, refs=None, omit_refs=None):
        """Read identities, timestamps and payloads in one primary SQL statement."""
        events = self._events(project, card)
        query = select(events).where(events.c.active)
        if omit_refs:
            query = query.where(tuple_(events.c.kind, events.c.id).not_in([self._identity(ref) for ref in omit_refs]))
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
            project, card, _ = self._authorize(user, project, card, channel)
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
            if record.user_id == user.id:
                result["request_key"] = record.request_key
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
                    "source_revision": record.source_revision, "has_more_sources": has_more,
                    "covered_from": snapshot.sources[0].created_at.isoformat(), "covered_through": last.created_at.isoformat()}

    def get_page(self, user, project, card, *, before=None, channel=CollaborationChannel.Api):
        """Expose bounded verified blocks and the caller's in-flight request IDs."""
        with DbSession.atomic() as db:
            project, card, can_generate = self._authorize(user, project, card, channel)
            project_id, card_id = project.id, card.id
            events = self._events(project, card)
            count, characters = db.exec(select(func.count(), func.coalesce(func.sum(func.length(events.c.payload)), 0))
                .select_from(events).where(events.c.active)).first()
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            active = self._valid_blocks(user, project, card, channel=channel)
            covered = {ref for block in active for ref in block["evidence_refs"]}
            uncovered = select(func.count(), func.coalesce(func.sum(func.length(events.c.payload)), 0)).select_from(events).where(events.c.active)
            if covered:
                uncovered = uncovered.where(tuple_(events.c.kind, events.c.id).not_in([self._identity(ref) for ref in covered]))
            remaining_count, remaining_characters = db.exec(uncovered).first()
            needs_compaction = remaining_count >= 20 or remaining_characters >= 12_000
            next_key = None
            if needs_compaction and can_generate and binding:
                try:
                    snapshot, _ = self._snapshot(project, card, omit_refs=covered)
                    next_key = request_key(card, user, binding, snapshot)
                except CommentCompactionError:
                    pass
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
                "next_cursor": page[-1].get_uid() if len(records) > 5 else None,
                "eligibility": {"can_generate": can_generate, "provider_configured": binding is not None,
                                "event_count": count, "source_characters": characters,
                                "uncompacted_event_count": remaining_count, "uncompacted_characters": remaining_characters,
                                "needs_compaction": needs_compaction, "next_request_key": next_key}}

    def _valid_blocks(self, user, project, card, *, channel):
        """Choose current disjoint ranges; superseded summaries never hide sources."""
        with DbSession.use(readonly=False) as db:
            rows = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
                CardCommentCompaction.card_id == card.id, CardCommentCompaction.status == "completed",
            ).order_by(CardCommentCompaction.created_at.desc(), CardCommentCompaction.id.desc())).all()
        blocks, covered = [], set()
        for record in rows:
            block = self.get(user, project, card, record.id, channel=channel)
            if block["status"] != "completed" or covered.intersection(block["evidence_refs"]):
                continue
            covered.update(block["evidence_refs"])
            blocks.append(block)
        return blocks

    def get_view(self, user, project, card, *, before=None, channel=CollaborationChannel.Api):
        """One canonical page of current summaries and uncovered original events."""
        from .ActivityService import ActivityService

        with DbSession.atomic() as db:
            project, card, _ = self._authorize(user, project, card, channel)
            project_id, card_id = project.id, card.id
            overview = self.get_page(user, project, card, channel=channel)
            blocks = self._valid_blocks(user, project, card, channel=channel)
            covered = [ref for block in blocks for ref in block["evidence_refs"]]
            anchor = None
            if before:
                kind, uid = self._identity(before)
                events = self._events(project, card)
                row = db.exec(select(events.c.created_at, events.c.id, events.c.kind).where(
                    events.c.kind == kind, events.c.id == uid,
                )).first()
                if row is None:
                    raise CommentCompactionError("invalid_cursor", 400)
                anchor = (_utc(row[0]), int(row[1]), row[2])
            originals = self._get_service(ActivityService).get_card_timeline(project, card, before=before, omit_refs=covered)
            entries = []
            for record in originals["records"]:
                created_at = record["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                position = (_utc(created_at),
                            int(InfraHelper.convert_id(record["uid"])), record["kind"])
                entries.append((position, record, record["evidence_ref"]))
            for block in blocks:
                kind, source_id = self._identity(block["range_end"])
                position = (_utc(datetime.fromisoformat(block["covered_through"])), int(source_id), kind)
                if anchor is not None and position >= anchor:
                    continue
                entries.append((position, {"kind": "compaction", "uid": block["uid"],
                    "evidence_ref": f"compaction:{block['uid']}", "created_at": block["covered_through"], "data": block},
                    block["range_end"]))
            entries.sort(key=lambda entry: entry[0], reverse=True)
            page = entries[:20]
            for _, record, _ in page:
                if record["kind"] != "compaction":
                    continue
                current = self.get(user, project_id, card_id, record["uid"], channel=channel)
                if current["status"] != "completed" or current["source_revision"] != record["data"]["source_revision"]:
                    raise CommentCompactionError("source_changed")
            self._authorize(user, project_id, card_id, channel)
            has_more = len(entries) > 20 or originals["next_cursor"] is not None
            return {"records": [entry[1] for entry in page],
                    "next_cursor": page[-1][2] if has_more and page else None,
                    "eligibility": overview["eligibility"],
                    "in_flight": [record for record in overview["records"] if record["status"] in {"pending", "running", "retry_required"}]}

    def get_evidence(self, user, project, card, uid, *, before=None, channel=CollaborationChannel.Api):
        """Page original records through the existing timeline on the primary."""
        from .ActivityService import ActivityService

        with DbSession.atomic():
            current = self.get(user, project, card, uid, channel=channel)
            if current["status"] != "completed":
                raise CommentCompactionError("compaction_invalidated")
            refs = current["evidence_refs"]
            if before is not None and before not in refs:
                raise CommentCompactionError("invalid_cursor", 400)
            page = self._get_service(ActivityService).get_card_timeline(project, card, before=before, evidence_refs=refs)
            final = self.get(user, project, card, uid, channel=channel)
            if final["status"] != "completed" or final["source_revision"] != current["source_revision"]:
                raise CommentCompactionError("compaction_invalidated")
            return {**page, "compaction_uid": current["uid"], "source_revision": current["source_revision"]}

    def cancel(self, user, project, card, uid, *, channel=CollaborationChannel.Api):
        with DbSession.atomic() as db:
            project, card, _ = self._authorize(user, project, card, channel, write=True)
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

    async def generate(self, user, project, card, *, after=None, retry=False, expected_request_key=None, channel=CollaborationChannel.Api):
        if DbSession.has_active_transaction():
            raise CommentCompactionError("transaction_not_supported")
        project_id, card_id = InfraHelper.convert_id(project), InfraHelper.convert_id(card)
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(Card).where(Card.id == InfraHelper.convert_id(card)).with_for_update()).first()
            project, card, _ = self._authorize(user, project, current, channel, write=True)
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            if binding is None:
                raise CommentCompactionError("model_unconfigured")
            record = None
            if expected_request_key:
                record = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
                    CardCommentCompaction.request_key == expected_request_key,
                    CardCommentCompaction.card_id == card.id, CardCommentCompaction.user_id == user.id,
                )).first()
            if record:
                snapshot, has_more = self._snapshot(project, card, refs=[item["evidence_ref"] for item in record.source_manifest])
                if snapshot.source_revision() != record.source_revision:
                    raise CommentCompactionError("source_changed")
                if binding.id != record.internal_bot_id or provider_revision(binding) != record.provider_revision:
                    raise CommentCompactionError("provider_changed")
            else:
                covered = set()
                if expected_request_key:
                    covered = {ref for block in self._valid_blocks(user, project, card, channel=channel) for ref in block["evidence_refs"]}
                snapshot, has_more = self._snapshot(project, card, after=after, omit_refs=covered)
                if expected_request_key and request_key(card, user, binding, snapshot) != expected_request_key:
                    raise CommentCompactionError("source_changed")
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
                        fresh_project, fresh_card, _ = self._authorize(user, project_id, card_id, channel, write=True)
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
