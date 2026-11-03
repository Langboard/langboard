"""Fixed phase timings preserve board/dashboard payloads and failure behavior."""

import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.routing.ApiException import NotFoundException
from langboard_shared.domain.models import Bot, User
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from starlette.requests import Request


board = import_module("langboard.routes.board.BoardApi")
dashboard = import_module("langboard.routes.dashboard.DashboardApi")


@pytest.mark.parametrize("human", [True, False])
def test_project_phase_timing_preserves_payload_without_visit(monkeypatch, human):
    ticks = iter([10.0, 10.1, 10.3, 10.35])
    monkeypatch.setattr(board, "perf_counter", lambda: next(ticks))
    project = object()
    actor = (
        User(id=1, firstname="Phase", lastname="Test", email="phase@example.invalid", password="test-only")
        if human
        else Bot(
            id=2,
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
            name="Phase test",
            bot_uname="phase-test",
            app_api_token="test-only",
        )
    )
    details = {"uid": "private-project", "title": "Private project title"}
    service = SimpleNamespace(
        project=SimpleNamespace(
            get_details=Mock(return_value=(project, details)),
            get_api_bot_scope_list=Mock(return_value=[{"scope": "private-scope"}]),
            get_api_bot_schedule_list=Mock(return_value=[{"schedule": "private-schedule"}]),
            set_last_view=Mock(),
        )
    )
    response = board.get_project("private-project", user_or_bot=actor, service=service)
    assert json.loads(response.body) == {
        "project": details,
        "project_bot_scopes": [{"scope": "private-scope"}],
        "project_bot_schedules": [{"schedule": "private-schedule"}],
    }
    assert response.headers["Server-Timing"] == (
        "project_details;dur=100.000, project_bots;dur=200.000, project_serialize;dur=50.000"
    )
    service.project.get_details.assert_called_once_with(actor, "private-project", is_setting=False)
    service.project.set_last_view.assert_not_called()


def test_missing_project_still_stops_before_bots_or_visit():
    project = SimpleNamespace(get_details=Mock(return_value=None), get_api_bot_scope_list=Mock(), set_last_view=Mock())
    with pytest.raises(NotFoundException):
        board.get_project("missing", user_or_bot=object(), service=SimpleNamespace(project=project))
    project.get_api_bot_scope_list.assert_not_called()
    project.set_last_view.assert_not_called()


def test_dashboard_phase_timing_preserves_authorized_project_scope(monkeypatch):
    ticks = iter([10.0, 10.2, 10.3, 10.35])
    monkeypatch.setattr(dashboard, "perf_counter", lambda: next(ticks))
    user = object()
    projects = [{"uid": "private-project", "title": "Private project title"}]
    columns = [{"uid": "private-column", "count": 5}]
    service = SimpleNamespace(
        project=SimpleNamespace(get_api_list=Mock(return_value=(projects, [123]))),
        project_column=SimpleNamespace(get_api_list_by_project=Mock(return_value=columns)),
        card=SimpleNamespace(resolve_work_visibility_contexts=Mock(return_value={})),
    )
    response = dashboard.get_projects(Request({"type": "http"}), user=user, service=service)
    assert json.loads(response.body) == {"projects": projects, "columns": columns}
    assert response.headers["Server-Timing"] == (
        "dashboard_projects;dur=200.000, dashboard_columns;dur=100.000, dashboard_serialize;dur=50.000"
    )
    service.project.get_api_list.assert_called_once_with(user, contexts={})
    service.project_column.get_api_list_by_project.assert_called_once_with([123], contexts={})


def test_dashboard_query_failure_does_not_continue_or_return_partial_data():
    columns = Mock()
    service = SimpleNamespace(
        project=SimpleNamespace(get_api_list=Mock(side_effect=RuntimeError("database unavailable"))),
        card=SimpleNamespace(resolve_work_visibility_contexts=Mock(return_value={})),
        project_column=SimpleNamespace(get_api_list_by_project=columns),
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        dashboard.get_projects(Request({"type": "http"}), user=object(), service=service)
    columns.assert_not_called()
