"""Board enrichment reads must not hydrate assignments, labels, or authors for hidden cards."""

from datetime import timedelta
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardRelationship,
    GlobalCardRelationshipType,
    GlobalLabel,
    Project,
    ProjectAssignedUser,
    ProjectLabel,
    User,
)
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.infrastructure.repositories.factory.CardAssignedUserRepository import CardAssignedUserRepository
from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import CardRelationshipRepository
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.infrastructure.repositories.factory.ProjectLabelRepository import ProjectLabelRepository


@pytest.mark.parametrize("internal", [False, True])
def test_board_enrichment_filters_hidden_deleted_foreign_and_cold_cards(board, monkeypatch, internal):
    engine = DbEngine.get_main_engine()
    for model in (
        User,
        Bot,
        Project,
        ProjectAssignedUser,
        Card,
        GlobalLabel,
        ProjectLabel,
        CardAssignedUser,
        CardAssignedProjectLabel,
        GlobalCardRelationshipType,
        CardRelationship,
    ):
        model.__table__.create(engine, checkfirst=True)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    try:
        cutoff = SafeDateTime.now() - timedelta(days=7)
        cards = {}
        with DbSession.use(readonly=False) as db:
            user = User(firstname="Scoped", lastname="Test", email="scoped@example.invalid", password="test-only")
            db.insert(user)
            other_user = User(firstname="Other", lastname="Test", email="other@example.invalid", password="test-only")
            db.insert(other_user)
            project = Project(owner_id=user.id, title="Requested")
            foreign = Project(owner_id=user.id, title="Not requested")
            db.insert(project)
            db.insert(foreign)
            member = ProjectAssignedUser(project_id=project.id, user_id=user.id)
            db.insert(member)
            label = ProjectLabel(project_id=project.id, name="Existing", color="#123456", description="Existing label")
            db.insert(label)
            for title, fields in (
                ("Shared", {}),
                ("Internal", {"visibility": "INTERNAL"}),
                ("Own private", {"visibility": "PRIVATE", "owner_user_id": user.id, "created_by_user_id": user.id}),
                (
                    "Other private",
                    {"visibility": "PRIVATE", "owner_user_id": other_user.id, "created_by_user_id": other_user.id},
                ),
                ("Deleted", {"deleted_at": cutoff}),
                ("Foreign", {"project_id": foreign.id}),
                ("Cold archive", {"archived_at": cutoff - timedelta(seconds=1)}),
                ("Recent archive", {"archived_at": cutoff}),
            ):
                card = Card(
                    project_id=project.id, project_column_id=board[5][0].id,
                    title=title, visibility="SHARED", created_by_user_id=user.id,
                )
                for key, value in fields.items():
                    setattr(card, key, value)
                db.insert(card)
                cards[title] = card
                db.insert(CardAssignedUser(card_id=card.id, user_id=user.id, project_assigned_id=member.id))
                db.insert(CardAssignedProjectLabel(card_id=card.id, project_label_id=label.id))
            relation_type = GlobalCardRelationshipType(parent_name="Contains", child_name="Part of")
            db.insert(relation_type)
            for title, card in cards.items():
                if title == "Shared":
                    continue
                for parent, child in ((cards["Shared"], card), (card, cards["Shared"])):
                    db.insert(
                        CardRelationship(
                            relationship_type_id=relation_type.id,
                            card_id_parent=parent.id,
                            card_id_child=child.id,
                        )
                    )
        context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, internal, actor_user_id=int(user.id))
        expected = {cards[title].id for title in ("Shared", "Own private", "Recent archive")}
        if internal:
            expected.add(cards["Internal"].id)
        # Unscoped internal callers retain their original semantics.
        member_repo = CardAssignedUserRepository(None, None)
        label_repo = ProjectLabelRepository(None, None)
        unscoped_ids = {card.id for name, card in cards.items() if name not in {"Foreign", "Cold archive"}}
        assert {assignment.card_id for _, assignment in member_repo.get_all_by_project(project, cutoff)} == unscoped_ids
        assert {
            assignment.card_id for _, assignment in label_repo.get_all_card_labels_by_project(project, cutoff)
        } == unscoped_ids
        members = member_repo.get_all_by_project(project, cutoff, context=context)
        labels = label_repo.get_all_card_labels_by_project(project, cutoff, context=context)
        assert {assignment.card_id for _, assignment in members} == expected
        assert {assignment.card_id for _, assignment in labels} == expected
        assert all(value.id == user.id for value, _ in members)
        assert all(value.id == label.id for value, _ in labels)
        creators = CardRepository(None, None).get_board_creators(project, cutoff, context=context)
        assert set(creators) == expected
        assert all(creator.id == user.id for creator in creators.values())
        relation_repo = CardRelationshipRepository(None, None)
        unscoped = relation_repo.get_all_by_project(project, cutoff)
        assert len(unscoped) == 8
        relationships = relation_repo.get_all_by_project(project, cutoff, context=context)
        assert len(relationships) == 2 * (len(expected) - 1)
        assert all(edge.card_id_parent in expected and edge.card_id_child in expected for edge, _ in relationships)
        without_cutoff = relation_repo.get_all_by_project(project, context=context)
        assert len(without_cutoff) == len(relationships) + 2
    finally:
        engine.dispose()
