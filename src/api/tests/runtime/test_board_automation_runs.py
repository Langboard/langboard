"""Native audiences, bounded metadata, stable cursors, and explicit detail HTTP reads."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from langboard.middlewares import ApiAuthMiddleware, RoleMiddleware
from langboard.routes.board.BoardAutomationRunsApi import get_board_automation_run, get_board_automation_runs
from langboard_shared.core.db import ChatContentModel, DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    BotLog,
    BotSchedule,
    BotScheduleRun,
    Card,
    CardBotLog,
    CardBotSchedule,
    ChatHistory,
    ChatSession,
    InternalBot,
    InternalBotRun,
    ProjectBotLog,
    ProjectBotSchedule,
    ProjectChatSession,
    ProjectColumnBotLog,
    ProjectColumnBotSchedule,
    SettingRole,
    User,
    WorkflowEffectRun,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.BotLog import BotLogMessage, BotLogType
from langboard_shared.domain.models.BotSchedule import BotScheduleStatus
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.InternalBotRun import InternalBotRunKind
from langboard_shared.domain.services.factory.BoardAutomationRuns import read_runs
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.helpers import MiddlewareHelper
from langboard_shared.security import RoleSecurity
from sqlalchemy import delete, event, update
from sqlalchemy.orm import Session


@pytest.fixture
def executions(board):
    service, user, project, member, role, columns, stages = board
    engine = DbEngine.get_main_engine()
    for model in (
        Bot,
        BotLog,
        BotSchedule,
        BotScheduleRun,
        Card,
        CardBotLog,
        CardBotSchedule,
        ChatSession,
        ChatHistory,
        InternalBot,
        InternalBotRun,
        ProjectChatSession,
        ProjectColumnBotLog,
        ProjectColumnBotSchedule,
        ProjectBotLog,
        ProjectBotSchedule,
        SettingRole,
        WorkflowEffectRun,
    ):
        model.__table__.create(engine, checkfirst=True)
    card_service = CardService(lambda _: None, lambda _: None, None)
    card_service._resolve_internal_access = lambda _: True
    service._get_service = lambda _: card_service
    with Session(engine, expire_on_commit=False) as db:
        internal_bot = InternalBot(
            id=999,
            display_name="Native",
            bot_type=InternalBotType.ProjectChat,
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        bot = Bot(
            id=100,
            name="Native",
            bot_uname="native",
            app_api_token="test-only",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Endpoint,
        )
        session = ChatSession(id=101, user_id=user.id)
        project_session = ProjectChatSession(id=102, chat_session_id=session.id, project_id=project.id)
        history = ChatHistory(id=103, chat_session_id=session.id, message=ChatContentModel(content="PRIVATE QUESTION"))
        card = Card(id=104, title="Visible", project_id=project.id, project_column_id=columns[0].id)
        schedule = BotSchedule(id=105, bot_id=bot.id, interval_str="* * * * *", status=BotScheduleStatus.Started)
        association = ProjectBotSchedule(id=106, project_id=project.id, bot_schedule_id=schedule.id)
        chat = InternalBotRun(
            id=200,
            request_key="test-owned",
            request_digest="digest",
            client_task_id="task",
            user_id=user.id,
            project_id=project.id,
            internal_bot_id=999,
            chat_session_id=session.id,
            chat_history_id=history.id,
            kind=InternalBotRunKind.BoardChat,
            request_payload={"secret": "REQUEST SECRET"},
            output_text="PRIVATE OUTPUT",
        )
        dispatch = BotScheduleRun(
            id=201,
            schedule_id=schedule.id,
            scope_table=association.__tablename__,
            scope_id=association.id,
            occurrence_key="occurrence",
            state="accepted",
            error_code="SECRET DIAGNOSTIC",
        )
        workflow = WorkflowEffectRun(
            id=202,
            stage_id=stages[0].id,
            stage_key=stages[0].key,
            stage_revision="revision",
            project_id=project.id,
            card_id=card.id,
            actor_id=user.id,
            actor_type="user",
            source="manual",
            status="accepted",
            effects=["bot:PRIVATE"],
            duration_ms=0,
            result={"accepted": 1},
        )
        log = BotLog(
            id=203,
            bot_id=bot.id,
            log_type=BotLogType.Error,
            message_stack=[BotLogMessage(message="PRIVATE LOG", log_type=BotLogType.Error)],
        )
        project_log = ProjectBotLog(id=204, project_id=project.id, bot_log_id=log.id)
        for row in (
            internal_bot,
            bot,
            session,
            project_session,
            history,
            card,
            schedule,
            association,
            chat,
            dispatch,
            workflow,
            log,
            project_log,
        ):
            db.add(row)
            db.flush()
        db.commit()
        db.expunge_all()
    return SimpleNamespace(
        service=service,
        user=user,
        project=project,
        role=role,
        card=card,
        chat=chat,
        dispatch=dispatch,
        workflow=workflow,
        log=log,
        association=association,
        session=session,
    )


def read(fixture, **kwargs):
    return read_runs(fixture.service, fixture.user, fixture.project.get_uid(), CollaborationChannel.HumanUI, **kwargs)


def grant(fixture, *, update_role=True, admin=False, workflow=False):
    with DbSession.use(readonly=False) as db:
        fixture.role.actions = ["read", "update"] if update_role else ["read"]
        db.update(fixture.role)
        fixture.user.is_admin = admin
        db.update(fixture.user)
        if workflow:
            db.insert(SettingRole(user_id=fixture.user.id, actions=["workflow_stage_read"]))


def test_read_users_only_see_owned_board_chat_and_explicit_output(executions):
    page = read(executions)
    assert [(item["source"], item["status"]) for item in page["items"]] == [("chat", "accepted")]
    assert "PRIVATE" not in str(page) and "SECRET" not in str(page)
    detail = read(executions, source="chat", uid=executions.chat.get_uid())
    assert detail["item"]["detail"] == {"output_text": "PRIVATE OUTPUT", "error_message": None}
    assert read(executions, source="log", uid=executions.log.get_uid()) is None
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBotRun).values(user_id=2))
    assert read(executions)["items"] == []
    assert read(executions, source="chat", uid=executions.chat.get_uid()) is None


def test_mixed_paging_is_stable_metadata_only_and_sql_bounded(executions):
    grant(executions, admin=True, workflow=True)
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    engine = DbEngine.get_main_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = read(executions, limit=2)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [row["source"] for row in page["items"]] == ["log", "workflow"]
    assert not any(
        secret in str(page) for secret in ("PRIVATE", "SECRET", "request_payload", "message_stack", "output_text")
    )
    summary_statements = [
        statement
        for statement in statements
        if "ORDER BY" in statement and "bot" in statement or "ORDER BY workflow_effect_run" in statement
    ]
    assert len(summary_statements) == 4
    assert all("LIMIT" in statement for statement in summary_statements)
    assert all(
        "output_text" not in statement and "request_payload" not in statement and "message_stack" not in statement
        for statement in summary_statements
    )
    second = read(executions, limit=2, before=page["next_cursor"])
    assert [row["source"] for row in second["items"]] == ["schedule", "chat"]
    assert second["items"][0]["status"] == "accepted" and second["next_cursor"] is None
    assert (
        read(executions, source="log", uid=executions.log.get_uid())["item"]["detail"]["messages"][0]["message"]
        == "PRIVATE LOG"
    )


@pytest.mark.parametrize("cursor", ["invalid", "log:00000000001", "workflow:00000000001", "chat:00000000001"])
def test_cursor_must_belong_to_current_authorized_audience(executions, cursor):
    with pytest.raises(ValueError):
        read(executions, before=cursor)


def test_revocation_deletion_kind_and_session_hide_both_cursor_and_detail(executions):
    grant(executions)
    cursor = f"log:{executions.log.get_uid()}"
    grant(executions, update_role=False)
    with pytest.raises(ValueError):
        read(executions, before=cursor)
    assert read(executions, source="log", uid=executions.log.get_uid()) is None
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBotRun).values(kind=InternalBotRunKind.EditorChat))
    assert read(executions)["items"] == []
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBotRun).values(kind=InternalBotRunKind.BoardChat))
        db.exec(delete(ChatSession).where(ChatSession.id == executions.session.id))
    assert read(executions)["items"] == []
    assert read(executions, source="chat", uid=executions.chat.get_uid()) is None


def test_workflow_requires_admin_setting_role_and_current_card_visibility(executions):
    grant(executions, workflow=True)
    assert "workflow" not in [row["source"] for row in read(executions)["items"]]
    grant(executions, admin=True)
    assert "workflow" in [row["source"] for row in read(executions)["items"]]
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(Card)
            .where(Card.id == executions.card.id)
            .values(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
        )
    assert "workflow" not in [row["source"] for row in read(executions)["items"]]
    assert read(executions, source="workflow", uid=executions.workflow.get_uid()) is None
    with pytest.raises(ValueError):
        read(executions, before=f"workflow:{executions.workflow.get_uid()}")


def test_deleted_schedule_and_bot_hide_retained_receipts(executions):
    grant(executions)
    with DbSession.use(readonly=False) as db:
        db.exec(delete(ProjectBotSchedule).where(ProjectBotSchedule.id == executions.association.id))
    assert "schedule" not in [row["source"] for row in read(executions)["items"]]
    assert read(executions, source="schedule", uid=executions.dispatch.get_uid()) is None
    with DbSession.use(readonly=False) as db:
        db.exec(update(Bot).values(deleted_at=SafeDateTime.now()))
    assert "log" not in [row["source"] for row in read(executions)["items"]]


def test_expired_running_lease_is_uncertain_without_promoting_acceptance(executions):
    grant(executions, admin=True, workflow=True)
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(WorkflowEffectRun).values(
                status="running", lease_expires_at=SafeDateTime.now() - timedelta(minutes=1)
            )
        )
    rows = {row["source"]: row for row in read(executions)["items"]}
    assert rows["workflow"]["status"] == "uncertain"
    assert rows["schedule"]["status"] == rows["chat"]["status"] == "accepted"
    assert rows["log"]["status"] == "error"


def test_http_routes_require_auth_and_read_before_lazy_service(monkeypatch):
    user = User.model_construct(id=1, is_admin=False)
    service = Mock()
    authorized = {"value": False}

    def authenticate(scope, **kwargs):
        if not any(name == b"authorization" for name, value in scope["headers"]):
            return 401
        scope["auth"] = user
        return user

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", authenticate)
    monkeypatch.setattr(RoleSecurity, "is_authorized", lambda *args: authorized["value"])
    import langboard.routes.board.BoardAutomationRunsApi as route_module

    native_read = Mock(return_value={"items": [], "next_cursor": None})
    monkeypatch.setattr(route_module, "read_runs", native_read)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    for route in AppRouter.api.routes:
        if isinstance(route, APIRoute) and route.endpoint in (get_board_automation_runs, get_board_automation_run):
            dependency = next(item.call for item in route.dependant.dependencies if item.name == "service")
            app.dependency_overrides[dependency] = lambda: service
    client = TestClient(app)
    path = "/board/00000000001/automation/runs"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer fixture"}).status_code == 403
    native_read.assert_not_called()
    authorized["value"] = True
    response = client.get(path, headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert client.get(path + "?limit=51", headers={"Authorization": "Bearer fixture"}).status_code == 400
    assert client.get(path + "?before=invalid", headers={"Authorization": "Bearer fixture"}).status_code == 400


def test_equal_ids_use_source_tie_break_and_cursor_does_not_skip(executions):
    grant(executions, admin=True, workflow=True)
    with DbSession.use(readonly=False) as db:
        db.exec(delete(ProjectBotLog))
        db.exec(update(BotLog).values(id=200))
        db.insert(ProjectBotLog(project_id=executions.project.id, bot_log_id=200))
        db.exec(update(BotScheduleRun).values(id=200))
        db.exec(update(WorkflowEffectRun).values(id=200))
    names = []
    cursor = None
    while True:
        page = read(executions, limit=1, before=cursor)
        names.extend(item["source"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert names == ["chat", "schedule", "workflow", "log"]


def test_removed_read_or_membership_and_deleted_board_fail_closed(executions):
    from langboard_shared.domain.models import Project, ProjectAssignedUser

    with DbSession.use(readonly=False) as db:
        db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == executions.user.id))
    assert read(executions) is None
    assert read(executions, source="chat", uid=executions.chat.get_uid()) is None
    with DbSession.use(readonly=False) as db:
        db.exec(update(Project).where(Project.id == executions.project.id).values(deleted_at=SafeDateTime.now()))
    assert read(executions) is None


def test_native_http_summary_detail_and_revocation(executions, monkeypatch):
    user = executions.user

    def authenticate(scope, **kwargs):
        scope["auth"] = user
        return user

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", authenticate)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    for route in AppRouter.api.routes:
        if isinstance(route, APIRoute) and route.endpoint in (get_board_automation_runs, get_board_automation_run):
            dependency = next(item.call for item in route.dependant.dependencies if item.name == "service")
            app.dependency_overrides[dependency] = lambda: SimpleNamespace(workflow_stage=executions.service)
    client = TestClient(app)
    path = f"/board/{executions.project.get_uid()}/automation/runs"
    summary = client.get(path)
    assert summary.status_code == 200
    assert "PRIVATE" not in summary.text
    details = client.get(f"{path}/chat/{executions.chat.get_uid()}")
    assert details.status_code == 200 and "PRIVATE OUTPUT" in details.text
    with DbSession.use(readonly=False) as db:
        executions.role.actions = []
        db.update(executions.role)
    assert client.get(path).status_code == 403
    assert client.get(f"{path}/chat/{executions.chat.get_uid()}").status_code == 403


@pytest.mark.parametrize("scope", ["column", "card"])
def test_column_and_card_receipts_require_live_scope_and_visibility(executions, scope):
    from langboard_shared.domain.models import ProjectColumn

    grant(executions)
    association_model, log_model, field, target = (
        (ProjectColumnBotSchedule, ProjectColumnBotLog, "project_column_id", ProjectColumn)
        if scope == "column"
        else (CardBotSchedule, CardBotLog, "card_id", Card)
    )
    target_id = 40 if scope == "column" else executions.card.id
    with DbSession.use(readonly=False) as db:
        association = association_model(bot_schedule_id=105, **{field: target_id})
        db.insert(association)
        db.exec(update(BotScheduleRun).values(scope_table=association.__tablename__, scope_id=association.id))
        db.exec(delete(ProjectBotLog))
        db.insert(log_model(bot_log_id=executions.log.id, **{field: target_id}))
    assert {row["source"] for row in read(executions)["items"]} == {"chat", "schedule", "log"}
    with DbSession.use(readonly=False) as db:
        if scope == "card":
            db.exec(
                update(Card)
                .where(Card.id == target_id)
                .values(visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
            )
        else:
            db.exec(update(target).where(target.id == target_id).values(deleted_at=SafeDateTime.now()))
    assert {row["source"] for row in read(executions)["items"]} == {"chat"}
    assert read(executions, source="schedule", uid=executions.dispatch.get_uid()) is None
    assert read(executions, source="log", uid=executions.log.get_uid()) is None


def test_actual_foreign_cursor_and_detail_are_denied_before_page(executions):
    with DbSession.use(readonly=False) as db:
        db.exec(update(InternalBotRun).values(project_id=11))
    assert read(executions)["items"] == []
    assert read(executions, source="chat", uid=executions.chat.get_uid()) is None
    with pytest.raises(ValueError):
        read(executions, before=f"chat:{executions.chat.get_uid()}")
