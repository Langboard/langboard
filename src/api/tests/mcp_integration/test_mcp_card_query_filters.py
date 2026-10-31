"""MCP continuations bind the current actor, board and normalized filter contract."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.card_workspace.domain import CardQueryFilters
from langboard.mcp_tools import UserMcp


def search_service():
    page = {
        "cards": [{"uid": "card", "project_uid": "board", "title": "Safe"}],
        "total_count": 4,
        "next_fields": ("2026-10-20T00:00:00+00:00", "card"),
    }
    return SimpleNamespace(card=SimpleNamespace(search_context_by_project=Mock(return_value=page)))


@pytest.mark.parametrize(
    "change",
    ["actor", "board", "query", "visibility", "created_by", "assigned_to", "workflow_stages", "include_closed"],
)
def test_search_rejects_cursor_replay_under_changed_context(change):
    service = search_service()
    actor = SimpleNamespace(id=1)
    first = UserMcp.search_project_cards("board", "needle", service, user=actor)
    service.card.search_context_by_project.reset_mock()
    project, query, kwargs = "board", "needle", {"cursor": first["next_cursor"]}
    if change == "actor":
        actor = SimpleNamespace(id=2)
    elif change == "board":
        project = None
    elif change == "query":
        query = "another"
    else:
        kwargs[change] = True if change == "include_closed" else ["PRIVATE"] if change == "visibility" else ["me"]
    with pytest.raises(ValueError, match="context changed"):
        UserMcp.search_project_cards(project, query, service, user=actor, **kwargs)
    service.card.search_context_by_project.assert_not_called()


def test_search_filters_and_global_scope_reach_native_bounded_query():
    service = search_service()
    actor = SimpleNamespace(id=1)
    first = UserMcp.search_project_cards(
        None,
        "needle",
        service,
        user=actor,
        limit=2,
        visibility=["PRIVATE", "SHARED"],
        created_by=["me"],
        assigned_to=["me"],
        workflow_stages=["active", "review"],
    )
    assert first["cards"][0]["project_uid"] == "board"
    assert first["total_count"] == 4
    UserMcp.search_project_cards(
        None,
        "needle",
        service,
        user=actor,
        cursor=first["next_cursor"],
        limit=2,
        visibility=["SHARED", "PRIVATE", "SHARED"],
        created_by=["me"],
        assigned_to=["me"],
        workflow_stages=["review", "active"],
    )
    call = service.card.search_context_by_project.call_args
    assert call.args[0] is None
    assert call.kwargs["paginated"] is True and call.kwargs["limit"] == 2
    assert call.kwargs["before_card"] == "card"


@pytest.mark.parametrize(
    "filters", [{"visibility": ["UNKNOWN"]}, {"created_by": "me"}, {"assigned_to": [""]}, {"workflow_stages": [False]}]
)
def test_invalid_filter_groups_reject_before_native_query(filters):
    with pytest.raises(ValueError):
        CardQueryFilters.normalize(**filters)
