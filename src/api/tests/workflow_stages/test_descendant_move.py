"""Root-only confirmation, transitive movement and rollback use native repositories."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import Card, CardRelationship, GlobalCardRelationshipType, ProjectRole
from test_entry_effects import flow as flow  # noqa: F401


@pytest.fixture
def family(request):
    f = request.getfixturevalue("flow")
    for model in (GlobalCardRelationshipType, CardRelationship, ProjectRole):
        model.__table__.create(f.engine)
    with DbSession.atomic() as db:
        f.stage.entry_effects = ["confirm_descendant_move"]
        db.update(f.stage)
        kind = GlobalCardRelationshipType(
            parent_name="Parent", child_name="Child", machine_semantic="contains", is_system_default=True
        )
        db.insert(kind)
        children = [Card(project_id=1, project_column_id=f.old.id, title=str(i), order=i + 1) for i in range(3)]
        for child in children:
            db.insert(child)
        for parent, child in [(f.card, children[0]), (children[0], children[1]), (f.card, children[2])]:
            db.insert(CardRelationship(card_id_parent=parent.id, card_id_child=child.id, relationship_type_id=kind.id))
        db.insert(ProjectRole(project_id=1, user_id=1, actions=["read", "card_update"]))
    f.service.resolve_readable_card = lambda *_: (f.project, f.card, None)
    f.children, f.kind = children, kind
    return f


def move(f, **kwargs):
    return f.service.change_order(f.actor, f.project, f.card, 0, f.new, **kwargs)


def positions(f):
    with DbSession.atomic() as db:
        return {row.id: row.project_column_id for row in db.exec(SqlBuilder.select.table(Card)).all()}


def test_confirmation_has_no_mutation_and_includes_grandchildren(family):
    f = family
    prompt = move(f)
    assert prompt["confirmation_required"] is True
    assert set(prompt) == {"confirmation_required", "confirmation_revision"}
    assert set(positions(f).values()) == {f.old.id}
    assert not f.notifications
    assert move(f, move_descendants=True, confirmation_revision=prompt["confirmation_revision"]) is True
    assert set(positions(f).values()) == {f.new.id}
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(CardRelationship)).all()) == 3
    assert len(f.notifications) == 4


def test_decline_moves_only_root(family):
    f = family
    prompt = move(f)
    assert move(f, move_descendants=False, confirmation_revision=prompt["confirmation_revision"]) is True
    assert positions(f)[f.card.id] == f.new.id
    assert all(positions(f)[child.id] == f.old.id for child in f.children)


def test_card_with_parent_never_prompts_or_moves_its_child(family):
    f = family
    assert f.service.change_order(f.actor, f.project, f.children[0], 0, f.new) is True
    assert positions(f)[f.children[0].id] == f.new.id
    assert positions(f)[f.children[1].id] == f.old.id


def test_changed_tree_requires_new_confirmation(family):
    f = family
    prompt = move(f)
    with DbSession.atomic() as db:
        child = Card(project_id=1, project_column_id=f.old.id, title="New child")
        db.insert(child)
        db.insert(CardRelationship(card_id_parent=f.card.id, card_id_child=child.id, relationship_type_id=f.kind.id))
    again = move(f, move_descendants=True, confirmation_revision=prompt["confirmation_revision"])
    assert again["confirmation_required"] is True
    assert again["confirmation_revision"] != prompt["confirmation_revision"]
    assert set(positions(f).values()) == {f.old.id}


def test_unreadable_descendant_rejects_without_partial_move(family):
    f = family
    prompt = move(f)
    f.service.resolve_readable_card = (
        lambda *args: None if args[1].id == f.children[1].id else (f.project, f.card, None)
    )
    with pytest.raises(ValueError, match="complete card family"):
        move(f, move_descendants=True, confirmation_revision=prompt["confirmation_revision"])
    assert set(positions(f).values()) == {f.old.id}
    assert not f.notifications


def test_failure_after_root_movement_rolls_back_everything(family, monkeypatch):
    f = family
    prompt = move(f)
    original = f.service._change_order

    def fail(actor, project, card, order, target):
        if card.id == f.children[1].id:
            raise ValueError("Injected descendant failure")
        return original(actor, project, card, order, target)

    monkeypatch.setattr(f.service, "_change_order", fail)
    with pytest.raises(ValueError, match="Injected"):
        move(f, move_descendants=True, confirmation_revision=prompt["confirmation_revision"])
    assert set(positions(f).values()) == {f.old.id}
    assert not f.notifications


def test_references_do_not_make_a_family(family):
    f = family
    with DbSession.atomic() as db:
        f.kind.machine_semantic = "references"
        db.update(f.kind)
    assert move(f) is True
    assert all(positions(f)[child.id] == f.old.id for child in f.children)


def test_same_stage_reorder_does_not_prompt(family):
    f = family
    with DbSession.atomic() as db:
        f.old.workflow_stage = f.new.workflow_stage
        db.update(f.old)
    assert move(f) is True
    assert all(positions(f)[child.id] == f.old.id for child in f.children)


def test_revoked_update_role_rejects_family_move(family):
    f = family
    prompt = move(f)
    with DbSession.atomic() as db:
        role = db.exec(SqlBuilder.select.table(ProjectRole)).first()
        role.actions = ["read"]
        db.update(role)
    with pytest.raises(ValueError, match="Current card update permission"):
        move(f, move_descendants=True, confirmation_revision=prompt["confirmation_revision"])
    assert set(positions(f).values()) == {f.old.id}


def test_corrupt_descendant_cycle_visits_each_card_once(family):
    f = family
    with DbSession.atomic() as db:
        db.insert(
            CardRelationship(
                card_id_parent=f.children[1].id, card_id_child=f.children[0].id, relationship_type_id=f.kind.id
            )
        )
    prompt = move(f)
    assert move(f, move_descendants=True, confirmation_revision=prompt["confirmation_revision"]) is True
    assert set(positions(f).values()) == {f.new.id}
    assert len(f.notifications) == 4


def test_disabled_option_keeps_existing_single_card_move(family):
    f = family
    with DbSession.atomic() as db:
        f.stage.entry_effects = []
        db.update(f.stage)
    assert move(f) is True
    assert all(positions(f)[child.id] == f.old.id for child in f.children)


@pytest.mark.parametrize("kind", ["cross_board", "app_owned", "linked_mirror"])
def test_ineligible_descendant_rejects_complete_family_without_effects(family, kind):
    from langboard_shared.domain.models import CardAppOwnership
    from sqlalchemy import text

    f = family
    with DbSession.atomic() as db:
        child = db.exec(SqlBuilder.select.table(Card).where(Card.id == f.children[1].id)).first()
        if kind == "cross_board":
            db.exec(text("INSERT INTO project(id) VALUES (2)"))
            child.project_id = 2
            db.update(child)
        elif kind == "linked_mirror":
            child.source_type = Card.LINKED_RESOURCE_PROJECT_CARD
            child.source_uid = f.card.get_uid()
            db.update(child)
        else:
            db.insert(CardAppOwnership(card_id=child.id, app_key="github"))
    before = positions(f)
    prompt = move(f)
    assert set(prompt) == {"confirmation_required", "confirmation_revision"}
    assert positions(f) == before
    with pytest.raises(ValueError, match="complete card family|App-owned cards"):
        move(f, move_descendants=True, confirmation_revision=prompt["confirmation_revision"])
    assert positions(f) == before
    assert not f.notifications
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(CardRelationship)).all()) == 3
