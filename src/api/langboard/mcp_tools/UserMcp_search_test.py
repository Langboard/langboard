"""Validate explicit search periods before invoking the native query."""

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
import pytest


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard.mcp_tools import UserMcp  # noqa: E402
from langboard_shared.core.types import SafeDateTime, SnowflakeID  # noqa: E402
from langboard_shared.domain.services.CardVisibilityPolicy import (  # noqa: E402
    CardVisibilityContext,
    CollaborationChannel,
)
from langboard_shared.domain.services.factory.CardService import CardService  # noqa: E402


def test_search_passes_timezone_aware_half_open_period_to_native_query() -> None:
    calls = []
    service = SimpleNamespace(
        card=SimpleNamespace(
            search_context_by_project=lambda project, query, **filters: calls.append((project, query, filters))
            or {"cards": [], "total_count": 0, "next_fields": None},
        )
    )
    assert UserMcp.search_project_cards(
        "project",
        " release ",
        service,
        "created_at",
        "2026-09-15T00:00:00+09:00",
        "2026-09-16T00:00:00+09:00",
        user=SEARCH_USER,
    ) == {
        "cards": [], "workflow_stages": {}, "columns": {}, "total_count": 0, "next_cursor": None, "limit": 20,
        "search_scope": {
            "project_uid": "project", "query": "release", "include_closed": False,
            "workflow_stages": None, "visibility": None, "created_by": None, "assigned_to": None,
            "date_field": "created_at", "since": "2026-09-14T15:00:00+00:00", "until": "2026-09-15T15:00:00+00:00",
            "method": "lexical", "sources": "readable_card_context_and_extracted_attachment_text", "exhaustive": False,
        },
    }
    project, query, filters = calls[0]
    assert (project, query) == ("project", "release")
    assert filters["date_field"] == "created_at"
    assert filters["since"] == datetime.fromisoformat("2026-09-15T00:00:00+09:00")
    assert filters["until"] == datetime.fromisoformat("2026-09-16T00:00:00+09:00")


@pytest.mark.parametrize(
    ("since", "until"),
    [
        ("2026-09-15T00:00:00", None),
        ("not-a-date", None),
        ("2026-09-16T00:00:00Z", "2026-09-15T00:00:00Z"),
    ],
)
def test_search_rejects_invalid_period_before_service_access(since, until) -> None:
    with pytest.raises(ValueError):
        UserMcp.search_project_cards(
            "project", "release", SimpleNamespace(), since=since, until=until, user=SEARCH_USER
        )


def test_my_work_scopes_projects_and_round_trips_keyset_cursor() -> None:
    project_uid = SnowflakeID(12).to_short_code()
    card_uid = SnowflakeID(34).to_short_code()
    card = SimpleNamespace(
        id=34, get_uid=lambda: card_uid, title="Assigned", priority="P2",
        updated_at=SafeDateTime(2026, 9, 29), deadline_at=None
    )
    project = SimpleNamespace(get_uid=lambda: project_uid, title="Readable")
    column = SimpleNamespace(get_uid=lambda: "column", name="Work")
    page = Mock(return_value=[(card, project, column, True), (card, project, column, True)])
    context = CardVisibilityContext(CollaborationChannel.Mcp, True, True, False, actor_user_id=1)
    service = SimpleNamespace(card=SimpleNamespace(
        resolve_work_visibility_contexts=Mock(return_value={12: context}),
        repo=SimpleNamespace(card=SimpleNamespace(get_my_work_page=page)),
        get_work_states=Mock(return_value={34: {"completed": False}}),
    ))
    user = SimpleNamespace(id=1)
    service.card.list_assigned_work = (
        lambda *args, **kwargs: CardService.list_assigned_work(service.card, *args, **kwargs)
    )
    first = UserMcp.list_my_work(user, service, limit=1)
    assert first["items"][0]["card_uid"] == card_uid
    assert first["items"][0]["priority"] == "P2"
    assert first["items"][0]["work_state"] == {"completed": False}
    assert page.call_args.args[1] == [project_uid] and page.call_args.args[-1] == 2
    assert page.call_args.kwargs["contexts"] == {12: context}
    service.card.resolve_work_visibility_contexts.assert_called_once_with(user, CollaborationChannel.Mcp)
    UserMcp.list_my_work(user, service, cursor=first["next_cursor"], limit=1)
    assert page.call_args.kwargs["before"][1:] == (12, 34)
    with pytest.raises(ValueError, match="not readable"):
        UserMcp.list_my_work(user, service, project_uid="hidden")
    with pytest.raises(ValueError, match="Invalid My Work cursor"):
        UserMcp.list_my_work(user, service, cursor="bad!")


def test_search_resolves_distinct_workflow_once_and_keeps_bounded_description():
    state = {"workflow_stage": "released", "completed": True, "reasons": [{"code": "recorded", "message": "verbose"}]}
    cards = [
        {"uid": str(i), "description": {"content": "match"}, "project_column_uid": "c", "work_state": state}
        for i in range(3)
    ]
    resolve = Mock(
        return_value={
            "released": {
                "key": "released",
                "counts_as_completed": True,
                "rules": [{
                    "name": "Stop timers", "is_enabled": True, "trigger": {"kind": "stage_entered"},
                    "condition_match": "all", "conditions": [], "actions": [{"kind": "timers.stop"}],
                }],
                "translations": {"ko": {"name": "완료"}},
            }
        }
    )
    columns = Mock(return_value={})
    service = SimpleNamespace(
        card=SimpleNamespace(
            search_context_by_project=Mock(
                return_value={"cards": cards, "total_count": len(cards), "next_fields": None}
            )
        ),
        workflow_stage=SimpleNamespace(get_api_by_keys=resolve),
        project_column=SimpleNamespace(get_api_workflow_context=columns),
    )
    response = UserMcp.search_project_cards("p", "match", service, user=SEARCH_USER)
    resolve.assert_called_once_with({"released"})
    columns.assert_called_once_with("p", {"c"})
    assert service.card.search_context_by_project.call_args.kwargs["include_work_state"] is True
    assert response["workflow_stages"]["released"]["rules"][0]["actions"] == [{"kind": "timers.stop"}]
    assert "translations" not in response["workflow_stages"]["released"]
    assert all(
        item["description"] == {"content": "match"}
        and item["work_state"]["completed"] is True
        and "reasons" not in item["work_state"]
        for item in response["cards"]
    )


def test_document_search_tool_returns_sources_with_aggregate_excerpt_budget():
    cards = [
        {
            "uid": str(index),
            "title": "hit",
            "document_matches": [{
                "attachment_uid": "a", "filename": "report.pdf", "snippet": "한" * 2000,
                "chunk_id": "chunk-current", "generation": "generation-current", "content_hash": "b" * 64,
                "page": 3, "pages": [3, 4], "provenance_scope": "page", "url": "https://untrusted.invalid",
            }] * 4,
        }
        for index in range(20)
    ]
    service = SimpleNamespace(
        card=SimpleNamespace(
            search_context_by_project=lambda *args, **kwargs: {
                "cards": cards,
                "total_count": len(cards),
                "next_fields": None,
            }
        ),
        project_column=SimpleNamespace(get_api_workflow_context=lambda *args: {}),
    )
    response = UserMcp.search_project_cards("board", "한", service, user=SEARCH_USER)
    matches = [match for card in response["cards"] for match in card.get("document_matches", [])]
    assert sum(len(match["snippet"]) for match in matches) == 4000
    assert all(len(match["snippet"]) <= 500 and match["attachment_uid"] == "a" for match in matches)
    assert all(len(card.get("document_matches", [])) <= 2 for card in response["cards"])
    assert all(
        match["chunk_id"] == "chunk-current" and match["generation"] == "generation-current"
        and match["content_hash"] == "b" * 64 and match["page"] == 3 and match["pages"] == [3, 4]
        and match["provenance_scope"] == "page" and "url" not in match
        for match in matches
    )


SEARCH_USER = SimpleNamespace(id=1)


@pytest.mark.parametrize("project_uid", ["board", None])
@pytest.mark.parametrize("include_closed", [False, True])
def test_search_scope_preserves_filters_without_claiming_complete_document_coverage(project_uid, include_closed):
    search = Mock(return_value={
        "cards": [{"uid": "one-card", "title": "one hit", "project_uid": "board"}],
        "total_count": 1,
        "next_fields": None,
    })
    service = SimpleNamespace(
        card=SimpleNamespace(search_context_by_project=search),
        project_column=SimpleNamespace(get_api_workflow_context=lambda *args: {}),
    )
    response = UserMcp.search_project_cards(
        project_uid, " Friday ", service, include_closed=include_closed,
        workflow_stages=["done"], visibility=["SHARED"], created_by=["author"],
        assigned_to=["assignee"], user=SEARCH_USER,
    )
    scope = response["search_scope"]
    assert scope["project_uid"] == search.call_args.args[0] == project_uid
    assert scope["query"] == search.call_args.args[1] == "Friday"
    for field in ("include_closed", "workflow_stages", "visibility", "created_by", "assigned_to", "date_field"):
        assert scope[field] == search.call_args.kwargs[field]
    assert len(response["cards"]) == 1
    assert scope["method"] == "lexical"
    assert scope["sources"] == "readable_card_context_and_extracted_attachment_text"
    assert scope["exhaustive"] is False
