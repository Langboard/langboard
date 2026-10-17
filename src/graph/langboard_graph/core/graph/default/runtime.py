"""Invocation-only credentials; checkpoints retain a model reference and redacted tweaks."""

from copy import deepcopy
from json import loads as json_loads
from typing import Any, TypedDict
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.contracts.audit_events import REDACTED
from langboard_shared.domain.models import Bot, InternalBot
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langgraph.runtime import Runtime
from .state import DefaultGraphState


class DefaultGraphContext(TypedDict):
    tweaks: dict[str, Any]


def invocation_tweaks(state: DefaultGraphState, runtime: Runtime[DefaultGraphContext] | None) -> dict[str, Any]:
    # Copy before applying approval policy: neither runtime context nor checkpoint input is mutable authority.
    return deepcopy(runtime.context["tweaks"] if runtime and runtime.context else state.get("tweaks") or {})


def restore_model_credentials(state: DefaultGraphState) -> dict[str, Any]:
    tweaks = deepcopy(state.get("tweaks") or {})
    source = state.get("model_source")
    config = tweaks.get("Graph")
    if not source or not isinstance(config, dict):
        return tweaks
    model = {"bot": Bot, "internal_bot": InternalBot}.get(source.get("table"))
    if model is None:
        raise ValueError("Invalid graph model source")
    with DbSession.use(readonly=False) as db:
        bot = db.exec(
            SqlBuilder.select.table(model).where(model.id == source["id"], model.deleted_at.is_(None))
        ).first()
        if (
            bot is None
            or bot.platform != BotPlatform.Default
            or bot.platform_running_type != BotPlatformRunningType.Default
        ):
            raise ValueError("Graph model source is no longer available")
        current = json_loads(bot.value)
    settings = config.get("settings") or {}
    if (
        not isinstance(current, dict)
        or current.get("agent_llm") != config.get("agent_llm")
        or current.get("base_url") != settings.get("base_url")
    ):
        raise ValueError("Graph model provider changed; start a new run")
    config["settings"] = _restore_redacted_values(settings, current)
    return tweaks


def _restore_redacted_values(saved: Any, current: Any) -> Any:
    if saved == REDACTED:
        if current is None or current == REDACTED:
            raise ValueError("Graph model credentials are no longer available")
        return current
    if isinstance(saved, dict):
        return {
            key: _restore_redacted_values(value, current.get(key) if isinstance(current, dict) else None)
            for key, value in saved.items()
        }
    if isinstance(saved, list):
        return [
            _restore_redacted_values(
                value, current[index] if isinstance(current, list) and index < len(current) else None
            )
            for index, value in enumerate(saved)
        ]
    return saved
