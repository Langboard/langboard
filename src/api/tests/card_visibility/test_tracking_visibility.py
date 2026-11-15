"""Personal work rechecks current card access before SQL pagination."""

from datetime import timedelta
from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.schema import TimeBasedPagination
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.CheckitemRepository import CheckitemRepository
from langboard_shared.infrastructure.repositories.factory.CheckitemTimerRecordRepository import (
    CheckitemTimerRecordRepository,
)
from src.api.tests.native_fixtures import TEST_PASSWORD_HASH


@pytest.fixture(autouse=True)
def external_scim_policy(monkeypatch):
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))


def work_fixture(current_card):
    actor, project, own, card_service = current_card
    for model in (Checklist, Checkitem, CheckitemTimerRecord):
        model.__table__.create(DbEngine.get_main_engine())
    now = SafeDateTime.now()
    with DbSession.use(readonly=False) as db:
        other = User(
            firstname="Other",
            lastname="Owner",
            email="other@example.invalid",
            password=TEST_PASSWORD_HASH,
            activated_at=now,
        )
        db.insert(other)
        shared = Card(project_id=project.id, title="Shared", visibility="SHARED", created_by_user_id=other.id)
        hidden = Card(
            project_id=project.id,
            title="Foreign private",
            visibility="PRIVATE",
            owner_user_id=other.id,
            created_by_user_id=other.id,
        )
        internal = Card(project_id=project.id, title="Internal", visibility="INTERNAL")
        deleted = Card(project_id=project.id, title="Deleted", visibility="SHARED", deleted_at=now)
        cards = [own, shared, hidden, internal, deleted]
        items = []
        checklists = []
        for index, card in enumerate(cards):
            if card is not own:
                db.insert(card)
            checklist = Checklist(card_id=card.id, title="Work")
            db.insert(checklist)
            checklists.append(checklist)
            item = Checkitem(
                checklist_id=checklist.id,
                user_id=actor.id,
                title=card.title,
                status=CheckitemStatus.Started,
                created_at=now - timedelta(seconds=10 - index),
            )
            db.insert(item)
            items.append(item)
        db.insert(
            Checkitem(
                checklist_id=items[0].checklist_id,
                user_id=other.id,
                title="Other worker",
                status=CheckitemStatus.Started,
            )
        )
    repo = CheckitemRepository(None, None)
    service = CheckitemService(
        lambda _: card_service,
        lambda _: None,
        SimpleNamespace(checkitem=repo, checkitem_timer_record=CheckitemTimerRecordRepository(None, None)),
    )
    return SimpleNamespace(
        actor=actor,
        other=other,
        project=project,
        own=own,
        shared=shared,
        items=items,
        checklists=checklists,
        service=service,
        repo=repo,
        refer=now,
    )


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
def test_tracking_filters_before_paging_and_active_work_uses_same_scope(current_card):
    s = work_fixture(current_card)
    seen = []
    for page in range(1, 4):
        items, cards, projects = s.service.get_tracking_list(
            s.actor, TimeBasedPagination(limit=1, page=page, refer_time=s.refer)
        )
        seen.extend(item["uid"] for item in items)
        assert len(items) == len(cards) == len(projects)
    assert seen == [s.items[1].get_uid(), s.items[0].get_uid()]
    assert {row["checkitem"]["uid"] for row in s.service.get_active_work(s.actor)} == set(seen)
    # Internal mutation lookup can still stop existing work after access is revoked.
    internal_ids = {row[0].id for row in s.repo.get_started_work_by_user(s.actor)}
    assert s.items[2].id in internal_ids and s.items[0].id in internal_ids


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "gate", ["membership", "role", "deactivated", "deleted-project", "private-transition", "deleted-checklist"]
)
def test_personal_work_drops_revoked_or_newly_private_rows(current_card, gate):
    s = work_fixture(current_card)
    with DbSession.use(readonly=False) as db:
        s.project.owner_id = s.other.id
        db.update(s.project)
        membership = ProjectAssignedUser(project_id=s.project.id, user_id=s.actor.id)
        role = ProjectRole(project_id=s.project.id, user_id=s.actor.id, actions=[ProjectRoleAction.Read])
        db.insert(membership)
        db.insert(role)
    assert len(s.service.get_active_work(s.actor)) == 2
    with DbSession.use(readonly=False) as db:
        if gate == "membership":
            db.delete(membership)
        elif gate == "role":
            role.actions = []
            db.update(role)
        elif gate == "deactivated":
            s.actor.activated_at = None
            db.update(s.actor)
        elif gate == "deleted-project":
            s.project.deleted_at = SafeDateTime.now()
            db.update(s.project)
        elif gate == "deleted-checklist":
            s.checklists[0].deleted_at = SafeDateTime.now()
            db.update(s.checklists[0])
        else:
            s.shared.visibility = "PRIVATE"
            s.shared.owner_user_id = s.other.id
            db.update(s.shared)
    expected = (
        [s.items[0].get_uid()]
        if gate == "private-transition"
        else [s.items[1].get_uid()]
        if gate == "deleted-checklist"
        else []
    )
    assert [
        x["uid"] for x in s.service.get_tracking_list(s.actor, TimeBasedPagination(refer_time=s.refer))[0]
    ] == expected
    assert [x["checkitem"]["uid"] for x in s.service.get_active_work(s.actor)] == expected
