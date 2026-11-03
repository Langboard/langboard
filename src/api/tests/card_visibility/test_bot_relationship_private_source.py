"""Native reproduction: a stale relationship must not promote PRIVATE into bot context."""
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Card, CardRelationship, GlobalCardRelationshipType
from langboard_shared.tasks.bots.utils.BotTaskDataHelper import BotTaskDataHelper
from langboard_shared.tasks.bots.utils.BotTaskHelper import BotTaskHelper


@pytest.mark.parametrize("direction", ["parents", "children"])
@pytest.mark.parametrize("allowed_visibility", ["SHARED", "INTERNAL"])
def test_shared_bot_context_excludes_private_related_source(current_card, direction, allowed_visibility):
    _, project, private, _ = current_card
    for model in (GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.use(readonly=False) as db:
        shared = Card(project_id=project.id, title="Shared trigger", visibility="SHARED")
        normal = Card(project_id=project.id, title="Allowed related", visibility=allowed_visibility)
        kind = GlobalCardRelationshipType(parent_name="Parent", child_name="Child")
        for row in (shared, normal, kind):
            db.insert(row)
        for related in (private, normal):
            db.insert(CardRelationship(
                relationship_type_id=kind.id,
                card_id_parent=related.id if direction == "parents" else shared.id,
                card_id_child=shared.id if direction == "parents" else related.id,
            ))
    assert BotTaskHelper.can_dispatch_card_scope({"card_uid": shared.get_uid()}, project, shared)
    context = BotTaskDataHelper.create_card_relationship_context(shared)
    assert [row["card_uid"] for row in context[direction]] == [normal.get_uid()]
    assert private.title not in repr(context)
    assert BotTaskDataHelper.create_card_relationship_context(private) == {"parents": [], "children": []}
