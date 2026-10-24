"""Expose ordinary native comment replay after commit and HTTP response loss."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langboard.routes.board import BoardCardCommentApi as api
from langboard.routes.board.forms.Comment import CreateCardCommentForm
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardChangeReceipt,
    CardComment,
    CardMetadata,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.MetadataService import MetadataService
from langboard_shared.infrastructure.repositories.factory.CardCommentRepository import CardCommentRepository
from langboard_shared.infrastructure.repositories.factory.MetadataRepository import MetadataRepository
from langboard_shared.publishers import CardCommentPublisher
from langboard_shared.tasks.activities import CardCommentActivityTask
from langboard_shared.tasks.bots import CardCommentBotTask
from sqlalchemy import delete, text, update
from test_card_mirrors import mirror_board as mirror_board


pytestmark = pytest.mark.parametrize("mirror_board", [None, "postgresql-test"], indirect=True)


def persisted_comments():
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(CardComment, with_deleted=True)).all()


@pytest.fixture
def ordinary_comments(mirror_board, monkeypatch):  # noqa: F811 - imported native fixture
    b = mirror_board
    CardComment.__table__.create(b.engine)
    CardMetadata.__table__.create(b.engine)
    CardChangeReceipt.__table__.create(b.engine)
    if b.engine.dialect.name == "postgresql":
        with b.engine.begin() as db:
            db.execute(text("CREATE SEQUENCE content_change_seq"))
    b.notifications = Mock()
    notifications = SimpleNamespace(notify_mentioned_in_comment=b.notifications)
    b.comments = CardCommentService(
        lambda cls: b.service if cls is CardService else notifications,
        lambda _: None,
        SimpleNamespace(card_comment=CardCommentRepository(None, None)),
    )
    b.effects = [Mock(), Mock(), Mock()]
    for owner, method, effect in zip(
        (CardCommentPublisher, CardCommentActivityTask, CardCommentBotTask),
        ("created", "card_comment_added", "card_comment_added"),
        b.effects,
    ):
        monkeypatch.setattr(owner, method, effect)
    b.fail_response = False
    app = FastAPI()

    @app.post("/comment")
    def post(form: CreateCardCommentForm, request: Request):
        response = api.add_card_comment(
            b.source_board.get_uid(),
            b.source.get_uid(),
            request,
            form,
            b.actor,
            SimpleNamespace(card_comment=b.comments, card=b.service),
        )
        # Service has returned after its transaction commits; the transport loses
        # the response before the caller can know the create succeeded.
        if b.fail_response:
            assert len(persisted_comments()) == 1
            assert all(effect.call_count == 1 for effect in b.effects)
            b.fail_response = False
            raise ConnectionResetError("response lost after committed comment")
        return response

    with TestClient(app) as client:
        b.client = client
        yield b


def test_plain_comment_without_request_identity_remains_a_new_write(ordinary_comments):
    b = ordinary_comments
    for _ in range(2):
        response = b.client.post("/comment", json={"content": "Plain comment"})
        assert response.status_code == 201 and response.json() == {}
    assert len(persisted_comments()) == 2
    assert all(effect.call_count == 2 for effect in b.effects)


@pytest.mark.parametrize("anchor", [None, {"exact": "Original private body"}])
def test_same_request_after_committed_response_loss_has_one_write(ordinary_comments, anchor):
    b = ordinary_comments
    payload = {"content": "Replay me", "request_uid": str(uuid4()), "anchor": anchor}
    b.fail_response = True
    with pytest.raises(ConnectionResetError, match="response lost after committed comment"):
        b.client.post("/comment", json=payload)
    before = persisted_comments()
    assert len(before) == 1
    response = b.client.post("/comment", json=payload)
    assert response.status_code == 201
    after = persisted_comments()
    assert len(after) == 1, {
        "request_uid": payload["request_uid"],
        "before_uids": [row.get_uid() for row in before],
        "after_uids": [row.get_uid() for row in after],
        "transport_effect_counts": [effect.call_count for effect in b.effects],
        "notification_calls": b.notifications.call_count,
    }
    assert after[0].id == before[0].id
    assert all(effect.call_count == 1 for effect in b.effects)
    assert response.json() == {"comment_uid": before[0].get_uid(), "replayed": True}
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(CardChangeReceipt)).all()) == 1


def create(b, request_uid, **kwargs):
    return b.comments.create(
        kwargs.pop("actor", b.actor),
        kwargs.pop("project", b.source_board),
        kwargs.pop("card", b.source),
        {"content": kwargs.pop("content", "Replay me")},
        kwargs.pop("anchor", None),
        request_uid=request_uid,
        **kwargs,
    )


@pytest.mark.parametrize("change", ["actor", "card", "project", "content", "anchor", "dispatch_effects"])
def test_receipt_binds_actor_project_card_and_creation_payload(ordinary_comments, change):
    b = ordinary_comments
    uid = str(uuid4())
    assert create(b, uid)["replayed"] is False
    values = {
        "actor": b.outsider,
        "card": b.source,
        "project": b.source_board,
        "content": "Changed",
        "anchor": {"exact": "quote"},
        "dispatch_effects": False,
    }
    if change in ("card", "project"):
        with DbSession.use(readonly=False) as db:
            other = Card(
                project_id=b.destination.id if change == "project" else b.source_board.id,
                project_column_id=b.column.id if change == "project" else b.source.project_column_id,
                title="Other",
                visibility="SHARED",
            )
            db.insert(other)
        kwargs = {"card": other}
        if change == "project":
            kwargs["project"] = b.destination
    else:
        kwargs = {change: values[change]}
    with pytest.raises(ValueError, match="another payload"):
        create(b, uid, **kwargs)
    assert len(persisted_comments()) == 1
    assert all(effect.call_count == 1 for effect in b.effects)


@pytest.mark.parametrize("revocation", ["role", "membership", "account", "private", "card_deleted"])
def test_replay_requires_current_native_authority(ordinary_comments, revocation):
    b = ordinary_comments
    uid = str(uuid4())
    create(b, uid)
    with b.engine.begin() as db:
        if revocation == "role":
            db.execute(update(ProjectRole).values(actions=[]))
        elif revocation == "membership":
            db.execute(delete(ProjectAssignedUser))
        elif revocation == "account":
            db.execute(update(User).where(User.id == b.actor.id).values(activated_at=None))
        elif revocation == "private":
            db.execute(
                update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id)
            )
        else:
            db.execute(update(Card).where(Card.id == b.source.id).values(deleted_at=SafeDateTime.now()))
    assert create(b, uid) is None
    assert len(persisted_comments()) == 1
    assert all(effect.call_count == 1 for effect in b.effects)


def test_concurrent_same_request_commits_one_row_and_one_effect(ordinary_comments):
    b = ordinary_comments
    uid = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(b, uid), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert len({result["comment_uid"] for result in results}) == 1
    assert len(persisted_comments()) == 1
    assert all(effect.call_count == 1 for effect in b.effects)
    assert b.notifications.call_count == 1


def test_outer_rollback_discards_receipt_row_and_transport_effects(ordinary_comments):
    b = ordinary_comments
    uid = str(uuid4())
    with pytest.raises(RuntimeError, match="abort"):
        with DbSession.atomic():
            create(b, uid)
            assert all(effect.call_count == 0 for effect in b.effects)
            raise RuntimeError("abort")
    assert persisted_comments() == []
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(CardMetadata)).all() == []
        assert db.exec(SqlBuilder.select.table(CardChangeReceipt)).all() == []
    assert create(b, uid)["replayed"] is False
    assert all(effect.call_count == 1 for effect in b.effects)


def test_receipt_survives_edit_and_soft_delete_without_recreation(ordinary_comments):
    b = ordinary_comments
    uid = str(uuid4())
    first = create(b, uid)
    with DbSession.use(readonly=False) as db:
        row = persisted_comments()[0]
        row.content.content = "Edited"
        db.update(row)
    assert create(b, uid) == {**first, "replayed": True}
    with DbSession.use(readonly=False) as db:
        db.delete(persisted_comments()[0])
    assert create(b, uid) is None
    assert len(persisted_comments()) == 1
    assert all(effect.call_count == 1 for effect in b.effects)


@pytest.mark.parametrize("uid", ["bad", "", 42])
def test_supplied_invalid_request_identity_fails_closed(ordinary_comments, uid):
    with pytest.raises(ValueError, match="valid request UUID"):
        create(ordinary_comments, uid)
    assert persisted_comments() == []


def test_supplied_bot_request_identity_fails_closed(ordinary_comments):
    from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType

    with pytest.raises(ValueError, match="user actor"):
        create(
            ordinary_comments,
            str(uuid4()),
            actor=Bot(
                id=999,
                name="Test bot",
                bot_uname="fixture",
                app_api_token="test-only",
                platform=BotPlatform.Default,
                platform_running_type=BotPlatformRunningType.Default,
            ),
        )
    assert persisted_comments() == []


def test_receipt_metadata_is_hidden_protected_and_excluded_before_page_limit(ordinary_comments):
    b = ordinary_comments
    create(b, str(uuid4()))
    with DbSession.use(readonly=False) as db:
        receipt = db.exec(SqlBuilder.select.table(CardMetadata)).first()
        db.insert(CardMetadata(card_id=b.source.id, key="visible", value="Public"))
    service = MetadataService(
        lambda _: b.service, lambda _: None, SimpleNamespace(metadata=MetadataRepository(None, None))
    )
    assert service.get_by_key_as_api(CardMetadata, b.source, receipt.key) is None
    assert list(service.get_all_as_api(CardMetadata, b.source, as_dict=True, limit=1)) == ["visible"]
    for key in (receipt.key, "  " + receipt.key.upper()):
        with pytest.raises(ValueError, match="server-owned"):
            service.save(CardMetadata, b.source, key, "Changed")
        with pytest.raises(ValueError, match="server-owned"):
            service.save(CardMetadata, b.source, "visible", "Changed", old_key=key)
        with pytest.raises(ValueError, match="server-owned"):
            service.delete(CardMetadata, b.source, key)


def test_concurrent_uuid_reuse_by_different_actors_and_cards_conflicts(ordinary_comments):
    b = ordinary_comments
    uid = str(uuid4())
    with DbSession.use(readonly=False) as db:
        other = Card(project_id=b.destination.id, project_column_id=b.column.id, title="Other", visibility="SHARED")
        db.insert(other)

    def submit(index):
        try:
            if index == 0:
                return create(b, uid, actor=b.outsider)
            return create(b, uid, actor=b.actor, project=b.destination, card=other)
        except ValueError as error:
            assert "another payload" in str(error)
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results.count("conflict") == 1
    assert len(persisted_comments()) == 1
    assert all(effect.call_count == 1 for effect in b.effects)
