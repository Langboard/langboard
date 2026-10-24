"""Governed card query groups are applied by native SQL before bounded pages."""

from types import SimpleNamespace
import pytest
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardAssignedUser,
    CardAttachment,
    CardComment,
    Project,
    ProjectColumn,
)
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from sqlalchemy import update
from sqlalchemy.orm import Session


@pytest.fixture
def query_cards(board):
    _, actor, project, member, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    for model in (Bot, Card, CardAssignedUser, CardAttachment, CardComment):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        foreign_column = ProjectColumn(id=99, project_id=11, name="Foreign", workflow_stage="active")
        db.add(foreign_column)
        db.flush()
        rows = []
        for i, visibility in enumerate(("SHARED", "PRIVATE", "INTERNAL", "SHARED", "PRIVATE")):
            card = Card(
                id=100 + i,
                project_id=project.id,
                project_column_id=columns[0].id,
                title="needle",
                visibility=visibility,
                created_by_user_id=actor.id if i < 3 else 2,
                owner_user_id=(actor.id if i == 1 else 2) if visibility == "PRIVATE" else None,
                updated_at=SafeDateTime(2026, 10, 20 - i),
            )
            db.add(card)
            db.flush()
            db.add(CardAssignedUser(id=300+i, project_assigned_id=member.id, card_id=card.id, user_id=actor.id))
            rows.append(card)
        db.add(
            Card(
                id=200,
                project_id=11,
                project_column_id=99,
                title="needle",
                visibility="SHARED",
                created_by_user_id=actor.id,
                updated_at=SafeDateTime(2026, 10, 22),
            )
        )
        db.commit()
    context = CardVisibilityContext(CollaborationChannel.Mcp, True, True, True, actor_user_id=actor.id)
    return actor, project, rows, context, CardRepository(None, None)


def test_groups_are_and_values_are_or_before_limit_with_equal_count(query_cards):
    actor, project, rows, context, repo = query_cards
    filters = dict(
        context=context,
        include_closed=False,
        workflow_stages=["active", "review"],
        visibility=["SHARED", "PRIVATE"],
        created_by=["me"],
        assigned_to=[actor.get_uid()],
    )
    page = repo.get_page_by_project(project, 1, **filters)
    assert [card.id for card, _ in page] == [rows[0].id, rows[1].id]
    assert repo.count_by_project(project, **filters) == 2
    found, count = repo.search_context_by_project(project, "needle", 1, paginated=True, **filters)
    assert count == 2
    assert [card.id for card, _ in found] == [rows[0].id, rows[1].id]
    more, count = repo.search_context_by_project(
        project,
        "needle",
        1,
        paginated=True,
        before_updated_at=rows[0].updated_at,
        before_card=rows[0].get_uid(),
        **filters,
    )
    assert [card.id for card, _ in more] == [rows[1].id]
    assert count == 2
    assert repo.count_by_project(project, **{**filters, "created_by": []}) == 0


def test_external_cannot_select_internal_or_someone_elses_private(query_cards):
    actor, project, rows, _, repo = query_cards
    external = CardVisibilityContext(CollaborationChannel.Mcp, True, True, False, actor_user_id=actor.id)
    found, count = repo.search_context_by_project(
        project, "needle", 25, paginated=True, context=external, visibility=["PRIVATE", "INTERNAL"], include_closed=True
    )
    assert [card.id for card, _ in found] == [rows[1].id]
    assert count == 1


def test_global_query_is_scoped_and_revocation_is_reread(query_cards):
    actor, project, rows, context, repo = query_cards
    service = CardService(lambda _: SimpleNamespace(is_employee=lambda _: False), lambda _: None, SimpleNamespace())
    contexts = service.resolve_work_visibility_contexts(actor, CollaborationChannel.Mcp)
    assert set(contexts) == {int(project.id)}
    found, count = repo.search_context_by_project(
        None, "needle", 25, context=context, contexts=contexts, paginated=True
    )
    assert all(card.project_id == project.id for card, _ in found)
    assert count == 3  # two shared cards and the actor's private card
    with DbEngine.get_main_engine().begin() as db:
        db.execute(update(Project).where(Project.id == 11).values(owner_id=actor.id))
    contexts = service.resolve_work_visibility_contexts(actor, CollaborationChannel.Mcp)
    found, count = repo.search_context_by_project(None, "needle", 1, context=context, contexts=contexts, paginated=True)
    assert found[0][0].id == 200
    assert count == 4
    with DbEngine.get_main_engine().begin() as db:
        db.execute(update(Project).where(Project.id == 11).values(owner_id=2))
    contexts = service.resolve_work_visibility_contexts(actor, CollaborationChannel.Mcp)
    found, count = repo.search_context_by_project(
        None, "needle", 25, context=context, contexts=contexts, paginated=True
    )
    assert all(card.project_id == project.id for card, _ in found)
    assert count == 3


def test_single_board_read_role_revoked_during_provider_inference(query_cards, monkeypatch):
    import importlib
    from langboard_shared.domain.models import ProjectRole
    from langboard_shared.domain.services.factory.CardCommentCompactionService import CardCommentCompactionService
    from sqlalchemy import delete
    module = importlib.import_module("langboard_shared.domain.services.factory.CardService")
    actor, project, _, _, repo = query_cards
    def infer(*_args, **_kwargs):
        with DbEngine.get_main_engine().begin() as db:
            db.execute(delete(ProjectRole).where(ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id))
        return {}
    semantic = SimpleNamespace(search_metadata_candidates=infer)
    service = CardService(lambda cls: semantic if cls is CardCommentCompactionService else SimpleNamespace(is_employee=lambda _: False),
                          lambda _: None, SimpleNamespace(card=repo))
    monkeypatch.setattr(module, "search_document_candidates", lambda *_: None)
    result = service.search_context_by_project(project, "needle", user=actor, channel=CollaborationChannel.Mcp,
                                               paginated=True)
    assert result == {"cards": [], "total_count": 0, "next_fields": None}


def test_stage_and_closed_filters_apply_to_page_and_same_count(query_cards):
    actor, project, rows, context, repo = query_cards
    with DbEngine.get_main_engine().begin() as db:
        db.execute(update(Card).where(Card.id == rows[0].id).values(archived_at=SafeDateTime.now()))
        db.execute(update(Card).where(Card.id == rows[1].id).values(project_column_id=41))
    filters = dict(context=context, visibility=["SHARED", "PRIVATE"], created_by=["me"], assigned_to=["me"],
                   workflow_stages=["active", "review"])
    assert repo.count_by_project(project, include_closed=False, **filters) == 1
    assert repo.count_by_project(project, include_closed=True, **filters) == 2
    assert repo.count_by_project(project, include_closed=True, **{**filters, "workflow_stages": ["closed"]}) == 0
    found, count = repo.search_context_by_project(project, "needle", 1, paginated=True, include_closed=False, **filters)
    assert count == 1 and [card.id for card, _ in found] == [rows[1].id]


def test_cross_board_candidates_do_not_drop_later_boards_after_first_twenty(query_cards):
    _, project, _, context, repo = query_cards
    found, count = repo.search_context_by_project(
        None, "not-in-card-text", 1, context=context,
        contexts={int(project.id): context, 11: context}, paginated=True,
        additional_card_ids=[*range(1000, 1020), 200],
    )
    assert [card.id for card, _ in found] == [200]
    assert count == 1
