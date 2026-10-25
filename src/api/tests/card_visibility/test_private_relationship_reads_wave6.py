"""Native database proof of PRIVATE relationship exclusion before pagination.

The fixture includes deliberately invalid legacy edges. Reads must hide these
without assuming that every stored relationship passed today's write policy.
"""

from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardRelationship,
    GlobalCardRelationshipType,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.factory.CardRelationshipService import CardRelationshipService
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import CardRelationshipRepository


@pytest.fixture
def private_edges(current_card):
    owner, project, source, cards = current_card
    for model in (GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.use(readonly=False) as db:
        other = User(firstname="Other", lastname="Member", email="edge-member@example.invalid",
                     password="test-only", activated_at=SafeDateTime.now())
        db.insert(other)
        membership = ProjectAssignedUser(project_id=project.id, user_id=other.id)
        db.insert(membership)
        db.insert(ProjectRole(project_id=project.id, user_id=other.id, actions=["read"]))
        kind = GlobalCardRelationshipType(parent_name="Parent", child_name="Child")
        db.insert(kind)
        edges = []
        targets = []
        for name, visibility, vault_owner, deleted in (
            ("Foreign vault 1", "PRIVATE", other.id, False),
            ("Foreign vault 2", "PRIVATE", other.id, False),
            ("Cross-boundary shared", "SHARED", None, False),
            ("Cross-boundary internal", "INTERNAL", None, False),
            ("Deleted vault", "PRIVATE", owner.id, True),
            ("Allowed vault 1", "PRIVATE", owner.id, False),
            ("Allowed vault 2", "PRIVATE", owner.id, False),
            ("Allowed vault 3", "PRIVATE", owner.id, False),
        ):
            target = Card(project_id=project.id, title=name, visibility=visibility,
                          owner_user_id=vault_owner, created_by_user_id=vault_owner or owner.id,
                          deleted_at=SafeDateTime.now() if deleted else None)
            db.insert(target)
            # Alternate edge directions to exercise both endpoint projections.
            parent, child = (source, target) if len(edges) % 2 else (target, source)
            edge = CardRelationship(card_id_parent=parent.id, card_id_child=child.id,
                                    relationship_type_id=kind.id)
            db.insert(edge)
            targets.append(target)
            edges.append(edge)
    repository = CardRelationshipRepository(None, None)
    service = CardRelationshipService(lambda _: cards, lambda _: None,
                                      SimpleNamespace(card_relationship=repository))
    return SimpleNamespace(owner=owner, other=other, project=project, source=source,
                           cards=cards, service=service, edges=edges, targets=targets,
                           membership=membership)


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("channel", list(CollaborationChannel))
@pytest.mark.parametrize("actor_name", ["owner", "other"])
@pytest.mark.parametrize("policy_mode", ["project_members", "scim"])
def test_private_incident_and_summary_pages_filter_before_limit(private_edges, channel, actor_name, policy_mode, monkeypatch):
    fixture = private_edges
    # SCIM's external classification is supplied by the shared service fixture;
    # actor, project membership, endpoints, edges and pagination remain native.
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: policy_mode))
    actor = getattr(fixture, actor_name)
    _, context = fixture.cards.resolve_visibility_context(fixture.project, actor, channel)
    assert context.internal_member is (policy_mode == "project_members")
    allowed = actor_name == "owner" and channel in (CollaborationChannel.HumanUI, CollaborationChannel.Mcp)
    expected = [edge.get_uid() for edge in fixture.edges[-3:]] if allowed else []
    # Every denied edge precedes the valid rows. Raw-edge limiting would starve
    # this page or expose a hidden overflow count.
    for limit in (1, 2, 3, 4, None):
        result = fixture.service.get_api_list_by_card(fixture.source, limit=limit, context=context)
        assert [row["uid"] for row in result] == expected[:limit]
    cursor = None
    seen = []
    while True:
        page = fixture.service.get_api_relation_summaries(
            fixture.project, fixture.source, actor, channel=channel, limit=1, cursor=cursor)
        if not allowed:
            assert page is None
            break
        seen.extend(item["relationship"]["uid"] for item in page["items"])
        assert len(page["items"]) == 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == expected


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("channel", [CollaborationChannel.HumanUI, CollaborationChannel.Mcp])
def test_private_relationship_reads_revalidate_stale_source_and_actor(private_edges, channel):
    fixture = private_edges
    _, context = fixture.cards.resolve_visibility_context(fixture.project, fixture.owner, channel)
    assert len(fixture.service.get_api_list_by_card(fixture.source, context=context)) == 3
    page = fixture.service.get_api_relation_summaries(
        fixture.project, fixture.source, fixture.owner, channel=channel, limit=1)
    # Preserve stale caller models and context; primary endpoint deletion wins.
    with DbSession.use(readonly=False) as db:
        target = fixture.targets[-2]
        target.deleted_at = SafeDateTime.now()
        db.update(target)
    next_page = fixture.service.get_api_relation_summaries(
        fixture.project, fixture.source, fixture.owner, channel=channel,
        limit=1, cursor=page["next_cursor"])
    assert [row["relationship"]["uid"] for row in next_page["items"]] == [fixture.edges[-1].get_uid()]
    assert next_page["next_cursor"] is None
    with DbSession.use(readonly=False) as db:
        fixture.owner.activated_at = None
        db.update(fixture.owner)
    assert fixture.service.get_api_list_by_card(fixture.source, context=context) == []
    assert fixture.service.get_api_relation_summaries(
        fixture.project, fixture.source, fixture.owner, channel=channel) is None
