"""Prepare current readable native evidence and fence a tool-free semantic draft."""

import json
from anyio import to_thread
from httpx import AsyncClient
from ....ai.avatar_summary import AvatarSummarySnapshot, clean_text, digest, model_settings, validate_draft
from ....core.db import DbSession, SqlBuilder
from ....core.security.CollaborationChannel import CollaborationChannel
from ....core.security.GraphServiceAuth import graph_service_headers
from ....Env import Env
from ...models import Card, CardAssignedProjectLabel, InternalBot, ProjectColumn, ProjectLabel
from ...models.BaseBotModel import BotPlatform, BotPlatformRunningType
from ...models.InternalBot import InternalBotType
from ..CardVisibilityPolicy import CardVisibility, card_visibility_scope
from .AvatarService import AvatarError, AvatarService
from .CardService import CardService
from .InternalBotService import InternalBotService


def profile_settings(binding):
    if (binding is None or binding.deleted_at is not None or binding.bot_type != InternalBotType.AvatarSummary
        or binding.platform != BotPlatform.Default or binding.platform_running_type != BotPlatformRunningType.Default
        or binding.api_url):
        raise AvatarError("avatar_summary_profile_unavailable", 409)
    try:
        return model_settings(binding.value)
    except (ValueError, TypeError):
        raise AvatarError("avatar_summary_profile_unavailable", 409) from None


async def request_avatar_summary(settings, snapshot, revision):
    async with AsyncClient(timeout=Env.AI_REQUEST_TIMEOUT + 5, follow_redirects=False) as client:
        async with client.stream("POST", f"{Env.DEFAULT_GRAPH_URL.rstrip('/')}/api/v1/graph/avatar/semantic-draft",
                                 headers=graph_service_headers(), json={"model_settings": settings,
                                 "source_revision": revision, "sources": snapshot.model_dump(mode="json")["sources"]}) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                content.extend(chunk)
                if len(content) > 16000:
                    raise ValueError("Invalid avatar draft")
    result = json.loads(content)
    if result.get("generated") is not True or result.get("source_revision") != revision:
        raise ValueError("Avatar generation failed")
    return validate_draft(json.dumps(result.get("draft")), snapshot.sources)


class AvatarSummaryService:
    def __init__(self, service):
        self.service = service
        self.avatar = AvatarService(service)

    def context(self, actor, project_uid, *, profile_signatures=None):
        board = self.avatar._target(actor, project_uid)
        base_revision = board.avatar_revision
        cards = self.service._create_or_get_product(CardService)
        resolved = cards.resolve_visibility_context(board, actor, CollaborationChannel.HumanUI)
        if resolved is None:
            raise AvatarError("avatar_forbidden", 403)
        _, visibility = resolved
        with DbSession.use(readonly=False) as db:
            rows = db.exec(SqlBuilder.select.table(Card).join(ProjectColumn, ProjectColumn.id == Card.project_column_id)
                .where(Card.project_id == board.id, ProjectColumn.project_id == board.id,
                       Card.deleted_at.is_(None), ProjectColumn.deleted_at.is_(None), Card.archived_at.is_(None),
                       Card.visibility == CardVisibility.Shared.value, Card.source_type.is_(None),
                       card_visibility_scope(visibility)).order_by(Card.id).limit(24)
                .execution_options(populate_existing=True)).all()
            stages = db.exec(SqlBuilder.select.columns(ProjectColumn.workflow_stage).where(
                ProjectColumn.project_id == board.id, ProjectColumn.deleted_at.is_(None))
                .distinct().order_by(ProjectColumn.workflow_stage).limit(6)).all()
            labels = []
            if rows:
                labels = db.exec(SqlBuilder.select.columns(ProjectLabel.id, ProjectLabel.name)
                    .join(CardAssignedProjectLabel, CardAssignedProjectLabel.project_label_id == ProjectLabel.id)
                    .where(CardAssignedProjectLabel.card_id.in_([card.id for card in rows]),
                           ProjectLabel.project_id == board.id).distinct().order_by(ProjectLabel.id).limit(12)
                    .execution_options(populate_existing=True)).all()
            profiles = db.exec(SqlBuilder.select.table(InternalBot).where(
                InternalBot.bot_type == InternalBotType.AvatarSummary, InternalBot.deleted_at.is_(None))
                .order_by(InternalBot.id).limit(100).execution_options(populate_existing=True)).all()
        sources = []

        def add(key, kind, value, limit):
            text = clean_text(value, limit)
            if text and sum(len(source["text"]) for source in sources) + len(text) <= 5000:
                sources.append({"id": digest([project_uid, key])[:40], "kind": kind, "text": text})

        add("title", "project_title", board.title, 120)
        add("description", "project_description", board.description, 1000)
        add("type", "project_type", board.project_type, 120)
        stages = sorted({str(stage) for stage in stages if stage})
        if stages:
            add("stages", "workflow_stage", ", ".join(stages), 120)
        for card in rows:
            add(f"card:{card.id}", "card_title", card.title, 120)
        for label_id, label_name in labels:
            add(f"label:{label_id}", "label", label_name, 60)
        projected = []
        signatures = []
        for profile in profiles:
            try:
                settings = profile_settings(profile)
            except AvatarError:
                continue
            signature = digest([profile.get_uid(), profile.value, profile.api_key, profile.updated_at, profile.display_name])
            signatures.append(signature)
            if profile_signatures is not None:
                profile_signatures[profile.get_uid()] = signature
            projected.append({"uid": profile.get_uid(), "display_name": profile.display_name,
                              "model_name": settings.get("model_name") or settings.get("model") or settings.get("model_id")})
        current = self.avatar._target(actor, project_uid)
        if current.avatar_revision != base_revision:
            raise AvatarError("avatar_summary_source_changed", 409)
        return {"external_request": False, "source_revision": digest([actor.get_uid(), board.get_uid(), board.avatar_revision, sources, signatures]),
                "sources": sources, "profiles": projected}

    async def preview(self, actor, project_uid, revision, profile_uid, source_ids, confirmed):
        if confirmed is not True or not profile_uid or not source_ids or len(source_ids) != len(set(source_ids)):
            raise AvatarError("avatar_summary_confirmation_required", 400)

        def prepare():
            reviewed_signatures = {}
            context = self.context(actor, project_uid, profile_signatures=reviewed_signatures)
            if revision != context["source_revision"]:
                raise AvatarError("avatar_summary_source_changed", 409)
            allowed = {source["id"]: source for source in context["sources"]}
            if not set(source_ids).issubset(allowed):
                raise AvatarError("avatar_summary_source_invalid", 400)
            if not any(profile["uid"] == profile_uid for profile in context["profiles"]):
                raise AvatarError("avatar_summary_profile_unavailable", 409)
            binding = InternalBotService(None, None, None).get_current_by_id_like(profile_uid)
            settings = profile_settings(binding)
            signature = digest([binding.get_uid(), binding.value, binding.api_key, binding.updated_at, binding.display_name])
            if signature != reviewed_signatures.get(profile_uid):
                raise AvatarError("avatar_summary_profile_changed", 409)
            snapshot = AvatarSummarySnapshot(sources=[source for source in context["sources"] if source["id"] in source_ids])
            return settings, signature, snapshot

        settings, signature, snapshot = await to_thread.run_sync(prepare)
        try:
            draft = await request_avatar_summary(settings, snapshot, revision)
        except Exception:
            raise AvatarError("avatar_summary_generation_failed", 502) from None
        _, current_signature, current_snapshot = await to_thread.run_sync(prepare)
        if signature != current_signature or current_snapshot != snapshot:
            raise AvatarError("avatar_summary_profile_changed", 409)
        return {"external_request": True, "source_revision": revision, "profile_uid": profile_uid, "prompt": draft.prompt}
