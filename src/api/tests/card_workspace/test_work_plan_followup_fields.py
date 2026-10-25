"""Native atomic follow-up persistence with intercepted transport, never live delivery."""

from hashlib import sha256
from json import dumps
from types import SimpleNamespace
import pytest
from langboard.card_workspace.application.work_plan import PlanCard, WorkPlan, WorkPlanService
from langboard.card_workspace.domain import WorkPlanConflict
from langboard.mcp_integration.WorkPlanOutputs import WorkPlanApplyOutput
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAssignedUser,
    CardChangeReceipt,
    CardExecutionGeneration,
    CardMetadata,
    CardRelationship,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    NotificationEmailDelivery,
    ProjectAssignedUser,
    ProjectRole,
    UserNotification,
)
from langboard_shared.domain.services import DomainService
from sqlalchemy import func, select
from src.api.tests.card_workspace.test_card_change_counts import counts as counts
from src.api.tests.card_workspace.test_card_change_counts import native_delivery as native_delivery


@pytest.fixture
def followup_board(counts, native_delivery):  # noqa: F811 - imported pytest fixtures
    f = counts
    observed = native_delivery
    Card.metadata.create_all(f.engine)
    CardExecutionGeneration.__table__.create(f.engine, checkfirst=True)
    with DbSession.atomic() as db:
        db.insert(ProjectRole(project_id=f.project.id, user_id=f.user.id, actions=["read", "card_update"]))
        db.insert(ProjectAssignedUser(project_id=f.project.id, user_id=f.other.id))
        relationship_type = GlobalCardRelationshipType(
            parent_name="Contains", child_name="Part of", is_system_default=True, machine_semantic="contains"
        )
        db.insert(relationship_type)
    service = DomainService()
    service.initialize(f.repo)
    plan = WorkPlan(
        project_uid=f.project.get_uid(),
        anchor_card_uid=f.card.get_uid(),
        new_cards=[
            {
                "client_ref": f"new:{i}",
                "title": f"Follow-up {i}",
                "description": "Complete assigned work",
                "assign_user_uids": [assignee.get_uid()],
                "deadline_at": deadline,
            }
            for i, assignee, deadline in (
                (1, f.user, "2026-10-23T23:59:00+09:00"),
                (2, f.other, "2026-10-23T14:59:00Z"),
            )
        ],
        add_edges=[
            {
                "parent_ref": f.card.get_uid(),
                "child_ref": f"new:{i}",
                "relationship_type_uid": relationship_type.get_uid(),
            }
            for i in (1, 2)
        ],
        new_checklists=[
            {"target_card_ref": f"new:{i}", "title": "Steps", "items": ["Implement", "Verify"]} for i in (1, 2)
        ],
    )
    try:
        yield SimpleNamespace(
            f=f, service=service, plan=plan, observed=observed, owner=WorkPlanService(f.user, service)
        )
    finally:
        service.close()


def row_counts(f):
    with f.engine.connect() as db:
        return {
            model.__name__: db.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (
                Card,
                CardRelationship,
                Checklist,
                Checkitem,
                CardAssignedUser,
                CardMetadata,
                CardChangeReceipt,
                UserNotification,
                NotificationEmailDelivery,
            )
        }


@pytest.mark.parametrize("failure", [None, "child", "receipt", "assignee", "deadline"])
def test_native_followup_transaction_and_replay(followup_board, monkeypatch, failure):
    b = followup_board
    before = row_counts(b.f)
    preview = b.owner.preview(b.plan)
    assert preview["plan"] == b.plan.model_dump(mode="json")
    assert row_counts(b.f) == before and not b.observed
    if failure == "child":
        original = b.service.checkitem.create
        calls = []

        def fail_later(*args, **kwargs):
            calls.append(1)
            return None if len(calls) == 4 else original(*args, **kwargs)

        monkeypatch.setattr(b.service.checkitem, "create", fail_later)
    elif failure == "receipt":
        original = b.service.metadata.save

        def fail_receipt(*args, **kwargs):
            saved = original(*args, **kwargs)
            return None if kwargs.get("internal") else saved

        monkeypatch.setattr(b.service.metadata, "save", fail_receipt)
    elif failure == "assignee":
        monkeypatch.setattr(b.service.card, "update_assigned_users", lambda *args: None)
    elif failure == "deadline":
        monkeypatch.setattr(b.service.card, "update", lambda *args: None)
    if failure:
        with pytest.raises(ValueError):
            b.owner.apply(b.plan, preview["revision"], "native-followups")
        assert row_counts(b.f) == before
        assert b.observed == []
        return
    result = b.owner.apply(b.plan, preview["revision"], "native-followups")
    assert result["all_succeeded"] is True
    WorkPlanApplyOutput.model_validate(result)
    created = result["graph"]["created_cards"]
    assert len(created) == len(result["graph"]["created_relationships"]) == len(result["checklists"]) == 2
    for proposed, response in zip(b.plan.new_cards, created, strict=True):
        card = b.service.card.get_by_id_like(response["uid"])
        assert response["member_uids"] == proposed.assign_user_uids
        assert b.service.card.get_api_assigned_user_list(card, only_uids=True) == proposed.assign_user_uids
        assert SafeDateTime.fromisoformat(response["deadline_at"]) == SafeDateTime.fromisoformat(proposed.deadline_at)
        assert SafeDateTime.fromisoformat(card.deadline_at.isoformat()) == SafeDateTime.fromisoformat(
            proposed.deadline_at
        )
        assert response["last_change_seq"] == card.last_change_seq > 0
        assert response["is_check_card"] == b.service.card.is_check_card(card)
    persisted = row_counts(b.f)
    assert persisted["Card"] == before["Card"] + 2
    assert persisted["CardRelationship"] == before["CardRelationship"] + 2
    assert persisted["CardAssignedUser"] == before["CardAssignedUser"] + 2
    # Unknown response outcome: same request returns durable receipt without new effects.
    effects = list(b.observed)
    assert effects and all(active is False for _, active, _ in effects)
    assert b.owner.apply(b.plan, preview["revision"], "native-followups") == {**result, "replayed": True}
    assert row_counts(b.f) == persisted and b.observed == effects
    changed = b.plan.model_copy(update={"new_checklists": []})
    with pytest.raises(ValueError, match="reused"):
        b.owner.apply(changed, preview["revision"], "native-followups")


@pytest.mark.parametrize("failure", ["child", "receipt"])
def test_caught_plan_failure_preserves_host_transaction_only(followup_board, monkeypatch, failure):
    b = followup_board
    reviewed = b.owner.preview(b.plan)
    before = row_counts(b.f)
    host_effects = []
    if failure == "child":
        original = b.service.checkitem.create
        calls = []

        def fail_later(*args, **kwargs):
            calls.append(1)
            return None if len(calls) == 4 else original(*args, **kwargs)

        monkeypatch.setattr(b.service.checkitem, "create", fail_later)
    else:
        original = b.service.metadata.save

        def fail_receipt(*args, **kwargs):
            original(*args, **kwargs)
            return None

        monkeypatch.setattr(b.service.metadata, "save", fail_receipt)
    with DbSession.atomic() as db:
        db.after_commit(lambda: host_effects.append("committed"))
        # The host catches this command's failure and still commits its own work.
        with pytest.raises(ValueError, match="creation failed|receipt persistence failed"):
            b.owner.apply(b.plan, reviewed["revision"], "caught-failure")
        db.insert(CardMetadata(card_id=b.f.card.id, key="host-note", value="preserved"))
    after = row_counts(b.f)
    assert after == {**before, "CardMetadata": before["CardMetadata"] + 1}
    assert host_effects == ["committed"]
    assert b.observed == []


@pytest.mark.parametrize("invalid", ["outsider", "inactive", "deleted", "revoked", "missing"])
def test_membership_validated_at_preview_and_apply(followup_board, invalid):
    b = followup_board
    preview = b.owner.preview(b.plan)
    with DbSession.atomic() as db:
        if invalid in ("outsider", "revoked"):
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == b.f.project.id, ProjectAssignedUser.user_id == b.f.other.id
                )
            ).first()
            db.delete(member)
        elif invalid in ("inactive", "deleted"):
            user = b.service.user.get_by_id_like(b.f.other.get_uid())
            if invalid == "inactive":
                user.activated_at = None
            else:
                user.deleted_at = SafeDateTime.now()
            db.update(user)
        else:
            payload = b.plan.model_dump(mode="json")
            payload["new_cards"][1]["assign_user_uids"] = ["missing-user"]
            b.plan = WorkPlan.model_validate(payload)
    before = row_counts(b.f)
    for operation in (
        lambda: b.owner.preview(b.plan),
        lambda: b.owner.apply(b.plan, preview["revision"], "invalid-member"),
    ):
        with pytest.raises(ValueError):
            operation()
        assert row_counts(b.f) == before and not b.observed


@pytest.mark.parametrize("deadline", ["", "tomorrow", "2026-99-30T12:00:00Z", "2026-10-24T25:00:00Z", 1790000000])
def test_invalid_deadline_rejected_before_writes(deadline):
    with pytest.raises(ValueError):
        PlanCard(client_ref="new:child", title="Child", deadline_at=deadline)


def test_absent_followup_fields_preserve_serialization_and_digest():
    payload = {
        "project_uid": "project",
        "anchor_card_uid": "anchor",
        "new_cards": [{"client_ref": "new:child", "title": "Child", "description": None}],
        "add_edges": [],
        "remove_relationship_uids": [],
        "cardify_checkitems": [],
        "new_checklists": [],
    }
    plan = WorkPlan.model_validate(payload)
    assert plan.model_dump(mode="json") == payload
    assert (
        sha256(dumps(plan.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        == sha256(dumps(payload, sort_keys=True).encode()).hexdigest()
    )
    assert (
        PlanCard(client_ref="new:child", title="Child", assign_user_uids=None, deadline_at=None).model_dump(mode="json")
        == payload["new_cards"][0]
    )
    with pytest.raises(ValueError, match="Duplicate"):
        PlanCard(client_ref="new:child", title="Child", assign_user_uids=["user", "user"])


@pytest.mark.parametrize("field,value", [("deadline_at", "2026-10-24T14:59:00Z"), ("assign_user_uids", [])])
def test_followup_fields_require_current_review(followup_board, field, value):
    b = followup_board
    reviewed = b.owner.preview(b.plan)
    payload = b.plan.model_dump(mode="json")
    payload["new_cards"][0][field] = value
    changed = WorkPlan.model_validate(payload)
    assert b.owner.preview(changed)["revision"] != reviewed["revision"]
    before = row_counts(b.f)
    with pytest.raises(WorkPlanConflict):
        b.owner.apply(changed, reviewed["revision"], "changed-fields")
    assert row_counts(b.f) == before and not b.observed


def test_native_naive_deadline_and_explicit_empty_assignees(followup_board):
    b = followup_board
    payload = b.plan.model_dump(mode="json")
    payload["new_cards"][0].update(assign_user_uids=[], deadline_at="2026-10-23T14:59:00")
    plan = WorkPlan.model_validate(payload)
    preview = b.owner.preview(plan)
    result = b.owner.apply(plan, preview["revision"], "naive-empty")
    response = result["graph"]["created_cards"][0]
    assert response["member_uids"] == []
    assert SafeDateTime.fromisoformat(response["deadline_at"]) == SafeDateTime.fromisoformat("2026-10-23T14:59:00Z")
