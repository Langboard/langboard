"""Source commits invalidate native mirrors without disclosing source content."""

import importlib
from contextlib import contextmanager
from unittest.mock import Mock
import pytest
import test_card_mirrors as mirrors
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Card, CardChangeReceipt, CardVisibilityChange, ProjectRole
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from langboard_shared.publishers import CardPublisher, ProjectPublisher
from langboard_shared.tasks.activities import CardActivityTask
from langboard_shared.tasks.bots import CardBotTask


card_service_module = importlib.import_module("langboard_shared.domain.services.factory.CardService")
mirror_board = mirrors.mirror_board


@pytest.fixture
def invalidation_board(mirror_board, monkeypatch):
    b = mirror_board
    CardChangeReceipt.__table__.create(b.engine)
    b.mirror = mirrors.link(b)[0]
    b.dispatch = Mock()
    monkeypatch.setattr(CardPublisher, "put_dispather", b.dispatch)
    monkeypatch.setattr(CardPublisher, "updated", Mock())
    b.metadata_updated = Mock()
    monkeypatch.setattr(CardPublisher, "metadata_changed", b.metadata_updated)
    monkeypatch.setattr(CardPublisher, "deleted", Mock())
    monkeypatch.setattr(ProjectPublisher, "updated", Mock())
    monkeypatch.setattr(CardActivityTask, "card_updated", Mock())
    monkeypatch.setattr(CardActivityTask, "card_deleted", Mock())
    monkeypatch.setattr(CardBotTask, "card_updated", Mock())
    monkeypatch.setattr(CardBotTask, "card_deleted", Mock())
    monkeypatch.setattr(b.service, "_get_service", lambda _: Mock())
    # SQLite has no PostgreSQL sequence function; retain the real mutation transaction.
    monkeypatch.setattr(b.service, "next_change_seq", lambda: 1)
    monkeypatch.setattr(b.service, "is_check_card", lambda _: False)
    monkeypatch.setattr(b.service, "remove_completion_checklist", Mock())
    return b


def assert_scoped(b):
    b.dispatch.assert_called_once()
    payload, targets = b.dispatch.call_args.args
    assert payload == {"uid": b.mirror.get_uid()}
    assert {target.topic_id for target in targets} == {b.destination.get_uid(), b.mirror.get_uid()}
    assert all(target.card_uids == [b.mirror.get_uid()] for target in targets)
    assert b.source.title not in str(payload) and b.source.description.content not in str(payload)
    assert b.source.get_uid() not in str(payload)


def test_source_edit_invalidates_only_references_after_commit(invalidation_board):
    b = invalidation_board
    # An unrelated native reference must never receive this source's event.
    with DbSession.use(readonly=False) as db:
        db.insert(
            Card(
                project_id=b.destination.id,
                project_column_id=b.column.id,
                title="",
                source_type="project_card",
                source_uid=b.mirror.get_uid(),
            )
        )
    with DbSession.atomic():
        b.service.update(
            b.outsider, b.source_board, b.source, {"description": EditorContentModel(content="Changed protected body")}
        )
        b.dispatch.assert_not_called()
        b.metadata_updated.assert_not_called()
    assert_scoped(b)
    b.metadata_updated.assert_called_once()
    assert mirrors.project(b, b.mirror)["content"]["content"] == "Changed protected body"


def test_source_edit_rollback_does_not_invalidate(invalidation_board):
    b = invalidation_board
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            b.service.update(
                b.outsider, b.source_board, b.source, {"description": EditorContentModel(content="Uncommitted secret")}
            )
            raise RuntimeError("rollback")
    b.dispatch.assert_not_called()
    b.metadata_updated.assert_not_called()
    assert mirrors.project(b, b.mirror)["content"]["content"] == "Original private body"


def test_source_privacy_commit_invalidates_and_redacts(invalidation_board, monkeypatch):
    b = invalidation_board
    CardVisibilityChange.__table__.create(b.engine)
    monkeypatch.setattr(b.service, "_resolve_internal_access", lambda user: user.id == b.outsider.id)
    with DbSession.use(readonly=False) as db:
        db.insert(ProjectRole(project_id=b.source_board.id, user_id=b.outsider.id, actions=["card_update"]))
    with DbSession.atomic():
        result = b.service.change_visibility(
            b.outsider,
            b.source_board,
            b.source,
            CardVisibility.Internal,
            channel=CollaborationChannel.HumanUI,
            confirmed=True,
            expected_visibility="SHARED",
        )
        assert result["visibility"] == "INTERNAL"
        b.dispatch.assert_not_called()
    assert_scoped(b)
    assert mirrors.project(b, b.mirror) == {"type": "project_card", "uid": b.source.get_uid(), "status": "forbidden"}


def test_source_removal_commit_invalidates_and_redacts(invalidation_board, monkeypatch):
    b = invalidation_board
    monkeypatch.setattr(b.service, "_dependency_children", lambda _: [])
    monkeypatch.setattr(b.service, "publish_work_states", Mock())
    b.service.repo.checkitem = Mock(get_all_started_checkitem_by_card=Mock(return_value=[]))
    b.service.repo.card_assigned_user = Mock()
    b.service.repo.card_relationship = Mock()
    monkeypatch.setattr(b.service.repo.card, "reoder_after_delete", Mock())
    monkeypatch.setattr(card_service_module.BotScopeHelper, "delete_by_scope", Mock())
    monkeypatch.setattr(card_service_module.BotScheduleHelper, "unschedule_by_scope", Mock())

    @contextmanager
    def readiness():
        with DbSession.atomic():
            yield Mock()

    monkeypatch.setattr(card_service_module, "execution_readiness_uow", readiness)
    with DbSession.atomic():
        assert b.service._delete_card(b.outsider, b.source_board, b.source)
        b.dispatch.assert_not_called()
    assert_scoped(b)
    assert mirrors.project(b, b.mirror) == {"type": "project_card", "uid": b.source.get_uid(), "status": "forbidden"}


@pytest.fixture
def access_board(invalidation_board, monkeypatch):
    from types import SimpleNamespace
    from langboard_shared.domain.models import Project
    from langboard_shared.domain.services.factory.ProjectService import ProjectService
    from langboard_shared.infrastructure.repositories.factory.ProjectAssignedUserRepository import (
        ProjectAssignedUserRepository,
    )
    from langboard_shared.infrastructure.repositories.factory.ProjectRepository import ProjectRepository
    from langboard_shared.infrastructure.repositories.factory.roles.ProjectRoleRepository import ProjectRoleRepository
    from langboard_shared.tasks.activities import ProjectActivityTask
    from langboard_shared.tasks.bots import ProjectBotTask

    b = invalidation_board
    invitation = Mock()
    invitation.get_invitation_related_data.return_value = SimpleNamespace(user_ids_should_delete={b.actor.id})
    invitation.invite_emails.return_value = True
    invitation.get_api_invited_user_list_by_project.return_value = []
    b.invitation = invitation
    b.project_service = ProjectService(
        lambda _: Mock(),
        lambda name: {"card": b.service, "project_invitation": invitation}[name],
        SimpleNamespace(
            project=ProjectRepository(None, None),
            project_assigned_user=ProjectAssignedUserRepository(None, None),
            project_user_relationship=Mock(),
            role=SimpleNamespace(project=ProjectRoleRepository(None, None)),
            checkitem=Mock(get_all_started_checkitem_by_project=Mock(return_value=[])),
        ),
    )
    for name in ("assigned_users_updated", "assigned_to_users", "user_roles_updated", "deleted"):
        monkeypatch.setattr(ProjectPublisher, name, Mock())
    monkeypatch.setattr(ProjectActivityTask, "project_assigned_users_updated", Mock())
    monkeypatch.setattr(ProjectActivityTask, "project_deleted", Mock())
    monkeypatch.setattr(ProjectBotTask, "project_deleted", Mock())
    # Another board's original and reference must not receive source-board events.
    with DbSession.use(readonly=False) as db:
        unrelated = Card(project_id=b.destination.id, project_column_id=b.column.id, title="Unrelated original")
        db.insert(unrelated)
        other_destination = Project(owner_id=b.actor.id, title="Unrelated destination")
        db.insert(other_destination)
        db.insert(
            Card(
                project_id=other_destination.id,
                project_column_id=b.column.id,
                title="",
                source_type="project_card",
                source_uid=unrelated.get_uid(),
            )
        )
    return b


@pytest.mark.parametrize("rollback", [False, True])
def test_source_membership_removal_invalidates_after_commit_and_redacts(access_board, rollback):
    b = access_board

    def revoke():
        with DbSession.atomic():
            assert b.project_service.update_assigned_users(b.outsider, b.source_board, []) is True
            b.dispatch.assert_not_called()
            if rollback:
                raise RuntimeError("rollback membership removal")

    if rollback:
        with pytest.raises(RuntimeError, match="rollback membership"):
            revoke()
        b.dispatch.assert_not_called()
        assert mirrors.project(b, b.mirror)["status"] == "available"
    else:
        revoke()
        assert_scoped(b)
        assert mirrors.project(b, b.mirror) == {
            "type": "project_card",
            "uid": b.source.get_uid(),
            "status": "forbidden",
        }


@pytest.mark.parametrize("rollback", [False, True])
def test_source_project_deletion_invalidates_after_commit_and_redacts(access_board, rollback):
    b = access_board

    def remove():
        with DbSession.atomic():
            assert b.project_service.delete(b.outsider, b.source_board) is True
            b.dispatch.assert_not_called()
            if rollback:
                raise RuntimeError("rollback project deletion")

    if rollback:
        with pytest.raises(RuntimeError, match="rollback project"):
            remove()
        b.dispatch.assert_not_called()
        assert mirrors.project(b, b.mirror)["status"] == "available"
    else:
        remove()
        assert_scoped(b)
        assert mirrors.project(b, b.mirror) == {
            "type": "project_card",
            "uid": b.source.get_uid(),
            "status": "forbidden",
        }


@pytest.mark.parametrize("rollback", [False, True])
def test_source_role_change_invalidates_after_commit_without_changing_read_policy(access_board, rollback):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction

    b = access_board

    def change_roles():
        with DbSession.atomic():
            assert b.project_service.update_user_roles(b.source_board, b.actor, [ProjectRoleAction.CardUpdate])
            b.dispatch.assert_not_called()
            if rollback:
                raise RuntimeError("rollback role change")

    if rollback:
        with pytest.raises(RuntimeError, match="rollback role"):
            change_roles()
        b.dispatch.assert_not_called()
    else:
        change_roles()
        assert_scoped(b)
    with DbSession.use(readonly=False) as db:
        role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.user_id == b.actor.id)).first()
        assert ("card_update" in role.actions) is not rollback
    # Native role editing always retains Read; it must not invent a new ACL policy.
    assert mirrors.project(b, b.mirror)["status"] == "available"


def test_source_project_invalidation_pages_scoped_sources_and_references_with_decoys(access_board, monkeypatch):
    from langboard_shared.domain.models import Project

    b = access_board
    expected_mirrors = {b.mirror.get_uid()}
    expected_source_ids = {b.source.id}
    decoy_ids = set()
    with DbSession.use(readonly=False) as db:
        # Cross a source page boundary; originals stay in their actual source board.
        for index in range(204):
            source = Card(
                project_id=b.source_board.id, project_column_id=b.source.project_column_id, title=f"Source {index}"
            )
            db.insert(source)
            expected_source_ids.add(source.id)
            mirror = Card(
                project_id=b.destination.id,
                project_column_id=b.column.id,
                title="",
                source_type="project_card",
                source_uid=source.get_uid(),
            )
            db.insert(mirror)
            expected_mirrors.add(mirror.get_uid())
        # A single source can have more references than one reference page.
        for index in range(205):
            destination = Project(owner_id=b.actor.id, title=f"Referencing destination {index}")
            db.insert(destination)
            mirror = Card(
                project_id=destination.id,
                project_column_id=b.column.id,
                title="",
                source_type="project_card",
                source_uid=b.source.get_uid(),
            )
            db.insert(mirror)
            expected_mirrors.add(mirror.get_uid())
        # Unrelated tenant rows exceed the page size and must never be materialized.
        for index in range(230):
            decoy = Card(project_id=b.destination.id, project_column_id=b.column.id, title=f"Decoy {index}")
            db.insert(decoy)
            decoy_ids.add(decoy.id)
            mirror = Card(
                project_id=b.destination.id,
                project_column_id=b.column.id,
                title="",
                source_type="project_card",
                source_uid=decoy.get_uid(),
            )
            db.insert(mirror)
            decoy_ids.add(mirror.id)

    original_exec = DbSession.exec
    queries = []

    def observe_materialized(db, statement, *args, **kwargs):
        result = original_exec(db, statement, *args, **kwargs)
        compiled = statement.compile()
        queries.append((str(compiled), compiled.params, result.all()))
        return result

    monkeypatch.setattr(DbSession, "exec", observe_materialized)
    with DbSession.atomic():
        b.service.invalidate_source_project_mirrors(b.source_board)
        b.dispatch.assert_not_called()

    source_queries = [query for query in queries if "JOIN project" not in query[0]]
    reference_queries = [query for query in queries if "JOIN project" in query[0]]
    assert len(source_queries) == 2
    assert len(reference_queries) >= 3
    materialized_sources = set()
    materialized_mirrors = set()
    source_batches = []
    for sql, parameters, rows in source_queries:
        assert "card.project_id =" in sql and "card.id >" in sql
        assert "ORDER BY card.id" in sql and "LIMIT" in sql
        assert b.source_board.id in parameters.values()
        assert len(rows) <= 200
        assert set(rows) <= expected_source_ids
        assert not set(rows) & decoy_ids
        materialized_sources.update(rows)
        source_batches.append({source_id.to_short_code() for source_id in rows})
    for sql, parameters, rows in reference_queries:
        assert "card.source_uid IN" in sql and "card.id >" in sql
        assert "ORDER BY card.id" in sql and "LIMIT" in sql
        uid_batches = [value for value in parameters.values() if isinstance(value, list)]
        assert len(uid_batches) == 1 and 0 < len(uid_batches[0]) <= 200
        assert set(uid_batches[0]) in source_batches
        assert len(rows) <= 200
        assert all(mirror.get_uid() in expected_mirrors for mirror, _ in rows)
        assert not {mirror.id for mirror, _ in rows} & decoy_ids
        materialized_mirrors.update(mirror.get_uid() for mirror, _ in rows)
    assert materialized_sources == expected_source_ids
    assert materialized_mirrors == expected_mirrors
    assert b.dispatch.call_count == len(expected_mirrors)
    assert {call.args[0]["uid"] for call in b.dispatch.call_args_list} == expected_mirrors
    assert all(set(call.args[0]) == {"uid"} for call in b.dispatch.call_args_list)
