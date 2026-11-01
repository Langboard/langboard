"""Authorize, snapshot and persist comment compaction without altering sources."""

import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy import ARRAY, JSON, Text, and_, case, cast, func, literal, or_, select, tuple_, union_all
from ....ai.comment_compaction import (
    CommentCompactionEvidence,
    CommentCompactionSnapshot,
    CompactionDraft,
    PriorCompactionSummary,
    compaction_manifest_revision,
    compaction_threshold_reached,
    validate_compaction_draft,
)
from ....core.db import DbSession, SqlBuilder
from ....core.db.DbEngine import DbEngine
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
from ...models import (
    Card,
    CardComment,
    CardCommentCompaction,
    InternalBot,
    ProjectActivity,
    ProjectColumn,
    ProjectRole,
    User,
    WorkflowStageDefinition,
)
from ...models.ProjectActivity import ProjectActivityType
from ...models.ProjectRole import ProjectRoleAction
from ..CompactorBoardAuthority import CompactorAuthorityError, require_compactor_card
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
                                 "sources": snapshot.model_dump(mode="json")["sources"],
                                 "prior_summaries": snapshot.model_dump(mode="json")["prior_summaries"],
                                 "snapshot_revision": snapshot.source_revision()}) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16_384):
                content.extend(chunk)
                if len(content) > 64_000:
                    raise CommentCompactionError("invalid_result")
    try:
        result = json.loads(content)
        if isinstance(result, dict) and result.get("generated") is False:
            if result.get("status") == "generation_failed":
                raise CommentCompactionError("generation_failed")
            if result.get("status") == "model_unconfigured":
                raise CommentCompactionError("provider_unconfigured")
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
            cast(CardComment.anchor, Text).label("anchor_payload"),
        ).where(CardComment.card_id == card.id)
        moves = select(literal("activity").label("kind"), ProjectActivity.id.label("id"),
            ProjectActivity.created_at, ProjectActivity.updated_at, cast(ProjectActivity.activity_history, Text),
            ProjectActivity.user_id, ProjectActivity.bot_id, literal(True), cast(literal(None), Text),
        ).where(ProjectActivity.project_id == project.id, ProjectActivity.card_id == card.id,
                ProjectActivity.activity_type == ProjectActivityType.CardMoved)
        return union_all(comments, moves).subquery()

    def _snapshot(self, project, card, *, after=None, refs=None, omit_refs=None, manifest_only=False,
                  character_budget=500_000):
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
        sources, characters = [], 0
        for kind, uid, created, updated, payload, user_id, bot_id, _, anchor_payload in rows[:100]:
            body = json.loads(payload)
            author = f"user:{SnowflakeID(user_id).to_short_code()}" if user_id else f"bot:{SnowflakeID(bot_id).to_short_code()}" if bot_id else None
            if kind == "activity":
                body = {"from_column": body.get("from_column"), "to_column": body.get("column")}
            evidence = {"author_ref": author, "content": body}
            anchor = json.loads(anchor_payload) if anchor_payload else None
            if anchor is not None:
                # Selector changes must invalidate derived summaries even when
                # comment text and timestamps are unchanged. Original comments
                # retain the authoritative non-destructive location evidence.
                evidence["anchor"] = anchor
            text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            if not manifest_only and refs is None and characters + len(text) > character_budget:
                # Keep an ordered, whole-source prefix. Remaining originals stay
                # visible and are picked up with the prior summary next time.
                if sources:
                    break
                # Keep one oversized accepted original whole for identity;
                # inference consumes bounded sequential portions.
            sources.append({"evidence_ref": f"{kind}:{SnowflakeID(uid).to_short_code()}",
                            "created_at": _utc(created), "updated_at": _utc(updated), "text": text})
            characters += len(text)
        try:
            return CommentCompactionEvidence(sources=sources), len(rows) > len(sources)
        except ValueError:
            raise CommentCompactionError("range_too_large", 413) from None

    def _verified_manifest(self, project, card, manifest):
        refs = [entry["evidence_ref"] for entry in manifest]
        if not refs or len(refs) != len(set(refs)):
            raise CommentCompactionError("source_changed")
        current, endpoints = {}, []
        for offset in range(0, len(refs), 100):
            batch, _ = self._snapshot(project, card, refs=refs[offset:offset + 100], manifest_only=True)
            current.update({item["evidence_ref"]: item for item in batch.evidence_manifest()})
            endpoints.extend([batch.sources[0], batch.sources[-1]])
        if [current.get(ref) for ref in refs] != manifest:
            raise CommentCompactionError("source_changed")
        def position(source):
            kind, uid = self._identity(source.evidence_ref)
            return source.created_at, int(uid), kind
        return min(endpoints, key=position), max(endpoints, key=position)

    def _cumulative_snapshot(self, project, card, blocks, *, after=None, required_refs=None):
        """Only prior derived drafts plus uncovered originals reach the model."""
        # Coverage belongs to every valid block, including summaries omitted
        # from this bounded request. Never resend their original text.
        covered = {ref for block in blocks for ref in block["evidence_refs"]}
        eligible = [block for block in sorted(blocks, key=lambda item: (item["covered_from"], item["uid"]))
                    if required_refs is None or set(block["evidence_refs"]).issubset(required_refs)]
        prior_refs = {ref for block in eligible for ref in block["evidence_refs"]}
        source_refs = list(required_refs - prior_refs) if required_refs is not None else None
        snapshot, more = self._snapshot(project, card, after=after, refs=source_refs, omit_refs=covered)
        # Reserve enough space for at least the first whole uncovered source.
        # Remaining sources continue in a later request alongside valid blocks.
        character_budget = 500_000
        previous = []
        for block in eligible:
            size = len(CompactionDraft.model_validate(block["draft"]).model_dump_json())
            if len(previous) == 20 or size + min(len(snapshot.sources[0].text), 100_000) > character_budget:
                if required_refs is not None:
                    raise CommentCompactionError("source_changed")
                continue
            with DbSession.use(readonly=False) as db:
                record = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
                    CardCommentCompaction.id == InfraHelper.convert_id(block["uid"]),
                    CardCommentCompaction.card_id == card.id, CardCommentCompaction.status == "completed",
                )).first()
            if record is None:
                raise CommentCompactionError("source_changed")
            previous.append({"draft": block["draft"], "evidence_manifest": record.source_manifest})
            character_budget -= size
        if sum(len(source.text) for source in snapshot.sources) > character_budget:
            snapshot, more = self._snapshot(project, card, after=after, refs=source_refs,
                omit_refs=covered, character_budget=character_budget)
        try:
            return CommentCompactionEvidence(sources=snapshot.sources, prior_summaries=previous), more
        except ValueError:
            raise CommentCompactionError("range_too_large", 413) from None

    def _fresh_cumulative_snapshot(self, project, card, snapshot):
        fresh, _ = self._snapshot(project, card, refs=[source.evidence_ref for source in snapshot.sources])
        for previous in snapshot.prior_summaries:
            self._verified_manifest(project, card, [entry.model_dump() for entry in previous.evidence_manifest])
        try:
            return CommentCompactionEvidence(sources=fresh.sources, prior_summaries=snapshot.prior_summaries)
        except ValueError:
            raise CommentCompactionError("source_changed") from None

    async def _generate_steps(self, binding, snapshot, record, attempt, revalidate):
        """Publish nothing until every original portion has been consumed."""
        previous = snapshot.prior_summaries
        pending, characters = [], sum(len(item.draft.model_dump_json()) for item in previous)

        async def generate(sources):
            revalidate()
            if not self.repo.card_comment_compaction.renew(record, attempt,
                    lease_seconds=min(3600, Env.AI_REQUEST_TIMEOUT + 30)):
                raise CommentCompactionError("cancelled")
            request = CommentCompactionSnapshot(sources=sources, prior_summaries=previous)
            result = await request_compaction_draft(binding, request)
            return request.validate_draft_json(result.model_dump_json())

        # Preserve the existing one-request path for already bounded evidence.
        if (all(len(source.text) <= 100_000 for source in snapshot.sources)
                and characters + sum(len(source.text) for source in snapshot.sources) <= 500_000):
            return await generate([source.model_dump() for source in snapshot.sources])

        for source in snapshot.sources:
            offset = 0
            while offset < len(source.text):
                if pending and (characters >= 400_000
                                or any(item["evidence_ref"] == source.evidence_ref for item in pending)):
                    result = await generate(pending)
                    # Partial drafts have no coverage manifest and stay in memory.
                    previous = [PriorCompactionSummary(draft=result)]
                    pending, characters = [], len(result.model_dump_json())
                # Reserve room for metadata/settings within Graph's aggregate
                # string cap as well as the stricter per-source string cap.
                size = min(100_000, max(1, 400_000 - characters))
                text = source.text[offset:offset + size]
                pending.append({**source.model_dump(), "text": text})
                characters += len(text)
                offset += len(text)
        return await generate(pending)

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
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            block = self._completed_block(db, project, card, record, binding, result)
            # An invalidated historical block covers no sources. Continuation
            # must still reflect the current valid blocks and active originals.
            blocks = self._current_blocks(db, project, card, binding)
            # Later range validation can observe a source change after the first
            # projection. Recheck historical blocks even when superseded.
            if block["status"] == "completed":
                block = self._completed_block(db, project, card, record, binding, result)
            block["has_more_sources"] = self._has_uncovered_sources(db, project, card, blocks)
            self._authorize(user, project, card, channel)
            return block

    def _completed_block(self, db, project, card, record, binding, result):
        """Internal projection; callers must first authorize the current reader or profile."""
        if record.policy_version != POLICY_VERSION:
            return {**result, "status": "invalidated", "reason": "policy_changed"}
        if not binding or binding.id != record.internal_bot_id or provider_revision(binding) != record.provider_revision:
            return {**result, "status": "invalidated", "reason": "provider_changed"}
        refs = [source["evidence_ref"] for source in record.source_manifest]
        try:
            first, last = self._verified_manifest(project, card, record.source_manifest)
        except CommentCompactionError:
            return {**result, "status": "invalidated", "reason": "source_changed"}
        if compaction_manifest_revision(record.source_manifest) != record.source_revision:
            return {**result, "status": "invalidated", "reason": "source_changed"}
        try:
            draft = validate_compaction_draft(json.dumps(record.draft), set(refs)).model_dump(mode="json")
        except (ValueError, TypeError):
            return {**result, "status": "invalidated", "reason": "invalid_result"}
        return {**result, "draft": draft, "evidence_refs": refs, "range_end": last.evidence_ref,
                "generated_by": {"uid": binding.get_uid(), "display_name": binding.display_name},
                "source_revision": record.source_revision,
                "covered_from": first.created_at.isoformat(), "covered_through": last.created_at.isoformat()}

    def _has_uncovered_sources(self, db, project, card, blocks):
        """Continuation depends on current disjoint coverage, never event age."""
        covered = {ref for block in blocks for ref in block["evidence_refs"]}
        events = self._events(project, card)
        query = select(events.c.id).where(events.c.active)
        if covered:
            query = query.where(tuple_(events.c.kind, events.c.id).not_in([self._identity(ref) for ref in covered]))
        return db.exec(query.limit(1)).first() is not None

    @staticmethod
    def _comment_characters(events):
        # Count stored comment bodies in Unicode characters, not JSON escaping,
        # author metadata, previous summaries or card-movement event payloads.
        body = (cast(cast(events.c.payload, JSON).op("#>>")(literal([], type_=ARRAY(Text))), JSON).op("->>")("content")
                if DbEngine.get_main_engine().dialect.name == "postgresql"
                else func.json_extract(func.json_extract(events.c.payload, "$"), "$.content"))
        return case((events.c.kind == "comment", func.coalesce(func.length(body), 0)), else_=0)

    @staticmethod
    def _configured_threshold(db, card, rule_id=None):
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition)
            .join(ProjectColumn, ProjectColumn.workflow_stage == WorkflowStageDefinition.key)
            .where(ProjectColumn.id == card.project_column_id, ProjectColumn.project_id == card.project_id,
                   ProjectColumn.deleted_at.is_(None), WorkflowStageDefinition.is_active.is_(True))
            .execution_options(populate_existing=True)).first()
        if stage is None:
            return None
        from ...models import WorkflowRule
        from .WorkflowRuleService import action_policy, compaction_threshold_for_rule, rows
        if rule_id is None:
            candidates = rows(db, WorkflowRule, stage_id=stage.id, is_enabled=True, retired=False)
            rule_id = next((rule.id for rule in candidates if action_policy(db, stage, rule.id, "comments.compact")), None)
        if rule_id is None or action_policy(db, stage, rule_id, "comments.compact") is None:
            return None
        try:
            return compaction_threshold_for_rule(db, rule_id)
        except ValueError:
            raise CommentCompactionError("policy_changed") from None

    def _uncovered_comment_volume(self, db, project, card, covered):
        """Aggregate comment volume and actor runs in SQL; never fetch full bodies."""
        events = self._events(project, card)
        query = select(events.c.id, events.c.created_at, events.c.user_id, events.c.bot_id,
                       self._comment_characters(events).label("characters")).where(
                           events.c.active, events.c.kind == "comment")
        if covered:
            query = query.where(tuple_(events.c.kind, events.c.id).not_in([self._identity(ref) for ref in covered]))
        comments = query.subquery()
        ordering = (comments.c.created_at, comments.c.id)
        runs = select(comments.c.characters,
            case((or_(func.row_number().over(order_by=ordering) == 1,
                      comments.c.user_id.is_distinct_from(func.lag(comments.c.user_id).over(order_by=ordering)),
                      comments.c.bot_id.is_distinct_from(func.lag(comments.c.bot_id).over(order_by=ordering))), 1),
                 else_=0).label("turn")).subquery()
        count, characters, turns = db.exec(select(func.count(), func.coalesce(func.sum(runs.c.characters), 0),
                                                  func.coalesce(func.sum(runs.c.turn), 0)).select_from(runs)).first()
        return {"comments": count, "characters": characters, "turns": turns}

    def _eligibility(self, user, project, card, can_generate, blocks):
        """Request-local volume projection over blocks already verified by this caller."""
        with DbSession.use(readonly=False) as db:
            events = self._events(project, card)
            covered = {ref for block in blocks for ref in block["evidence_refs"]}
            uncovered = (tuple_(events.c.kind, events.c.id).not_in([self._identity(ref) for ref in covered])
                         if covered else literal(True))
            volume = select(events.c.payload, uncovered.label("uncovered"),
                            self._comment_characters(events).label("characters")).where(events.c.active).subquery()
            count, characters, remaining_count, remaining_characters = db.exec(select(
                func.count(), func.coalesce(func.sum(volume.c.characters), 0),
                func.coalesce(func.sum(case((volume.c.uncovered, 1), else_=0)), 0),
                func.coalesce(func.sum(case((volume.c.uncovered, volume.c.characters), else_=0)), 0),
            ).select_from(volume)).first()
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            threshold = self._configured_threshold(db, card)
            comment_volume = self._uncovered_comment_volume(db, project, card, covered)
            needs_compaction = (compaction_threshold_reached(threshold, **comment_volume) if threshold is not None
                                else remaining_count >= 20 or remaining_characters >= 12_000)
            next_key = None
            if needs_compaction and can_generate and binding:
                try:
                    snapshot, _ = self._cumulative_snapshot(project, card, blocks)
                    next_key = request_key(card, user, binding, snapshot)
                except CommentCompactionError:
                    pass
        return {"can_generate": can_generate, "provider_configured": binding is not None,
                "event_count": count, "source_characters": characters,
                "uncompacted_event_count": remaining_count, "uncompacted_characters": remaining_characters,
                "needs_compaction": needs_compaction, "next_request_key": next_key,
                "uncompacted_comment_count": comment_volume["comments"], "uncompacted_turn_count": comment_volume["turns"],
                **(threshold or {"compact_after_characters": None, "compact_after_comments": 0, "compact_after_turns": 0})}

    def get_page(self, user, project, card, *, before=None, request_key_filter=None, channel=CollaborationChannel.Api):
        """Expose bounded verified blocks and the caller's in-flight request IDs."""
        with DbSession.atomic() as db:
            project, card, can_generate = self._authorize(user, project, card, channel)
            project_id, card_id = project.id, card.id
            active = self._valid_blocks(user, project, card, channel=channel)
            eligibility = self._eligibility(user, project, card, can_generate, active)
            query = SqlBuilder.select.table(CardCommentCompaction).where(
                CardCommentCompaction.card_id == card_id,
                or_(CardCommentCompaction.status == "completed", CardCommentCompaction.user_id == user.id),
            )
            if request_key_filter is not None:
                if len(request_key_filter) != 64 or any(char not in "0123456789abcdef" for char in request_key_filter):
                    raise CommentCompactionError("invalid_request_key", 400)
                query = query.where(CardCommentCompaction.request_key == request_key_filter, CardCommentCompaction.user_id == user.id)
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
                "eligibility": eligibility}

    def _valid_blocks(self, user, project, card, *, channel):
        """Choose current disjoint ranges; superseded summaries never hide sources."""
        with DbSession.atomic() as db:
            project, card, _ = self._authorize(user, project, card, channel)
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            blocks = self._current_blocks(db, project, card, binding, user=user)
            has_more = self._has_uncovered_sources(db, project, card, blocks)
            return [{**block, "has_more_sources": has_more} for block in blocks]

    def _current_blocks(self, db, project, card, binding, *, user=None):
        """Internal selection; the caller must authorize the human or profile."""
        rows = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
            CardCommentCompaction.card_id == card.id, CardCommentCompaction.status == "completed",
        ).order_by(CardCommentCompaction.created_at.desc(), CardCommentCompaction.id.desc())).all()
        blocks, covered = [], set()
        for record in rows:
            if covered.intersection(source["evidence_ref"] for source in record.source_manifest):
                continue
            result = {"status": "completed", "uid": record.get_uid()}
            if user is not None and record.user_id == user.id:
                result["request_key"] = record.request_key
            block = self._completed_block(db, project, card, record, binding, result)
            if block["status"] != "completed" or covered.intersection(block["evidence_refs"]):
                continue
            covered.update(block["evidence_refs"])
            blocks.append(block)
        return blocks

    @staticmethod
    def _search_metadata(blocks):
        """Project only currently valid card search metadata from its authoritative compaction drafts."""
        result = {"keywords": [], "entities": []}
        for field, name in (("keywords", "value"), ("entities", "name")):
            seen = set()
            for block in blocks:
                for item in block["draft"].get(field, []):
                    identity = (item.get("kind"), item[name].casefold())
                    if identity in seen or len(result[field]) >= 40:
                        continue
                    seen.add(identity)
                    result[field].append({**item, "compaction_uid": block["uid"],
                                          "source_revision": block["source_revision"]})
        return {"source": "comment_compaction", "derived": True, **result}

    def get_search_metadata(self, user, project, card, *, channel=CollaborationChannel.Api):
        with DbSession.atomic():
            project, card, _ = self._authorize(user, project, card, channel)
            blocks = self._valid_blocks(user, project, card, channel=channel)
            self._authorize(user, project, card, channel)
            return self._search_metadata(blocks)

    def search_metadata_candidates(self, user, project, context, query, *, channel=CollaborationChannel.Api, request=None):
        from ..CardVisibilityPolicy import card_visibility_scope

        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        postgres = DbEngine.get_main_engine().dialect.name == "postgresql"
        elements = func.json_array_elements if postgres else func.json_each
        keywords = elements(CardCommentCompaction.draft["keywords"]).table_valued("value").alias("search_keywords")
        entities = elements(CardCommentCompaction.draft["entities"]).table_valued("value").alias("search_entities")
        aliases = (func.json_array_elements_text if postgres else func.json_each)(
            entities.c.value.op("->")("aliases")).table_valued("value").alias("search_aliases")
        keyword_match = select(literal(1)).select_from(keywords).where(
            keywords.c.value.op("->>")("value").ilike(pattern, escape="\\")).correlate(CardCommentCompaction).exists()
        alias_match = select(literal(1)).select_from(aliases).where(
            aliases.c.value.ilike(pattern, escape="\\")).correlate(entities).exists()
        entity_match = select(literal(1)).select_from(entities).where(or_(
            entities.c.value.op("->>")("name").ilike(pattern, escape="\\"), alias_match,
        )).correlate(CardCommentCompaction).exists()
        # Persisted hints may be stale. Apply the result limit after current-source
        # validation, reading bounded SQL pages without retaining all candidates.
        query_statement = (select(Card.id).join(CardCommentCompaction, CardCommentCompaction.card_id == Card.id)
            .join(ProjectColumn, and_(ProjectColumn.id == Card.project_column_id, ProjectColumn.project_id == Card.project_id))
            .where(Card.project_id == project.id, Card.deleted_at.is_(None), Card.source_type.is_(None),
                   ProjectColumn.deleted_at.is_(None), card_visibility_scope(context),
                   CardCommentCompaction.status == "completed", or_(keyword_match, entity_match))
            .distinct().order_by(Card.id.desc()).limit(20))
        matches, before = {}, None
        while len(matches) < 20:
            with DbSession.use(readonly=False) as db:
                candidates = db.exec(query_statement.where(Card.id < before) if before is not None else query_statement).all()
            if not candidates:
                break
            before = candidates[-1][0]
            for (card_id,) in candidates:
                try:
                    metadata = self.get_search_metadata(user, project, card_id, channel=channel)
                except CommentCompactionError:
                    continue
                terms = [item["value"] for item in metadata["keywords"]]
                terms.extend(term for item in metadata["entities"] for term in [item["name"], *item.get("aliases", [])])
                if any(query.casefold() in term.casefold() for term in terms):
                    matches[card_id] = metadata
                    if len(matches) == 20:
                        break
            if len(candidates) < 20:
                break
        # Native board similarity ranks all indexed card ages in one bounded query.
        from types import SimpleNamespace
        from ....tasks.docling.CompactionMetadataIndexTask import search_board_metadata, validate_metadata_hits

        facade = SimpleNamespace(card_comment_compaction=self, internal_bot=self._get_service(InternalBotService),
                                 card=self._get_service(CardService))
        semantic = search_board_metadata(facade, user, project, query, channel=channel, request=request)
        for card_id, similarity in semantic.items():
            try:
                metadata = self.get_search_metadata(user, project, card_id, channel=channel)
            except CommentCompactionError:
                continue
            matches[card_id] = {**metadata, "similarity": similarity}
        # Later inference may revoke an earlier result. Recheck all accumulated
        # sources and readers locally after the last remote operation.
        for card_id, matched in list(matches.items()):
            try:
                fresh = self.get_search_metadata(user, project, card_id, channel=channel)
                with DbSession.use(readonly=False) as db:
                    current_card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card_id)).first()
                similarity = validate_metadata_hits(facade, user, project, current_card,
                    matched.get("similarity", []), channel=channel)
                # Rejected hits may reflect a source change after lexical projection.
                if len(similarity) < len(matched.get("similarity", [])):
                    fresh = self.get_search_metadata(user, project, card_id, channel=channel)
                terms = [item["value"] for item in fresh["keywords"]]
                terms.extend(term for item in fresh["entities"] for term in [item["name"], *item.get("aliases", [])])
                if similarity or any(query.casefold() in term.casefold() for term in terms):
                    matches[card_id] = {**fresh, **({"similarity": similarity} if similarity else {})}
                else:
                    del matches[card_id]
            except CommentCompactionError:
                del matches[card_id]
        return dict(list(matches.items())[:20])

    def get_view(self, user, project, card, *, before=None, channel=CollaborationChannel.Api):
        """One canonical page of current summaries and uncovered original events."""
        from .ActivityService import ActivityService

        with DbSession.atomic() as db:
            project, card, can_generate = self._authorize(user, project, card, channel)
            project_id, card_id = project.id, card.id
            blocks = self._valid_blocks(user, project, card, channel=channel)
            eligibility = self._eligibility(user, project, card, can_generate, blocks)
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
            own_active = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
                CardCommentCompaction.card_id == card_id, CardCommentCompaction.user_id == user.id,
                CardCommentCompaction.status.in_(["pending", "running"]),
            ).order_by(CardCommentCompaction.created_at.desc(), CardCommentCompaction.id.desc()).limit(5)).all()
            return {"records": [entry[1] for entry in page],
                    "next_cursor": page[-1][2] if has_more and page else None,
                    "eligibility": eligibility,
                    "total_count": eligibility["uncompacted_event_count"] + len(blocks),
                    "in_flight": [self.get(user, project_id, card_id, record.id, channel=channel) for record in own_active]}

    def get_evidence(self, user, project, card, uid, *, before=None, evidence_ref=None, channel=CollaborationChannel.Api):
        """Page original records through the existing timeline on the primary."""
        from .ActivityService import ActivityService

        with DbSession.atomic():
            current = self.get(user, project, card, uid, channel=channel)
            if current["status"] != "completed":
                raise CommentCompactionError("compaction_invalidated")
            refs = current["evidence_refs"]
            if evidence_ref is not None:
                if evidence_ref not in refs:
                    raise CommentCompactionError("invalid_evidence_ref", 400)
                refs = [evidence_ref]
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

    def _automatic_context(self, db, project_id, profile_id, card_id, *, rule_id, effect="compact"):
        if effect not in {"compact", "archive"}:
            raise CommentCompactionError("policy_changed")
        try:
            project, binding, card = require_compactor_card(db, project_id, profile_id, card_id, action=effect)
        except CompactorAuthorityError as error:
            raise CommentCompactionError(error.code, error.status) from None
        current = self._get_service(InternalBotService).get_comment_compaction_binding()
        if current is None or current.id != binding.id:
            raise CommentCompactionError("provider_changed")
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(
            ProjectColumn.id == card.project_column_id, ProjectColumn.project_id == project.id,
        ).execution_options(populate_existing=True).with_for_update()).first()
        if card.archived_at is not None or column is None or column.deleted_at or column.is_archive or not column.workflow_stage:
            raise CommentCompactionError("policy_changed")
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
            WorkflowStageDefinition.key == column.workflow_stage,
        ).execution_options(populate_existing=True).with_for_update()).first()
        from .WorkflowRuleService import action_policy, originating_run_is_current
        policy = action_policy(db, stage, rule_id, "comments.compact" if effect == "compact" else "card.archive") if stage else None
        if (stage is None or not stage.is_active or policy is None
                or (effect == "archive" and not stage.counts_as_completed)):
            raise CommentCompactionError("policy_changed")
        if not originating_run_is_current(db, policy[0], stage, card):
            raise CommentCompactionError("workflow_context_changed")
        # A return to the same column starts a new residency. Do not publish an
        # asynchronous result authorized by the earlier stay in this stage.
        residency = (card.workflow_stage_entered_key,
                     _utc(card.workflow_stage_entered_at) if card.workflow_stage_entered_at else None)
        return project, binding, card, {
            "rule_uid": policy[0].get_uid(), "rule_revision": policy[0].revision,
            "column_uid": column.get_uid(), "stage_uid": stage.get_uid(), "stage_revision": stage.edit_revision(),
            "residency_key": residency[0], "residency_entered_at": residency[1].isoformat() if residency[1] else None,
        }

    async def generate_automatic(self, project, profile, card, *, rule_id, retry=False):
        """Internal continuous command; no user impersonation or public draft."""
        if DbSession.has_active_transaction():
            raise CommentCompactionError("transaction_not_supported")
        project_id, profile_id, card_id = map(InfraHelper.convert_id, (project, profile, card))
        with DbSession.atomic() as db:
            try:
                project, binding, card, policy = self._automatic_context(db, project_id, profile_id, card_id, rule_id=rule_id)
            except CommentCompactionError as error:
                if error.code == "workflow_context_changed":
                    return {"status": "skipped", "reason": "workflow_context_changed"}
                raise
            binding_revision = provider_revision(binding)
            blocks = self._current_blocks(db, project, card, binding)
            covered = {ref for block in blocks for ref in block["evidence_refs"]}
            volume = self._uncovered_comment_volume(db, project, card, covered)
            threshold = self._configured_threshold(db, card, rule_id=rule_id)
            if threshold is None:
                raise CommentCompactionError("policy_changed")
            if not compaction_threshold_reached(threshold, **volume):
                return {"status": "skipped", "reason": "below_threshold"}
            snapshot, has_more = self._cumulative_snapshot(project, card, blocks)
            record = self.repo.card_comment_compaction.reserve_automatic(card, binding, snapshot)
            claimed = self.repo.card_comment_compaction.claim(record, retry=retry,
                lease_seconds=min(3600, Env.AI_REQUEST_TIMEOUT + 30))
        def revalidate():
            with DbSession.atomic() as db:
                fresh_project, fresh_binding, fresh_card, fresh_policy = self._automatic_context(
                    db, project_id, profile_id, card_id, rule_id=rule_id)
                if fresh_policy != policy:
                    raise CommentCompactionError("permission_changed")
                if provider_revision(fresh_binding) != binding_revision:
                    raise CommentCompactionError("provider_changed")
                fresh = self._fresh_cumulative_snapshot(fresh_project, fresh_card, snapshot)
                if fresh.source_revision() != snapshot.source_revision():
                    raise CommentCompactionError("source_changed")

        if claimed is not None:
            try:
                draft = await self._generate_steps(binding, snapshot, record, claimed.attempt, revalidate)
            except asyncio.CancelledError:
                self.repo.card_comment_compaction.fail(record, claimed.attempt, "cancelled")
                raise
            except CommentCompactionError as error:
                code = error.code if error.code in {"generation_failed", "provider_unconfigured", "source_changed", "provider_changed", "permission_changed", "cancelled"} else "permission_changed" if error.status in {403, 404} or error.code == "workflow_context_changed" else "invalid_result"
                self.repo.card_comment_compaction.fail(record, claimed.attempt, code)
            except Exception:
                self.repo.card_comment_compaction.fail(record, claimed.attempt, "provider_error")
            else:
                try:
                    with DbSession.atomic() as db:
                        fresh_project, fresh_binding, fresh_card, fresh_policy = self._automatic_context(
                            db, project_id, profile_id, card_id, rule_id=rule_id)
                        if fresh_policy != policy:
                            raise CommentCompactionError("policy_changed")
                        if provider_revision(fresh_binding) != binding_revision:
                            raise CommentCompactionError("provider_changed")
                        fresh_snapshot = self._fresh_cumulative_snapshot(fresh_project, fresh_card, snapshot)
                        if fresh_snapshot.source_revision() != snapshot.source_revision():
                            raise CommentCompactionError("source_changed")
                        self.repo.card_comment_compaction.complete(record, claimed.attempt, fresh_snapshot, fresh_binding, draft,
                            automatic_authority=fresh_policy)
                except CommentCompactionError as error:
                    code = error.code if error.code in {"source_changed", "provider_changed"} else "permission_changed"
                    self.repo.card_comment_compaction.fail(record, claimed.attempt, code)
        # Only a receipt leaves the worker. Readers use the existing authorized projection.
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(CardCommentCompaction.id == record.id)
                              .execution_options(populate_existing=True)).first()
            from ...models import WorkflowEffectRun
            effect = db.exec(SqlBuilder.select.table(WorkflowEffectRun).where(
                WorkflowEffectRun.execution_key == f"comment_compaction:{current.get_uid()}:{current.attempt}")).first()
            # Inference may have admitted new originals, or failed without
            # publishing coverage. Inspect only under the profile's current
            # authority; lost authority makes continuation unknown.
            has_more = None
            try:
                fresh_project, fresh_binding, fresh_card, _ = self._automatic_context(
                    db, project_id, profile_id, card_id, rule_id=rule_id)
            except CommentCompactionError:
                pass
            else:
                current_blocks = self._current_blocks(db, fresh_project, fresh_card, fresh_binding)
                has_more = self._has_uncovered_sources(db, fresh_project, fresh_card, current_blocks)
            return {"uid": current.get_uid(), "status": current.status,
                    "error_code": current.error_code, "has_more_sources": has_more,
                    "legacy_effect_run_uid": effect.get_uid() if effect else None}

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
            blocks = self._valid_blocks(user, project, card, channel=channel)
            if record:
                if binding.id != record.internal_bot_id or provider_revision(binding) != record.provider_revision:
                    raise CommentCompactionError("provider_changed")
                if record.status == "completed":
                    return self.get(user, project, card, record.id, channel=channel)
                snapshot, has_more = self._cumulative_snapshot(project, card, blocks,
                    required_refs={item["evidence_ref"] for item in record.source_manifest})
                if snapshot.source_revision() != record.source_revision:
                    raise CommentCompactionError("source_changed")
            else:
                snapshot, has_more = self._cumulative_snapshot(project, card, blocks, after=after)
                if expected_request_key and request_key(card, user, binding, snapshot) != expected_request_key:
                    raise CommentCompactionError("source_changed")
                record = self.repo.card_comment_compaction.reserve(card, user, binding, snapshot)
            claimed = self.repo.card_comment_compaction.claim(record, retry=retry,
                lease_seconds=min(3600, Env.AI_REQUEST_TIMEOUT + 30))
        def revalidate():
            with DbSession.atomic():
                fresh_project, fresh_card, _ = self._authorize(user, project_id, card_id, channel, write=True)
                fresh_binding = self._get_service(InternalBotService).get_comment_compaction_binding()
                if not fresh_binding or fresh_binding.id != binding.id or provider_revision(fresh_binding) != provider_revision(binding):
                    raise CommentCompactionError("provider_changed")
                fresh = self._fresh_cumulative_snapshot(fresh_project, fresh_card, snapshot)
                if fresh.source_revision() != snapshot.source_revision():
                    raise CommentCompactionError("source_changed")

        if claimed is not None:
            try:
                draft = await self._generate_steps(binding, snapshot, record, claimed.attempt, revalidate)
            except asyncio.CancelledError:
                self.repo.card_comment_compaction.fail(record, claimed.attempt, "cancelled")
                raise
            except CommentCompactionError as error:
                code = error.code if error.code in {"generation_failed", "provider_unconfigured", "source_changed", "provider_changed", "cancelled"} else "permission_changed" if error.status in {403, 404} else "invalid_result"
                self.repo.card_comment_compaction.fail(record, claimed.attempt, code)
            except Exception:
                self.repo.card_comment_compaction.fail(record, claimed.attempt, "provider_error")
            else:
                try:
                    with DbSession.atomic():
                        fresh_project, fresh_card, _ = self._authorize(user, project_id, card_id, channel, write=True)
                        fresh_binding = self._get_service(InternalBotService).get_comment_compaction_binding()
                        if not fresh_binding or fresh_binding.id != binding.id or provider_revision(fresh_binding) != provider_revision(binding):
                            raise CommentCompactionError("provider_changed")
                        fresh_snapshot = self._fresh_cumulative_snapshot(fresh_project, fresh_card, snapshot)
                        if fresh_snapshot.source_revision() != snapshot.source_revision():
                            raise CommentCompactionError("source_changed")
                        self.repo.card_comment_compaction.complete(record, claimed.attempt, fresh_snapshot, fresh_binding, draft)
                except CommentCompactionError as error:
                    code = error.code if error.code in {"source_changed", "provider_changed"} else "permission_changed"
                    self.repo.card_comment_compaction.fail(record, claimed.attempt, code)
        # Recheck visibility and source validity before returning any derived text.
        result = self.get(user, project_id, card_id, record.id, channel=channel)
        if result["status"] == "completed":
            return result
        # Failed or in-flight snapshots have not covered any new sources.
        with DbSession.atomic() as db:
            project, card, _ = self._authorize(user, project_id, card_id, channel)
            binding = self._get_service(InternalBotService).get_comment_compaction_binding()
            blocks = self._current_blocks(db, project, card, binding)
            return {**result, "has_more_sources": self._has_uncovered_sources(db, project, card, blocks)}
