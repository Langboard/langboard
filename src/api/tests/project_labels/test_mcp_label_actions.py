import os
from types import SimpleNamespace
import pytest


os.environ.setdefault("PROJECT_NAME", "langboard")
from fastmcp.exceptions import ValidationError
from langboard.mcp_tools import CardMcp, LabelMcp
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import (
    Card,
    GlobalLabel,
    Project,
    ProjectAssignedUser,
    ProjectLabel,
    ScimGroup,
    ScimGroupMember,
    UserIdentityLink,
)
from langboard_shared.domain.services.factory.ProjectLabelService import ProjectLabelService
from sqlalchemy import delete, update
from src.api.tests.card_workspace.test_card_change_counts import (  # noqa: F401
    counts,
    native_delivery,
    receipt_count,
)


def service(local=None):
    local = local or []
    created = []

    def create(actor, project, name, color, description):
        label = {"uid": "new", "name": name, "color": color, "description": description}
        local.append(label)
        created.append(label)
        return object(), label

    svc = SimpleNamespace(
        project=SimpleNamespace(get_by_id_like=lambda uid: object()),
        project_label=SimpleNamespace(get_api_list_by_project=lambda project, **kwargs: local, create=create),
    )
    return svc, created


def test_local_creation_is_denied_without_explicit_request():
    svc, created = service()
    for flag in (False, None):
        with pytest.raises(ValueError, match="EXPLICIT_USER_REQUEST"):
            LabelMcp.create_local_project_label("p", "New", object(), svc, flag)
    assert not created
    assert LabelMcp.create_local_project_label("p", "New", object(), svc, True)["created"]
    assert len(created) == 1
    assert not LabelMcp.create_local_project_label("p", " new ", object(), svc, True)["created"]
    assert len(created) == 1


def test_global_catalog_is_read_only_and_local_labels_come_first(monkeypatch):
    svc, created = service([{"uid": "local", "name": "Request", "color": "#112233", "description": "Local"}])
    global_label = GlobalLabel(name="Request", color="#445566", description="Global")
    monkeypatch.setattr(LabelMcp.InfraHelper, "get_all", lambda model: [global_label])
    result = LabelMcp.get_project_label_catalog("p", svc, query="request", limit=1)
    assert result["items"][0]["source"] == "local"
    assert result["next_offset"] == 1
    assert LabelMcp.get_project_label_catalog("p", svc, offset=1)["items"][0]["source"] == "global"
    assert not created


def test_global_selection_uses_the_shared_service_and_keeps_display_metadata():
    svc, _ = service()
    result = {
        "label": {
            "uid": "local",
            "name": "Request",
            "global_label_uid": "global",
            "global_display": {"emoji": "🏷️", "translations": {"ko": {"name": "요청"}}},
        },
        "created": True,
        "global_label_uid": "global",
    }
    calls = []
    svc.project_label.use_global = lambda *args: (calls.append(args) or result)
    actor = object()
    response = LabelMcp.use_global_project_label("p", "global", actor, svc)
    assert response["label"]["emoji"] == "🏷️"
    assert response["label"]["global_label_uid"] == "global"
    assert "global_display" not in response["label"]
    assert calls == [(actor, "p", "global")]
    svc.project_label.use_global = lambda *args: None
    with pytest.raises(ValueError, match="not found"):
        LabelMcp.use_global_project_label("p", "missing", actor, svc)


def test_attach_detach_preserve_unrelated_labels_and_are_idempotent(monkeypatch):
    project, card = object(), object()
    monkeypatch.setattr(CardMcp, "_require_task_card", lambda project_uid, card_uid, actor, service: (project, card))
    all_labels = [{"uid": uid, "name": uid} for uid in ["a", "b"]]
    current = [all_labels[0]]
    writes = []

    def update(actor, project, card, uids):
        current[:] = [label for label in all_labels if label["uid"] in uids]
        writes.append(uids)
        return True

    svc = SimpleNamespace(
        project_label=SimpleNamespace(
            get_api_list_by_project=lambda *a, **kw: all_labels, get_api_list_by_card=lambda card: current
        ),
        card=SimpleNamespace(update_labels=update),
    )
    for action in ["attach", "attach", "detach", "detach"]:
        LabelMcp.change_card_label("p", "c", "b", action, object(), svc)
    assert writes == [["a", "b"], ["a"]]
    assert current == [all_labels[0]]


def test_unknown_label_and_invalid_creation_never_write(monkeypatch):
    monkeypatch.setattr(
        CardMcp, "_require_task_card", lambda project_uid, card_uid, actor, service: (object(), object())
    )
    svc, created = service()
    svc.card = SimpleNamespace(update_labels=lambda *args: pytest.fail("Unexpected card mutation"))
    with pytest.raises(ValueError, match="Unknown local label"):
        LabelMcp.change_card_label("p", "c", "foreign-label", "attach", object(), svc)
    for name, color, description in [(" ", "#112233", ""), ("New", "red", ""), ("New", "#112233", "x" * 4001)]:
        with pytest.raises(ValueError, match="Invalid label fields"):
            LabelMcp.create_local_project_label("p", name, object(), svc, True, color, description)
    assert not created


@pytest.fixture
def native_labels(counts):  # noqa: F811
    f = counts
    for model in (ScimGroup, ScimGroupMember, UserIdentityLink):
        model.__table__.create(f.engine, checkfirst=True)
    with DbSession.atomic() as db:
        labels = [
            ProjectLabel(project_id=f.project.id, name=name, color="#112233", description="")
            for name in ("Existing", "Selected")
        ]
        for label in labels:
            db.insert(label)
    return SimpleNamespace(
        f=f, labels=labels, service=SimpleNamespace(card=f.cards, project_label=f.get(ProjectLabelService))
    )


@pytest.mark.parametrize("visibility", ["SHARED", "PRIVATE"])
def test_native_label_attach_detach_preserve_labels_and_skip_idempotent_writes(
    native_labels,
    native_delivery,  # noqa: F811
    visibility,
):
    n = native_labels
    f = n.f
    with f.engine.begin() as db:
        db.execute(
            update(Card)
            .where(Card.id == f.card.id)
            .values(visibility=visibility, owner_user_id=f.user.id if visibility == "PRIVATE" else None)
        )
    assert f.cards.update_labels(f.user, f.project, f.card, [n.labels[0].get_uid()], dispatch_effects=False)
    before = receipt_count(f)
    for action, changed, names, delta in (
        ("attach", True, {"Existing", "Selected"}, 1),
        ("attach", False, {"Existing", "Selected"}, 1),
        ("detach", True, {"Existing"}, 2),
        ("detach", False, {"Existing"}, 2),
    ):
        result = LabelMcp.change_card_label(
            f.project.get_uid(), f.card.get_uid(), n.labels[1].get_uid(), action, f.user, n.service
        )
        assert set(result) == {"labels", "changed"}
        assert result["changed"] is changed
        assert {label["name"] for label in result["labels"]} == names
        assert {label["name"] for label in n.service.project_label.get_api_list_by_card(f.card)} == names
        assert receipt_count(f) == before + delta
    with pytest.raises(ValueError, match="Unknown local label"):
        LabelMcp.change_card_label(f.project.get_uid(), f.card.get_uid(), f.card.get_uid(), "attach", f.user, n.service)
    assert receipt_count(f) == before + 2


@pytest.mark.parametrize("action", ["attach", "detach"])
@pytest.mark.parametrize(
    "denial", ["private_other_owner", "revoked_membership", "inactive", "foreign_project", "linked"]
)
def test_native_label_current_authority_denied_before_label_access(native_labels, monkeypatch, action, denial):
    n = native_labels
    f = n.f
    project_uid = f.project.get_uid()
    with f.engine.begin() as db:
        if denial == "private_other_owner":
            db.execute(
                update(Card)
                .where(Card.id == f.card.id)
                .values(visibility="PRIVATE", owner_user_id=f.other.id, created_by_user_id=f.other.id)
            )
        elif denial == "revoked_membership":
            db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.id == f.member.id))
        elif denial == "inactive":
            db.execute(update(type(f.user)).where(type(f.user).id == f.user.id).values(activated_at=None))
        elif denial == "foreign_project":
            with DbSession.atomic() as session:
                other_project = Project(owner_id=f.user.id, title="Other")
                session.insert(other_project)
            project_uid = other_project.get_uid()
        else:
            db.execute(update(Card).where(Card.id == f.card.id).values(source_type="wiki", source_uid="fixture"))

    def forbidden(*args, **kwargs):
        pytest.fail("Authority denial must precede label reads and writes")

    for owner, method in (
        (n.service.project_label, "get_api_list_by_project"),
        (n.service.project_label, "get_api_list_by_card"),
        (f.cards, "update_labels"),
    ):
        monkeypatch.setattr(owner, method, forbidden)
    error, message = (ValueError, "read-only") if denial == "linked" else (ValidationError, "CARD_UNAVAILABLE")
    with pytest.raises(error, match=message):
        LabelMcp.change_card_label(project_uid, f.card.get_uid(), n.labels[0].get_uid(), action, f.user, n.service)
    assert receipt_count(f) == 0
