"""Bounded native execution metadata; every detail rechecks its original audience."""

from datetime import timezone
from sqlalchemy import and_, or_, select
from ....core.db import DbSession, SqlBuilder
from ....core.types import SafeDateTime
from ....helpers import InfraHelper
from ...models import (
    Bot,
    BotLog,
    BotSchedule,
    BotScheduleRun,
    Card,
    CardBotLog,
    CardBotSchedule,
    ChatHistory,
    ChatSession,
    InternalBotRun,
    McpToolExecution,
    ProjectBotLog,
    ProjectBotSchedule,
    ProjectBotScope,
    ProjectChatSession,
    ProjectColumn,
    ProjectColumnBotLog,
    ProjectColumnBotSchedule,
    SettingRole,
    User,
    WorkflowEffectRun,
    WorkflowStageDefinition,
)
from ...models.InternalBotRun import InternalBotRunKind
from ...models.ProjectRole import ProjectRoleAction
from ...models.SettingRole import SettingRoleAction
from ..CardVisibilityPolicy import card_visibility_scope_by_project
from .CardService import CardService


SOURCES = ("chat", "schedule", "workflow", "log", "mcp")
MODELS = (InternalBotRun, BotScheduleRun, WorkflowEffectRun, BotLog, McpToolExecution)


def _queries(service, db, user, board, channel):
    contexts = service._get_service(CardService).resolve_work_visibility_contexts(
        user, channel, project_ids={int(board.id)}
    )
    visible_card = and_(
        Card.project_id == board.id, Card.deleted_at.is_(None), card_visibility_scope_by_project(contexts)
    )
    chat = (
        select(InternalBotRun.id)
        .join(ChatSession, ChatSession.id == InternalBotRun.chat_session_id)
        .join(
            ProjectChatSession,
            ProjectChatSession.chat_session_id == ChatSession.id,
        )
        .join(ChatHistory, ChatHistory.id == InternalBotRun.chat_history_id)
        .where(
            InternalBotRun.project_id == board.id,
            InternalBotRun.user_id == user.id,
            InternalBotRun.kind == InternalBotRunKind.BoardChat,
            ChatSession.user_id == user.id,
            ProjectChatSession.project_id == board.id,
            ChatHistory.chat_session_id == ChatSession.id,
            ChatHistory.is_received.is_(False),
            ChatHistory.deleted_at.is_(None),
        )
    )
    queries = {"chat": chat}
    if service._authorized_app_board(user, board.id, ProjectRoleAction.Update, revocation=True):
        schedule_scopes = []
        log_scopes = []
        for schedule, log, target, scope_column in (
            (ProjectBotSchedule, ProjectBotLog, None, "project_id"),
            (ProjectColumnBotSchedule, ProjectColumnBotLog, ProjectColumn, "project_column_id"),
            (CardBotSchedule, CardBotLog, Card, "card_id"),
        ):
            scope_id = board.id if target is None else target.id
            schedule_query = (
                select(schedule.id)
                .join(BotSchedule, BotSchedule.id == schedule.bot_schedule_id)
                .join(Bot, Bot.id == BotSchedule.bot_id)
                .where(Bot.deleted_at.is_(None), getattr(schedule, scope_column) == scope_id)
            )
            log_query = select(log.id).where(
                getattr(log, scope_column) == scope_id,
                log.bot_log_id == BotLog.id,
                select(Bot.id).where(Bot.id == BotLog.bot_id, Bot.deleted_at.is_(None)).exists(),
            )
            if target is not None:
                target_filter = (
                    visible_card if target is Card else and_(target.project_id == board.id, target.deleted_at.is_(None))
                )
                schedule_query = schedule_query.join(target, getattr(schedule, scope_column) == target.id).where(
                    target_filter
                )
                log_query = log_query.join(target, getattr(log, scope_column) == target.id).where(target_filter)
            schedule_scopes.append(
                and_(
                    BotScheduleRun.scope_table == schedule.__tablename__,
                    schedule_query.where(
                        schedule.bot_schedule_id == BotScheduleRun.schedule_id, schedule.id == BotScheduleRun.scope_id
                    ).exists(),
                )
            )
            log_scopes.append(log_query.exists())
        queries["schedule"] = select(BotScheduleRun.id).where(or_(*schedule_scopes))
        queries["log"] = select(BotLog.id).where(or_(*log_scopes))
    setting = db.exec(SqlBuilder.select.table(SettingRole).where(SettingRole.user_id == user.id)).first()
    if user.is_admin and setting is not None and setting.is_granted(SettingRoleAction.WorkflowStageRead):
        queries["workflow"] = (
            select(WorkflowEffectRun.id)
            .join(
                Card,
                and_(
                    Card.id == WorkflowEffectRun.card_id,
                    Card.project_id == WorkflowEffectRun.project_id,
                ),
            )
            .join(WorkflowStageDefinition, WorkflowStageDefinition.id == WorkflowEffectRun.stage_id)
            .where(visible_card)
        )
    mcp_actor = and_(McpToolExecution.user_id == user.id, McpToolExecution.bot_id.is_(None))
    if service._authorized_app_board(user, board.id, ProjectRoleAction.Update, revocation=True):
        bot_authority = (
            select(ProjectBotScope.id)
            .join(Bot, Bot.id == ProjectBotScope.bot_id)
            .where(
                ProjectBotScope.project_id == board.id,
                ProjectBotScope.bot_id == McpToolExecution.bot_id,
                Bot.deleted_at.is_(None),
            )
            .exists()
        )
        mcp_actor = or_(mcp_actor, and_(McpToolExecution.user_id.is_(None), bot_authority))
    queries["mcp"] = select(McpToolExecution.id).where(
        McpToolExecution.project_id == board.id,
        mcp_actor,
        or_(
            McpToolExecution.card_id.is_(None),
            select(Card.id)
            .where(
                Card.id == McpToolExecution.card_id,
                visible_card,
            )
            .exists(),
        ),
        or_(
            McpToolExecution.project_column_id.is_(None),
            select(ProjectColumn.id)
            .where(
                ProjectColumn.id == McpToolExecution.project_column_id,
                ProjectColumn.project_id == board.id,
                ProjectColumn.deleted_at.is_(None),
            )
            .exists(),
        ),
    )
    return queries


def _metadata(model):
    fields = [model.id, model.created_at]
    if model is InternalBotRun:
        fields += [model.status, model.finished_at, model.lease_expires_at]
    elif model is BotScheduleRun:
        fields += [model.state, model.attempt, model.lease_expires_at]
    elif model is WorkflowEffectRun:
        fields += [model.status, model.started_at, model.finished_at, model.duration_ms, model.lease_expires_at]
    elif model is McpToolExecution:
        fields += [model.outcome, model.started_at, model.finished_at, model.duration_ms]
    else:
        fields += [model.log_type]
    return fields


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _summary(source, row):
    data = dict(zip((column.key for column in _metadata(MODELS[SOURCES.index(source)])), row))
    state = data.get("status", data.get("state", data.get("outcome", data.get("log_type"))))
    state = getattr(state, "value", state)
    lease = data.get("lease_expires_at")
    if (
        lease
        and _utc(lease) < _utc(SafeDateTime.now())
        and state in ("streaming", "resuming", "running", "dispatching")
    ):
        state = "uncertain"
    # A log severity is evidence about a message, never a completed execution.
    return {
        "uid": InfraHelper.convert_uid(data["id"]),
        "source": source,
        "status": state,
        "created_at": data["created_at"],
        **{key: data[key] for key in ("started_at", "finished_at", "duration_ms", "attempt") if key in data},
    }


def read_runs(service, user, project_uid, channel, *, before=None, limit=20, source=None, uid=None):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Invalid page size")
    with DbSession.atomic() as db:
        current = db.exec(
            SqlBuilder.select.table(User).where(User.id == user.id).execution_options(populate_existing=True)
        ).first()
        if current is None:
            return None
        board = service._authorized_app_board(current, project_uid, ProjectRoleAction.Read, revocation=True)
        if board is None:
            return None
        queries = _queries(service, db, current, board, channel)
        anchor = None
        if before is not None:
            try:
                cursor_source, cursor_uid = before.split(":")
                cursor_id = InfraHelper.convert_id(cursor_uid)
                if (
                    cursor_source not in queries
                    or db.exec(
                        queries[cursor_source].where(MODELS[SOURCES.index(cursor_source)].id == cursor_id).limit(1)
                    ).first()
                    is None
                ):
                    raise ValueError("Unavailable cursor")
                anchor = (int(cursor_id), SOURCES.index(cursor_source))
            except (ValueError, TypeError, IndexError) as exc:
                raise ValueError("Unavailable cursor") from exc
        if source is not None:
            if source not in queries:
                return None
            model = MODELS[SOURCES.index(source)]
            row = db.exec(
                SqlBuilder.select.table(model)
                .where(model.id.in_(queries[source]), model.id == InfraHelper.convert_id(uid))
                .limit(1)
            ).first()
            if row is None:
                return None
            metadata = db.exec(select(*_metadata(model)).where(model.id == row.id)).first()
            item = _summary(source, metadata)
            if source == "mcp":
                item["detail"] = {
                    "tool": row.tool,
                    "request_id": row.request_id,
                    "payload_available": False,
                    "card_uid": InfraHelper.convert_uid(row.card_id) if row.card_id else None,
                }
            elif source == "chat":
                item["detail"] = {"output_text": row.output_text, "error_message": row.error_message}
            elif source == "log":
                item["detail"] = {"messages": [message.model_dump(mode="json") for message in row.message_stack]}
            elif source == "workflow":
                item["detail"] = {
                    "effects": row.effects,
                    "result": row.result,
                    "error_code": row.error_code,
                    "card_uid": InfraHelper.convert_uid(row.card_id),
                }
            else:
                item["detail"] = {
                    "error_code": row.error_code,
                    "attempt": row.attempt,
                    "max_attempts": row.max_attempts,
                }
            return {"item": item}
        rows = []
        for name, audience in queries.items():
            rank = SOURCES.index(name)
            model = MODELS[rank]
            query = select(*_metadata(model)).where(model.id.in_(audience))
            if anchor:
                query = query.where(model.id <= anchor[0] if rank > anchor[1] else model.id < anchor[0])
            rows.extend(
                (int(row[0]), rank, _summary(name, row))
                for row in db.exec(query.order_by(model.id.desc()).limit(limit + 1)).all()
            )
        rows.sort(key=lambda row: (-row[0], row[1]))
        items = [row[2] for row in rows[:limit]]
        return {
            "items": items,
            "next_cursor": f"{items[-1]['source']}:{items[-1]['uid']}" if len(rows) > limit else None,
        }
