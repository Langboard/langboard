"""New graph cards must remain in their anchor's existing visibility partition."""

import importlib
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from langboard.card_workspace.application.work_plan import WorkPlan, WorkPlanRejected, WorkPlanService
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Card, CardRelationship, Checkitem, Checklist, GlobalCardRelationshipType
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext
from langboard_shared.domain.services.factory.CardRelationshipService import CardRelationshipService
from langboard_shared.Env import Env
from langboard_shared.helpers import InfraHelper
from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import CardRelationshipRepository
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("visibility", ["PRIVATE", "INTERNAL", "SHARED"])
def test_graph_creation_preserves_identity_and_visible_relationship(board, monkeypatch, visibility):
    _, actor, project, _, _, columns, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    for model in (Card, GlobalCardRelationshipType, CardRelationship, Checklist, Checkitem):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        anchor = Card(project_id=project.id, project_column_id=columns[0].id, title="Private plan",
                      visibility=visibility, owner_user_id=actor.id if visibility == "PRIVATE" else None,
                      created_by_user_id=actor.id)
        kind = GlobalCardRelationshipType(parent_name="Contains", child_name="Part of",
                                          machine_semantic="contains", affects_readiness=False)
        db.insert(anchor)
        db.insert(kind)
    if visibility == "PRIVATE":
        # A caller's stale object must not widen the database anchor's partition.
        anchor.visibility = "INTERNAL"

    @contextmanager
    def readiness():
        yield SimpleNamespace(watch=lambda *_: None, watch_new=lambda *_: None)

    monkeypatch.setattr(importlib.import_module(CardRelationshipService.__module__), "execution_readiness_uow", readiness)
    repo = CardRelationshipRepository(None, None)
    service = CardRelationshipService(
        lambda _: None, lambda _: None,
        SimpleNamespace(card_relationship=repo, card=CardRepository(None, None)),
    )
    result = service.apply_graph_patch(actor, project, anchor, [("new:child", "Child", "Private scope")],
                                      [(anchor.get_uid(), "new:child", kind.get_uid())], [], dispatch_effects=False)
    child = InfraHelper.get_by_id_like(Card, result["created_cards"][0]["uid"])
    with DbSession.use(readonly=False) as db:
        edges = db.exec(SqlBuilder.select.table(CardRelationship)).all()
    assert len(edges) == 1 and edges[0].card_id_child == child.id
    context = CardVisibilityContext(channel=CollaborationChannel.Mcp, active=True, project_member=True,
                                    internal_member=True, actor_user_id=actor.id)
    # SQL persistence alone is insufficient: both normal read projections must retain the edge.
    assert len(repo.get_all_by_card(anchor, context=context)) == 1
    assert len(repo.get_all_by_card(child, context=context)) == 1
    assert child.visibility == visibility
    assert child.owner_user_id == (actor.id if visibility == "PRIVATE" else None)
    assert child.created_by_user_id == actor.id
    if visibility == "PRIVATE":
        other = CardVisibilityContext(channel=CollaborationChannel.Mcp, active=True, project_member=True,
                                      internal_member=True, actor_user_id=project.owner_id)
        assert repo.get_all_by_card(anchor, context=other) == []
        assert repo.get_all_by_card(child, context=other) == []
    plan = WorkPlan(project_uid=project.get_uid(), anchor_card_uid=anchor.get_uid(), add_edges=[{
        "parent_ref": anchor.get_uid(), "child_ref": child.get_uid(), "relationship_type_uid": kind.get_uid(),
    }])
    with pytest.raises(WorkPlanRejected) as rejected:
        WorkPlanService(actor, DomainService()).preview(plan)
    assert rejected.value.code == "WORK_PLAN_INVALID"
    assert anchor.title not in str(rejected.value)
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(CardRelationship)).all()) == 1
