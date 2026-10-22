"""Source commits invalidate native mirrors without disclosing source content."""

import importlib
from contextlib import contextmanager
from unittest.mock import Mock
import pytest
import test_card_mirrors as mirrors
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Card, CardVisibilityChange, ProjectRole
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
from langboard_shared.publishers import CardPublisher, ProjectPublisher
from langboard_shared.tasks.activities import CardActivityTask
from langboard_shared.tasks.bots import CardBotTask


card_service_module = importlib.import_module("langboard_shared.domain.services.factory.CardService")
mirror_board = mirrors.mirror_board


@pytest.fixture
def invalidation_board(mirror_board, monkeypatch):
    b = mirror_board
    b.mirror = mirrors.link(b)[0]
    b.dispatch = Mock()
    monkeypatch.setattr(CardPublisher, "put_dispather", b.dispatch)
    monkeypatch.setattr(CardPublisher, "updated", Mock())
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
    assert_scoped(b)
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
