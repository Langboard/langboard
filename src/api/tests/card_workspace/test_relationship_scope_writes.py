"""Both native relationship writers retain the PRIVATE pair boundary before writes."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.exceptions.RelationshipCycle import RelationshipCycle
from langboard_shared.domain.models import Card, CardRelationship, GlobalCardRelationshipType
from src.api.tests.card_workspace.test_work_plan_followup_fields import counts as counts
from src.api.tests.card_workspace.test_work_plan_followup_fields import followup_board as followup_board
from src.api.tests.card_workspace.test_work_plan_followup_fields import native_delivery as native_delivery
from src.api.tests.card_workspace.test_work_plan_followup_fields import row_counts


def endpoints(board, source_visibility, target_visibility, *, different_owner=False):
    with DbSession.atomic() as db:
        cards = []
        for index, visibility in enumerate((source_visibility, target_visibility)):
            owner = board.f.other if index and different_owner else board.f.user
            card = Card(
                project_id=board.f.project.id,
                project_column_id=board.f.column.id,
                title=f"Scope endpoint {index}",
                visibility=visibility,
                owner_user_id=owner.id if visibility == "PRIVATE" else None,
                created_by_user_id=owner.id,
            )
            db.insert(card)
            cards.append(card)
    return cards


def write(board, source, target, path, *, new_card=False):
    service = board.service.card_relationship
    kind = board.plan.add_edges[0].relationship_type_uid
    if path == "replace":
        return service.update(board.f.user, board.f.project, source, False, [(target.get_uid(), kind)])
    operation = service.preview_graph_patch if path == "preview" else service.apply_graph_patch
    edges = [(source.get_uid(), target.get_uid(), kind)]
    additions = []
    if new_card:
        additions = [("new:child", "Inherited scope", None)]
        edges = [(source.get_uid(), "new:child", kind), ("new:child", target.get_uid(), kind)]
    return operation(board.f.user, board.f.project, source, additions, edges, [])


@pytest.mark.parametrize("counts", ["sqlite"], indirect=True)
@pytest.mark.parametrize("path", ["preview", "apply", "replace"])
@pytest.mark.parametrize(
    "source,target,other",
    [
        ("INTERNAL", "PRIVATE", False),
        ("PRIVATE", "INTERNAL", False),
        ("SHARED", "PRIVATE", False),
        ("PRIVATE", "SHARED", False),
        ("PRIVATE", "PRIVATE", True),
    ],
)
def test_incompatible_pair_is_rejected_without_persistence(followup_board, path, source, target, other):  # noqa: F811
    board = followup_board
    parent, child = endpoints(board, source, target, different_owner=other)
    before = row_counts(board.f)
    with pytest.raises(ValueError, match="privacy scopes"):
        write(board, parent, child, path)
    assert row_counts(board.f) == before
    assert not board.observed


@pytest.mark.parametrize("counts", ["sqlite"], indirect=True)
@pytest.mark.parametrize("path", ["preview", "apply", "replace"])
@pytest.mark.parametrize("source,target", [("PRIVATE", "PRIVATE"), ("SHARED", "INTERNAL")])
def test_compatible_pair_remains_supported(followup_board, path, source, target):  # noqa: F811
    parent, child = endpoints(followup_board, source, target)
    assert write(followup_board, parent, child, path) is not None


@pytest.mark.parametrize("counts", ["sqlite"], indirect=True)
@pytest.mark.parametrize("path", ["preview", "apply"])
def test_new_reference_uses_anchor_scope_before_any_card_is_created(followup_board, path):  # noqa: F811
    parent, child = endpoints(followup_board, "INTERNAL", "PRIVATE")
    before = row_counts(followup_board.f)
    with pytest.raises(ValueError, match="privacy scopes"):
        write(followup_board, parent, child, path, new_card=True)
    assert row_counts(followup_board.f) == before
    assert not followup_board.observed


@pytest.mark.parametrize("counts", ["sqlite", "postgresql"], indirect=True)
@pytest.mark.parametrize("path", ["preview", "apply", "replace"])
@pytest.mark.parametrize("semantic", ["contains", "blocks", "references"])
def test_native_cycle_policy_persists_only_nonblocking_cycles(followup_board, path, semantic):  # noqa: F811
    board = followup_board
    parent, child = endpoints(board, "SHARED", "SHARED")
    with DbSession.atomic() as db:
        kind = GlobalCardRelationshipType(
            parent_name="Custom display",
            child_name="Custom display",
            machine_semantic=semantic,
            affects_readiness=semantic == "blocks",
        )
        db.insert(kind)
        edge = CardRelationship(card_id_parent=parent.id, card_id_child=child.id, relationship_type_id=kind.id)
        db.insert(edge)

    def reverse():
        service = board.service.card_relationship
        if path == "replace":
            return service.update(board.f.user, board.f.project, child, False, [(parent.get_uid(), kind.get_uid())])
        operation = service.preview_graph_patch if path == "preview" else service.apply_graph_patch
        return operation(
            board.f.user, board.f.project, child, [], [(child.get_uid(), parent.get_uid(), kind.get_uid())], []
        )

    before = row_counts(board.f)
    if semantic == "blocks":
        with pytest.raises(RelationshipCycle, match="blocks cycle"):
            reverse()
        assert row_counts(board.f) == before
        assert not board.observed
    else:
        assert reverse() is not None
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(CardRelationship)).all()
        actual = {(row.card_id_parent, row.card_id_child) for row in stored}
    expected = {(parent.id, child.id)}
    if semantic != "blocks" and path != "preview":
        expected.add((child.id, parent.id))
    assert actual == expected
