"""Invocation-only credentials; checkpoints retain a model reference and redacted tweaks."""

from copy import deepcopy
from json import loads as json_loads
from typing import Any, TypedDict
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.contracts.audit_events import REDACTED
from langboard_shared.domain.models import Bot, InternalBot, Project, ProjectWiki, ProjectWikiAssignedUser
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.helpers import InfraHelper
from langboard_shared.tasks.bots.utils.BotTaskDataHelper import BotTaskDataHelper
from langgraph.runtime import Runtime
from sqlalchemy.orm import defer
from .state import DefaultGraphState


class DefaultGraphContext(TypedDict):
    tweaks: dict[str, Any]
    execution_scope: dict[str, Any]


def invocation_tweaks(state: DefaultGraphState, runtime: Runtime[DefaultGraphContext] | None) -> dict[str, Any]:
    # Copy before applying approval policy: neither runtime context nor checkpoint input is mutable authority.
    return revalidate_graph_relationships(
        runtime.context["tweaks"] if runtime and runtime.context else state.get("tweaks") or {}
    )


def revalidate_graph_relationships(tweaks: dict[str, Any]) -> dict[str, Any]:
    """Recheck native relationship snapshots before checkpointing or inference."""
    current = deepcopy(tweaks)
    for variables in (current, current.get("LangboardCalledVariablesComponent")):
        if isinstance(variables, dict) and isinstance(variables.get("rest_data"), dict):
            variables["rest_data"] = BotTaskDataHelper.revalidate_related_cards(variables["rest_data"])
    return current


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


def _require_wiki_scope(actor, project_uid: str, wiki_uid: str | None, services) -> None:
    if wiki_uid is None:
        return
    with DbSession.use(readonly=False) as db:
        project = db.exec(
            SqlBuilder.select.table(Project)
            .where(Project.id == InfraHelper.convert_id(project_uid))
            .execution_options(populate_existing=True)
        ).first()
        wiki = (
            db.exec(
                SqlBuilder.select.table(ProjectWiki)
                .where(ProjectWiki.id == InfraHelper.convert_id(wiki_uid), ProjectWiki.project_id == project.id)
                .options(defer(ProjectWiki.content, raiseload=True))
                .execution_options(populate_existing=True)
            ).first()
            if project is not None
            else None
        )
        if wiki is None:
            raise ValueError("Graph wiki is no longer available in the selected project")
        assigned_ids = (
            db.exec(
                SqlBuilder.select.column(ProjectWikiAssignedUser.user_id).where(
                    ProjectWikiAssignedUser.project_wiki_id == wiki.id,
                    ProjectWikiAssignedUser.user_id == actor.id,
                )
            ).all()
            if not wiki.is_public
            else []
        )
        if not services.project_wiki.can_view(actor, project, wiki, assigned_ids):
            raise ValueError("Graph wiki access is no longer authorized")


def resolve_execution_scope(tweaks: dict[str, Any]) -> dict[str, Any]:
    """Resolve checkpoint identity from native authentication and current primary ACLs.

    The saved scope is a comparison binding, never an authorization grant. Store
    remains unused: this graph has no cross-thread data requirement.
    """
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.domain.models import ChatHistory, ChatSession, User
    from langboard_shared.domain.services import DomainService
    from langboard_shared.security import Auth
    from starlette.datastructures import Headers

    variables = tweaks.get("LangboardCalledVariablesComponent") or {}
    rest_data = variables.get("rest_data") or {}
    project_uid = variables.get("project_uid") or rest_data.get("project_uid")
    wiki_uid = rest_data.get("project_wiki_uid")
    if (
        rest_data.get("chat_scope") == "project_wiki"
        and not wiki_uid
        or wiki_uid is not None
        and (not isinstance(wiki_uid, str) or not wiki_uid or not project_uid)
    ):
        raise ValueError("Graph wiki scope requires a wiki and project")
    if (
        variables.get("project_uid")
        and rest_data.get("project_uid")
        and variables["project_uid"] != rest_data["project_uid"]
    ):
        raise ValueError("Graph project scope mismatch")
    runner_type = variables.get("current_runner_type")
    if runner_type not in {"user", "bot"}:
        if project_uid:
            raise ValueError("Scoped graph execution requires a current authenticated actor")
        return {}
    token = variables.get("app_api_token")
    if not isinstance(token, str) or not token or token == REDACTED:
        raise ValueError("Graph execution requires a fresh user token")
    headers = Headers({AuthSecurity.API_TOKEN_HEADER: token})
    if runner_type == "bot":
        bot = Auth.validate_bot(headers)
        if not isinstance(bot, Bot):
            raise ValueError("Graph bot is no longer authorized")
        if project_uid:
            # Existing Bot Hook policy, using a primary transaction for nested helpers.
            with DbSession.atomic(), DomainService.use() as services:
                if not services.bot.has_project_access(bot, project_uid):
                    raise ValueError("Graph bot project access is no longer authorized")
                card_uid = rest_data.get("card_uid")
                if card_uid:
                    from langboard_shared.domain.models import Card

                    card = InfraHelper.get_by_id_like(Card, card_uid)
                    if card is None:
                        raise ValueError("Graph bot card is no longer available")
                    services.bot.require_target_project(card, project_uid)
                _require_wiki_scope(bot, project_uid, wiki_uid, services)
        return {"bot_id": int(bot.id), "project_uid": project_uid}
    actor = Auth.validate_user_by_api_token(headers)
    if not isinstance(actor, User):
        raise ValueError("Graph execution user is no longer authorized")
    scope = {"user_id": int(actor.id), "project_uid": project_uid}
    if project_uid:
        project_id = int(InfraHelper.convert_id(project_uid))
        with DomainService.use() as services:
            contexts = services.card.resolve_work_visibility_contexts(
                actor, CollaborationChannel.Api, project_ids={project_id}
            )
            if project_id not in contexts:
                raise ValueError("Graph project access is no longer authorized")
            card_uid = rest_data.get("card_uid")
            if (
                card_uid
                and services.card.resolve_readable_card(project_uid, card_uid, actor, CollaborationChannel.Api) is None
            ):
                raise ValueError("Graph card access is no longer authorized")
            _require_wiki_scope(actor, project_uid, wiki_uid, services)
    # Chat anchors are selectors; only primary ownership can authorize their content.
    with DbSession.use(readonly=False) as db:
        session_uid = rest_data.get("chat_session_uid") or variables.get("chat_session_uid")
        history_uid = rest_data.get("chat_history_uid") or variables.get("chat_history_uid")
        session_id = InfraHelper.convert_id(session_uid) if session_uid else None
        if history_uid:
            history = db.exec(
                SqlBuilder.select.table(ChatHistory).where(ChatHistory.id == InfraHelper.convert_id(history_uid))
            ).first()
            if history is None or (session_id is not None and history.chat_session_id != session_id):
                raise ValueError("Graph chat scope mismatch")
            session_id = history.chat_session_id
        if session_id is not None:
            session = db.exec(
                SqlBuilder.select.table(ChatSession).where(
                    ChatSession.id == session_id, ChatSession.user_id == actor.id
                )
            ).first()
            if session is None:
                raise ValueError("Graph chat access is no longer authorized")
        scope["chat_session_id"] = int(session_id) if session_id is not None else None
    return scope


def validate_resume_scope(state: DefaultGraphState, tweaks: dict[str, Any], resume: Any) -> dict[str, Any]:
    variables = tweaks.get("LangboardCalledVariablesComponent") or {}
    if variables.get("current_runner_type") == "user":
        token = resume.get("app_api_token") if isinstance(resume, dict) else None
        variables["app_api_token"] = token
    elif variables.get("current_runner_type") == "bot":
        source = state.get("model_source") or {}
        saved = state.get("execution_scope") or {}
        if source.get("table") != "bot" or source.get("id") != saved.get("bot_id"):
            raise ValueError("Graph bot checkpoint source is unavailable; start a new run")
        with DbSession.use(readonly=False) as db:
            bot = db.exec(SqlBuilder.select.table(Bot).where(Bot.id == source["id"])).first()
        if bot is None:
            raise ValueError("Graph bot is no longer available")
        variables["app_api_token"] = bot.app_api_token
        approval_token = resume.get("app_api_token") if isinstance(resume, dict) else None
        if approval_token is not None:
            approval_tweaks = deepcopy(tweaks)
            approval_variables = approval_tweaks["LangboardCalledVariablesComponent"]
            approval_variables["current_runner_type"] = "user"
            approval_variables["app_api_token"] = approval_token
            resolve_execution_scope(approval_tweaks)
    current = resolve_execution_scope(tweaks)
    saved = state.get("execution_scope")
    if saved is None or current != saved:
        raise ValueError("Graph checkpoint execution scope changed; start a new run")
    return current
