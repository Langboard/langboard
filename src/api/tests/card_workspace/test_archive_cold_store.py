import os
from contextlib import contextmanager
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from typing import Any
import pytest
import sqlalchemy as sa
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from pydantic import SecretStr
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard.routes.board.BoardApi import get_project_cards  # noqa: E402
from langboard_shared.core.db import DbSession, EditorContentModel  # noqa: E402
from langboard_shared.core.types import SafeDateTime  # noqa: E402
from langboard_shared.domain.models import (  # noqa: E402
    Card,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardComment,
    CardRelationship,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    GlobalCardRelationshipType,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectLabel,
    User,
)
from langboard_shared.domain.services.factory.CardService import CardService  # noqa: E402
from langboard_shared.infrastructure.repositories.factory.CardAssignedUserRepository import (  # noqa: E402
    CardAssignedUserRepository,
)
from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import (  # noqa: E402
    CardRelationshipRepository,
)
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository  # noqa: E402
from langboard_shared.infrastructure.repositories.factory.CheckitemRepository import CheckitemRepository  # noqa: E402
from langboard_shared.infrastructure.repositories.factory.ChecklistRepository import ChecklistRepository  # noqa: E402
from langboard_shared.infrastructure.repositories.factory.ProjectLabelRepository import (  # noqa: E402
    ProjectLabelRepository,
)


card_service_module = import_module("langboard_shared.domain.services.factory.CardService")


class FakeCard:
    def __init__(self, card_id: int, uid: str, archived_at: SafeDateTime | None = None) -> None:
        self.id = card_id
        self._uid = uid
        self.archived_at = archived_at
        self.deadline_at = None
        self.description = SimpleNamespace(content="")
        self.is_linked_resource = False

    def get_uid(self) -> str:
        return self._uid

    def api_response(self) -> dict[str, Any]:
        return {"uid": self._uid, "archived_at": self.archived_at}

    def board_api_response(self, *, include_body: bool = True, **values: Any) -> dict[str, Any]:
        assert include_body is False
        return {"uid": self._uid, **values}


def _service(card_repository: Any, calls: dict[str, Any] | None = None) -> CardService:
    calls = calls if calls is not None else {}

    def capture(name: str):
        def inner(_project: Any, archive_visible_since: SafeDateTime, *, context=None) -> list[Any]:
            calls[name] = archive_visible_since
            return []

        return inner

    def capture_creators(_project: Any, archive_visible_since: SafeDateTime, *, context=None) -> dict[int, Any]:
        calls["creators"] = archive_visible_since
        return {}

    def capture_relationships(cards: list[Any], context: CardVisibilityContext, *, board_project_id: int):
        calls["relationships"] = ([card.id for card in cards], context, board_project_id)
        return {}

    def get_service(service_class):
        assert service_class is card_service_module.CardRelationshipService
        return SimpleNamespace(get_readable_incident_by_cards=capture_relationships)

    card_repository_methods = vars(card_repository) if isinstance(card_repository, SimpleNamespace) else {}
    card_repository_methods["get_board_creators"] = capture_creators
    repository = SimpleNamespace(
        card=SimpleNamespace(**card_repository_methods),
        card_assigned_user=SimpleNamespace(get_all_by_project=capture("members")),
        project_label=SimpleNamespace(get_all_card_labels_by_project=capture("labels")),
        checklist=SimpleNamespace(
            get_all_by_project=lambda _project, *, archive_visible_since, context: (
                calls.__setitem__("checklists", archive_visible_since),
                [],
            )[1]
        ),
        checkitem=SimpleNamespace(
            get_active_workers_by_project=lambda _project, _card=None, **kwargs: [],
        ),
    )
    return CardService(get_service, lambda _name: None, repository)


def test_board_list_passes_one_visibility_cutoff_to_all_hot_path_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(id=1, archive_visible_days=7)
    card = FakeCard(10, "visible-card")
    observed: dict[str, Any] = {}
    visibility_context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True)

    def get_board_list(
        _project: Any, cutoff: SafeDateTime, *, context: CardVisibilityContext
    ) -> list[tuple[FakeCard, int]]:
        observed["cutoff"] = cutoff
        return [(card, 2)]

    monkeypatch.setattr(card_service_module.InfraHelper, "get_by_id_like", lambda _model, value: project)

    # This test isolates the archive visibility cutoff; common work-state SQL
    # has its own batch contract tests and receives only these visible cards.
    def project_states(_service: CardService, cards: list[Any], *, context: CardVisibilityContext) -> dict[int, dict[str, Any]]:
        assert cards == [card]
        return {card.id: {"lifecycle": "active", "checklist_progress": {"total": 0, "completed": 0}}}

    monkeypatch.setattr(CardService, "get_work_states", project_states)

    def eligibility(cards: list[Any]) -> dict[int, bool]:
        assert cards == [card]
        return {card.id: True}

    # Ownership SQL has its own native contracts; this fake-card test verifies
    # the archive cutoff and passes only the selected cards to both projections.
    monkeypatch.setattr(CardService, "cross_board_eligibility", staticmethod(eligibility))
    monkeypatch.setattr(
        CardService,
        "resolve_visibility_context",
        lambda *args: (project, visibility_context),
    )
    before = SafeDateTime.now()
    result, _ = _service(SimpleNamespace(get_board_list=get_board_list), observed).get_board_snapshot(project)
    after = SafeDateTime.now()

    assert before.timestamp() - 7 * 86400 <= observed["cutoff"].timestamp() <= after.timestamp() - 7 * 86400
    assert observed["members"] == observed["cutoff"]
    assert observed["relationships"] == ([card.id], visibility_context, project.id)
    assert observed["labels"] == observed["cutoff"]
    assert observed["checklists"] == observed["cutoff"]
    assert result == [
        {
            "uid": "visible-card",
            "count_comment": 2,
            "member_uids": [],
            "relationships": [],
            "labels": [],
            "creator": None,
            "can_cross_board": True,
            "completed": False,
            "is_check_card": True,
            "checklist_total_count": 0,
            "checklist_completed_count": 0,
            "active_workers": [],
            "work_state": {"lifecycle": "active", "checklist_progress": {"total": 0, "completed": 0}},
        }
    ]


def test_board_route_passes_one_request_cutoff_to_cards_and_checklists(monkeypatch: pytest.MonkeyPatch) -> None:
    now = SafeDateTime.fromisoformat("2026-09-10T12:00:00+00:00")
    project = SimpleNamespace(archive_visible_days=7)
    calls: dict[str, SafeDateTime] = {}
    monkeypatch.setattr(SafeDateTime, "now", lambda: now)
    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _uid: project),
        app_setting=SimpleNamespace(get_api_global_relationship_list=lambda: []),
        project_column=SimpleNamespace(
            get_api_list_by_project=lambda _project, *, context: [],
            get_api_bot_scopes_by_project=lambda _project: [],
            get_api_bot_schedule_list_by_project=lambda _project, _columns: [],
        ),
        card=SimpleNamespace(
            resolve_visibility_context=lambda *args: (
                project,
                CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True),
            ),
            get_board_snapshot=lambda _project, _user_or_bot, cutoff, *, channel, phase_timings: (
                calls.__setitem__("snapshot", cutoff),
                ([], []),
            )[1],
        ),
        checklist=None,
    )

    clock = iter((0.0, 0.1, 0.3, 0.6, 1.0, 1.5))
    monkeypatch.setattr(import_module("langboard.routes.board.BoardApi"), "perf_counter", lambda: next(clock), raising=False)
    response = get_project_cards("PRIVATE_PROJECT", request=SimpleNamespace(scope={}), service=service)

    expected = now - timedelta(days=7)
    assert calls == {"snapshot": expected}
    assert response.status_code == 200
    assert response.headers["server-timing"] == (
        "board_context;dur=100.000, board_cards;dur=200.000, board_checklists;dur=300.000, "
        "board_bots;dur=400.000, board_serialize;dur=500.000"
    )
    assert b'"cards":[]' in response.body
    assert "PRIVATE_PROJECT" not in response.headers["server-timing"]


def test_archive_page_uses_archive_order_cursor_and_search_count(monkeypatch: pytest.MonkeyPatch) -> None:
    project = SimpleNamespace(id=1)
    archived_at = SafeDateTime.fromisoformat("2026-09-10T12:30:00+00:00")
    card = FakeCard(10, "archived-card", archived_at)
    column = SimpleNamespace(name="Archive")
    calls: list[tuple[Any, ...]] = []
    card_repository = SimpleNamespace(
        get_archived_page_by_project=lambda *args, **kwargs: (calls.append(args), [(card, column), (card, column)])[1],
        count_archived_by_project=lambda *args, **kwargs: (calls.append(args), 9)[1],
    )
    monkeypatch.setattr(card_service_module.InfraHelper, "get_by_id_like", lambda _model, value: project)

    monkeypatch.setattr(
        CardService,
        "resolve_visibility_context",
        lambda *args: (project, CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True)),
    )
    result = _service(card_repository).get_api_archived_page_by_project(project, 1, input_value="decision")

    assert result == (
        [{"uid": "archived-card", "archived_at": archived_at, "project_column_name": "Archive"}],
        9,
        (archived_at.isoformat(), "archived-card"),
    )
    assert calls[0] == (project, 1, None, None, "decision")
    assert calls[1] == (project, "decision")


def test_hot_queries_share_the_exact_boundary_and_hide_cold_relationship_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real SQL reads agree at the cutoff, including an edge with one cold endpoint."""

    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        Project.__table__,
        ProjectColumn.__table__,
        Card.__table__,
        CardComment.__table__,
        ProjectAssignedUser.__table__,
        CardAssignedUser.__table__,
        GlobalCardRelationshipType.__table__,
        CardRelationship.__table__,
        ProjectLabel.__table__,
        CardAssignedProjectLabel.__table__,
        Checklist.__table__,
        Checkitem.__table__,
        CheckitemTimerRecord.__table__,
    ]
    User.metadata.create_all(engine, tables=tables)
    cutoff = SafeDateTime.fromisoformat("2026-09-10T12:00:00+00:00")
    active_id, boundary_id, cold_id = 101, 102, 103

    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {
                "id": 1,
                "firstname": "Test",
                "lastname": "User",
                "email": "test@example.invalid",
                "username": "test-user",
                "password": SecretStr("unused"),
                "is_admin": False,
                "preferred_lang": "en-US",
            },
        )
        connection.execute(
            Project.__table__.insert(),
            {
                "id": 1,
                "owner_id": 1,
                "title": "Boundary project",
                "project_type": "Other",
                "archive_visible_days": 7,
            },
        )
        connection.execute(
            ProjectColumn.__table__.insert(),
            {
                "id": 10,
                "project_id": 1,
                "name": "Work",
                "order": 0,
                "is_archive": False,
                "description": "",
            },
        )
        connection.execute(
            Card.__table__.insert(),
            [
                {
                    "id": active_id,
                    "project_id": 1,
                    "project_column_id": 10,
                    "title": "Active",
                    "description": EditorContentModel(content=""),
                    "order": 0,
                    "archived_at": None,
                },
                {
                    "id": boundary_id,
                    "project_id": 1,
                    "project_column_id": 10,
                    "title": "Boundary",
                    "description": EditorContentModel(content=""),
                    "order": 1,
                    "archived_at": cutoff,
                },
                {
                    "id": cold_id,
                    "project_id": 1,
                    "project_column_id": 10,
                    "title": "Cold",
                    "description": EditorContentModel(content=""),
                    "order": 2,
                    "archived_at": cutoff - timedelta(microseconds=1),
                },
            ],
        )
        connection.execute(
            CardComment.__table__.insert(),
            [
                {"id": 800 + offset, "card_id": card_id, "user_id": 1, "content": EditorContentModel(content="Comment")}
                for offset, card_id in enumerate((active_id, boundary_id, boundary_id, cold_id))
            ],
        )
        connection.execute(
            ProjectAssignedUser.__table__.insert(),
            {"id": 20, "project_id": 1, "user_id": 1, "starred": False},
        )
        connection.execute(
            CardAssignedUser.__table__.insert(),
            [
                {"id": 201 + offset, "project_assigned_id": 20, "card_id": card_id, "user_id": 1}
                for offset, card_id in enumerate((active_id, boundary_id, cold_id))
            ],
        )
        connection.execute(
            GlobalCardRelationshipType.__table__.insert(),
            {"id": 30, "parent_name": "Parent", "child_name": "Child", "description": "Hierarchy"},
        )
        connection.execute(
            CardRelationship.__table__.insert(),
            [
                {"id": 301, "relationship_type_id": 30, "card_id_parent": active_id, "card_id_child": boundary_id},
                {"id": 302, "relationship_type_id": 30, "card_id_parent": active_id, "card_id_child": cold_id},
            ],
        )
        connection.execute(
            ProjectLabel.__table__.insert(),
            {"id": 40, "project_id": 1, "name": "Label", "color": "blue", "description": "", "order": 0},
        )
        connection.execute(
            CardAssignedProjectLabel.__table__.insert(),
            [
                {"id": 401 + offset, "card_id": card_id, "project_label_id": 40}
                for offset, card_id in enumerate((active_id, boundary_id, cold_id))
            ],
        )
        connection.execute(
            Checklist.__table__.insert(),
            [
                {"id": 501 + offset, "card_id": card_id, "title": "Checklist", "order": 0, "is_checked": False}
                for offset, card_id in enumerate((active_id, boundary_id, cold_id))
            ],
        )
        connection.execute(
            Checklist.__table__.insert(),
            {"id": 600, "card_id": active_id, "title": "Hidden", "order": 1, "is_checked": False, "is_system": True},
        )
        connection.execute(
            Checkitem.__table__.insert(),
            [
                {
                    "id": 601,
                    "checklist_id": 501,
                    "title": "Done",
                    "order": 0,
                    "is_checked": True,
                    "status": "stopped",
                    "accumulated_seconds": 0,
                },
                {
                    "id": 602,
                    "checklist_id": 501,
                    "title": "Open",
                    "order": 1,
                    "is_checked": False,
                    "status": "started",
                    "accumulated_seconds": 5,
                },
                {
                    "id": 603,
                    "checklist_id": 600,
                    "title": "Hidden",
                    "order": 0,
                    "is_checked": True,
                    "status": "stopped",
                    "accumulated_seconds": 0,
                },
                {
                    "id": 604,
                    "checklist_id": 503,
                    "title": "Cold",
                    "order": 0,
                    "is_checked": True,
                    "status": "stopped",
                    "accumulated_seconds": 0,
                },
            ],
        )
        connection.execute(
            CheckitemTimerRecord.__table__.insert(),
            {"id": 701, "checkitem_id": 602, "status": "started", "created_at": cutoff},
        )
        connection.execute(Checkitem.__table__.update().where(Checkitem.__table__.c.id == 602).values(user_id=1))

    @contextmanager
    def use_database(*, readonly: bool):
        with Session(engine, expire_on_commit=False) as session:
            yield DbSession(session, readonly=readonly)

    monkeypatch.setattr(DbSession, "use", use_database)

    def make_repo(repository: Any) -> Any:
        return repository(make_repo, lambda _name: None)

    project = 1

    with engine.begin() as connection:
        connection.execute(Card.__table__.update().values(ai_description="Large AI document 한국어 " * 4000))
    statements = []

    def capture_statement(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", capture_statement)
    cards = make_repo(CardRepository).get_board_list(
        project, cutoff, context=CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True)
    )
    sa.event.remove(engine, "before_cursor_execute", capture_statement)
    assert len(statements) == 1
    assert "card.ai_description" not in statements[0]
    for card, _ in cards:
        assert "ai_description" in sa.inspect(card).unloaded
        assert "ai_description" not in card.board_api_response(0, [], [], [], include_body=False)
    with Session(engine) as session:
        assert session.get(Card, active_id).api_response()["ai_description"] == "Large AI document 한국어 " * 4000
    members = make_repo(CardAssignedUserRepository).get_all_by_project(project, cutoff)
    relationships = make_repo(CardRelationshipRepository).get_all_by_project(project, cutoff)
    labels = make_repo(ProjectLabelRepository).get_all_card_labels_by_project(project, cutoff)
    checklists = make_repo(ChecklistRepository).get_all_by_project(project, cutoff)
    active_workers = make_repo(CheckitemRepository).get_active_workers_by_project(project)

    assert {card.id for card, _ in cards} == {active_id, boundary_id}
    assert {int(card.id): count for card, count in cards} == {active_id: 1, boundary_id: 2}
    assert {assignment.card_id for _, assignment in members} == {active_id, boundary_id}
    assert {relationship.id for relationship, _ in relationships} == {301}
    assert {assignment.card_id for _, assignment in labels} == {active_id, boundary_id}
    assert {checklist.card_id for checklist in checklists} == {active_id, boundary_id}
    assert [(int(item.id), int(checklist.card_id), started_at) for item, checklist, started_at in active_workers] == [
        (602, active_id, cutoff)
    ]
    state_repo = make_repo(CheckitemRepository)
    assert state_repo.get_work_state_counts([active_id, boundary_id]) == {active_id: (2, 1, 1, 0)}
    # An explicitly authorized archived card retains a timer diagnostic even
    # though avatar queries exclude it. No other card is discovered by this read.
    with engine.begin() as connection:
        connection.execute(
            Checkitem.__table__.update()
            .where(Checkitem.__table__.c.id == 604)
            .values(status="started", is_checked=False, user_id=1)
        )
    assert state_repo.get_work_state_counts([cold_id]) == {cold_id: (1, 0, 1, 0)}
    with engine.begin() as connection:
        connection.execute(Checkitem.__table__.update().where(Checkitem.__table__.c.id == 604).values(user_id=None))
    assert state_repo.get_work_state_counts([cold_id]) == {cold_id: (1, 0, 0, 0)}
    assert state_repo.get_work_state_counts([]) == {}

    # Explicit archive pages must enforce visibility before pagination/counts,
    # independently of the recent archive cutoff used by the active board.
    with engine.begin() as connection:
        connection.execute(
            Card.__table__.update()
            .where(Card.__table__.c.id == active_id)
            .values(archived_at=cutoff, deleted_at=cutoff)
        )
        connection.execute(
            Card.__table__.update()
            .where(Card.__table__.c.id == boundary_id)
            .values(visibility="PRIVATE", owner_user_id=1, created_by_user_id=1)
        )
        connection.execute(Card.__table__.update().where(Card.__table__.c.id == cold_id).values(visibility="SHARED"))
    repository = make_repo(CardRepository)
    external = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, False, actor_user_id=2)
    owner = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True, actor_user_id=1)
    assert [card.id for card, _ in repository.get_archived_page_by_project(project, 1, context=external)] == [cold_id]
    assert repository.count_archived_by_project(project, context=external) == 1
    assert [card.id for card, _ in repository.get_archived_page_by_project(project, 1, context=owner)] == [
        boundary_id,
        cold_id,
    ]
    assert repository.count_archived_by_project(project, context=owner) == 2
    assert repository.count_archived_by_project(project, input_value="Boundary", context=external) == 0


def test_archive_route_forwards_actor_and_channel_before_querying() -> None:
    from langboard.routes.board.BoardApi import get_archived_project_cards

    project = SimpleNamespace(id=1)
    actor = object()
    observed = {}

    def page(*args, **kwargs):
        observed.update(kwargs)
        return [], 0, None

    service = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda _: project),
        card=SimpleNamespace(get_api_archived_page_by_project=page),
    )
    get_archived_project_cards(
        "project",
        request=SimpleNamespace(scope={"collaboration_channel": CollaborationChannel.HumanUI}),
        user_or_bot=actor,
        limit=25,
        cursor=None,
        input_value=None,
        service=service,
    )
    assert observed == {"user_or_bot": actor, "channel": CollaborationChannel.HumanUI}


def test_archive_page_denies_missing_visibility_context_before_repository(monkeypatch) -> None:
    monkeypatch.setattr(CardService, "resolve_visibility_context", lambda *args: None)
    assert _service(SimpleNamespace()).get_api_archived_page_by_project(1, 25) is None
