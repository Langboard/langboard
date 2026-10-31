"""Capture only native validated scope; observability never changes execution."""

import logging
from contextvars import ContextVar
from inspect import signature
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, McpToolExecution, Project, ProjectBotScope, ProjectColumn, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from langboard_shared.Env import Env
from langboard_shared.helpers import InfraHelper
from sqlalchemy import select
from sqlalchemy.orm import Session


execution_scope = ContextVar("mcp_execution_scope", default=None)
logger = logging.getLogger(f"{Env.PROJECT_NAME}.mcp.history")


def resolve_scope(actor, handler, arguments):
    """Only declared native resource identifiers can nominate an audience."""
    if not isinstance(actor, (User, Bot)):
        return None
    parameters = signature(handler).parameters
    project_uid = arguments.get("project_uid") if "project_uid" in parameters else None
    card_uid = arguments.get("card_uid") if "card_uid" in parameters else None
    column_uid = arguments.get("column_uid") if "column_uid" in parameters else None
    if not project_uid:
        return None
    service = DomainService()
    try:
        if isinstance(actor, User):
            project = service.workflow_stage._authorized_app_board(
                actor, project_uid, ProjectRoleAction.Read, revocation=True
            )
        else:
            # Match native BotService hook authority against current primary rows.
            with Session(DbEngine.get_main_engine()) as db:
                project = (
                    db.execute(
                        select(Project)
                        .join(ProjectBotScope, ProjectBotScope.project_id == Project.id)
                        .join(Bot, Bot.id == ProjectBotScope.bot_id)
                        .where(
                            Project.id == InfraHelper.convert_id(project_uid),
                            Bot.id == actor.id,
                            Bot.deleted_at.is_(None),
                        )
                    )
                    .scalars()
                    .first()
                )
        if project is None or project.deleted_at:
            return None
        card = None
        if card_uid:
            if isinstance(actor, User):
                resolved = service.card.resolve_readable_card(
                    project, card_uid, actor, CollaborationChannel.Mcp, metadata_only=True
                )
                if resolved is None:
                    return None
                card = resolved[1]
            else:
                # Bot hook authority does not grant access to private card content.
                with Session(DbEngine.get_main_engine()) as db:
                    card = db.execute(
                        select(Card.id, Card.project_id, Card.deleted_at, Card.visibility).where(
                            Card.id == InfraHelper.convert_id(card_uid)
                        )
                    ).first()
                if (
                    card is None
                    or card.project_id != project.id
                    or card.deleted_at
                    or card.visibility not in {CardVisibility.Shared, CardVisibility.Internal}
                ):
                    return None
        column = None
        if column_uid:
            with Session(DbEngine.get_main_engine()) as db:
                column = (
                    db.execute(select(ProjectColumn).where(ProjectColumn.id == InfraHelper.convert_id(column_uid)))
                    .scalars()
                    .first()
                )
        if column_uid and (column is None or column.project_id != project.id or column.deleted_at):
            return None
        return {
            "project_id": project.id,
            "card_id": card.id if card else None,
            "project_column_id": column.id if column else None,
            "user_id": actor.id if isinstance(actor, User) else None,
            "bot_id": actor.id if isinstance(actor, Bot) else None,
        }
    finally:
        service.close()


def persist_execution(scope, *, request_id, tool, outcome, started_at, duration_ms):
    if not scope or tool == "unknown":
        return
    with Session(DbEngine.get_main_engine()) as db, db.begin():
        # Native nested middleware shares one scope. Unique request identity is a
        # second boundary against duplicate persisted mutation receipts.
        existing = db.execute(select(McpToolExecution.id).where(McpToolExecution.request_id == request_id)).first()
        if existing is None:
            DbSession(db, readonly=False).insert(
                McpToolExecution(
                    **scope,
                    request_id=request_id,
                    tool=tool,
                    outcome=outcome,
                    started_at=started_at,
                    finished_at=SafeDateTime.now(),
                    duration_ms=duration_ms,
                )
            )


def safe_scope(actor, handler, arguments):
    try:
        return resolve_scope(actor, handler, arguments)
    except Exception:
        logger.warning("Native MCP history scope unavailable")
        return None
