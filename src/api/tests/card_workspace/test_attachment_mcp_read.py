import os
from types import SimpleNamespace
from unittest.mock import Mock
import pytest


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard.mcp_tools import CardMcp  # noqa: E402
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401


def test_attachment_read_checks_card_ancestry_before_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    card = SimpleNamespace(id=7)
    attachment = SimpleNamespace(card_id=8, deleted_at=None)
    read_bytes = Mock(return_value=b"secret")
    monkeypatch.setattr(CardMcp, "_get_card_in_project", lambda *_: (object(), card))
    monkeypatch.setattr(CardMcp.Storage, "get_file", read_bytes)
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=Mock(return_value=(object(), card, object()))), card_attachment=SimpleNamespace(get_by_id_like=lambda _, **kwargs: attachment))

    with pytest.raises(ValueError, match="not found in card"):
        CardMcp.read_card_attachment("project", "card", "foreign", object(), service)
    read_bytes.assert_not_called()


def test_attachment_read_returns_bounded_file_object(monkeypatch: pytest.MonkeyPatch) -> None:
    card = SimpleNamespace(id=7)
    attachment = SimpleNamespace(
        card_id=7, deleted_at=None, filename="report.pdf", file=object(), get_uid=lambda: "attachment"
    )
    monkeypatch.setattr(CardMcp, "_get_card_in_project", lambda *_: (object(), card))
    monkeypatch.setattr(CardMcp.Storage, "get_file", lambda _: b"%PDF")
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=Mock(return_value=(object(), card, object()))), card_attachment=SimpleNamespace(get_by_id_like=lambda _, **kwargs: attachment))

    result = CardMcp.read_card_attachment("project", "card", "attachment", object(), service)
    assert result == {
        "attachment_uid": "attachment",
        "file_name": "report.pdf",
        "mime_type": "application/pdf",
        "size": 4,
        "file_data_base64": "JVBERg==",
    }


@pytest.mark.parametrize("changed", ["already_deleted", "deleted", "replaced", "renamed", "removed", "foreign"])
def test_deleted_attachment_never_returns_bytes(monkeypatch, changed):
    card = SimpleNamespace(id=7)
    attachment = SimpleNamespace(
        card_id=7,
        deleted_at="deleted" if changed == "already_deleted" else None,
        filename="report.pdf",
        file=object(),
        get_uid=lambda: "attachment",
    )
    monkeypatch.setattr(CardMcp, "_get_card_in_project", lambda *_: (object(), card))

    def read(_):
        if changed == "deleted":
            attachment.deleted_at = "deleted"
        elif changed == "replaced":
            attachment.file = object()
        elif changed == "renamed":
            attachment.filename = "other.pdf"
        elif changed == "foreign":
            attachment.card_id = 9
        elif changed == "removed":
            service.card_attachment.get_by_id_like = lambda _, **kwargs: None
        return b"private"

    read_bytes = Mock(side_effect=read)
    monkeypatch.setattr(CardMcp.Storage, "get_file", read_bytes)
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=Mock(return_value=(object(), card, object()))), card_attachment=SimpleNamespace(get_by_id_like=lambda _, **kwargs: attachment))
    with pytest.raises(ValueError, match="unavailable|not found"):
        CardMcp.read_card_attachment("project", "card", "attachment", object(), service)
    if changed == "already_deleted":
        read_bytes.assert_not_called()


@pytest.mark.parametrize("phase", ["before", "during"])
def test_card_visibility_revocation_never_returns_attachment_bytes(monkeypatch, phase):
    card = SimpleNamespace(id=7)
    attachment = SimpleNamespace(card_id=7, deleted_at=None, filename="proof.pdf", file=object(), get_uid=lambda: "a")
    resolver = Mock(return_value=None if phase == "before" else (object(), card, object()))
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=resolver), card_attachment=SimpleNamespace(get_by_id_like=lambda _, **kwargs: attachment))
    def read(_):
        resolver.return_value = None
        return b"secret"
    reader = Mock(side_effect=read)
    monkeypatch.setattr(CardMcp.Storage, "get_file", reader)
    with pytest.raises(ValueError, match="unavailable|not found"):
        CardMcp.read_card_attachment("p", "c", "a", object(), service)
    if phase == "before":
        reader.assert_not_called()


@pytest.mark.parametrize("change", ["unchanged", "deleted", "replaced", "membership"])
def test_attachment_read_rechecks_primary_after_storage_io(board, monkeypatch, change):  # noqa: F811
    from langboard_shared.core.db import DbSession
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.storage import FileModel
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Card, CardAttachment
    from langboard_shared.domain.services import DomainService
    from langboard_shared.Env import Env

    _, actor, project, member, _, columns, _ = board
    for model in (Card, CardAttachment):
        model.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    source = FileModel(storage_type="local", storage_name="fixture", original_filename="proof.pdf", filename="proof.pdf", path="proof.pdf")
    with DbSession.use(readonly=False) as db:
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="Primary source")
        db.insert(card)
        attachment = CardAttachment(user_id=actor.id, card_id=card.id, filename="proof.pdf", file=source)
        db.insert(attachment)

    def read(_):
        # The board fixture makes replica reads fail. Retained objects represent
        # stale source state; only committed primary changes may authorize return.
        with DbSession.use(readonly=False) as db:
            if change == "deleted":
                attachment.deleted_at = SafeDateTime.now()
                db.update(attachment)
            elif change == "replaced":
                attachment.file = source.model_copy(update={"path": "replacement.pdf"})
                db.update(attachment)
            elif change == "membership":
                db.delete(member)
        return b"%PDF private source"

    monkeypatch.setattr(CardMcp.Storage, "get_file", read)
    service = DomainService()
    args = (project.get_uid(), card.get_uid(), attachment.get_uid(), actor, service)
    if change == "unchanged":
        assert CardMcp.read_card_attachment(*args)["file_data_base64"] == "JVBERiBwcml2YXRlIHNvdXJjZQ=="
    else:
        with pytest.raises(ValueError, match="unavailable"):
            CardMcp.read_card_attachment(*args)
