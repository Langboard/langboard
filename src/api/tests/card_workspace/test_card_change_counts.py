"""Native receipt, transaction and authorized snapshot proof on SQLite/PostgreSQL."""

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.exceptions.CardCommentConflict import CardCommentConflict
from langboard_shared.core.exceptions.CardDescriptionConflict import CardDescriptionConflict
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Card,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardAttachment,
    CardChangeReceipt,
    CardComment,
    CardContentBlock,
    CardMetadata,
    CardRelationship,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    NotificationEmailDelivery,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectLabel,
    ProjectRole,
    User,
    UserCardReadState,
    UserNotification,
)
from langboard_shared.domain.services.CardChangeTransaction import card_change_snapshot
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
from langboard_shared.domain.services.factory.CardContentBlockService import CardContentBlockService
from langboard_shared.domain.services.factory.CardRelationshipService import CardRelationshipService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.domain.services.factory.ChecklistService import ChecklistService
from langboard_shared.infrastructure.repositories import Repository
from langboard_shared.publishers import CardPublisher
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.pool import StaticPool


@pytest.fixture(params=["sqlite", "postgresql"])
def counts(request, monkeypatch):
    url = os.getenv("LANGBOARD_COUNT_TEST_DATABASE_URL")
    schema = None
    if request.param == "postgresql":
        if not url:
            pytest.skip("Set LANGBOARD_COUNT_TEST_DATABASE_URL to disposable PostgreSQL")
        schema = "card_count_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=5000"})
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = set()

    def include(table):
        if table in tables:
            return
        tables.add(table)
        for fk in table.foreign_keys:
            include(fk.column.table)

    for model in (Card, CardChangeReceipt, UserCardReadState, ProjectAssignedUser, ProjectRole,
                  CardComment, CardContentBlock, CardAttachment, Checklist, Checkitem,
                  CardAssignedUser, CardAssignedProjectLabel, ProjectLabel, CardRelationship,
                  GlobalCardRelationshipType, CardMetadata, UserNotification, NotificationEmailDelivery):
        include(model.__table__)
    Card.metadata.create_all(engine, tables=list(tables))
    if schema:
        with engine.begin() as db:
            db.execute(text("CREATE SEQUENCE content_change_seq"))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    repository = Repository()
    services = {}

    def get(cls):
        if cls not in services:
            services[cls] = cls(get, lambda name: next(s for s in services.values() if s.name() == name), repository)
        return services[cls]

    cards = get(CardService)
    with DbSession.atomic() as db:
        user = User(firstname="Count", lastname="Reader", email="reader@example.invalid", password="fixture",
                    activated_at=SafeDateTime.now())
        db.insert(user)
        other = User(firstname="Count", lastname="Owner", email="owner@example.invalid", password="fixture",
                     activated_at=SafeDateTime.now())
        db.insert(other)
        project = Project(owner_id=other.id, title="Counts")
        db.insert(project)
        column = ProjectColumn(project_id=project.id, name="Doing")
        db.insert(column)
        member = ProjectAssignedUser(project_id=project.id, user_id=user.id)
        db.insert(member)
        card = Card(project_id=project.id, project_column_id=column.id, title="Native",
                    visibility="SHARED", created_by_user_id=user.id, description=EditorContentModel(content="Before"))
        db.insert(card)
    published = Mock()
    monkeypatch.setattr(CardPublisher, "metadata_changed", published)
    # Tests isolate the count contract from broker delivery and readiness policy.
    @contextmanager
    def readiness():
        with DbSession.atomic() as db:
            yield SimpleNamespace(db=db, watch=lambda *a: None, watch_new=lambda *a: None,
                                  watch_card_and_dependents=lambda *a: None)
    for cls in (CardService, CardRelationshipService):
        monkeypatch.setattr(import_module(cls.__module__), "execution_readiness_uow", readiness)
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, False, actor_user_id=int(user.id))
    fixture = SimpleNamespace(engine=engine, cards=cards, repo=repository, get=get, user=user, other=other,
                              project=project, column=column, card=card, member=member, context=context,
                              published=published)
    fixture.state = lambda target=card: repository.user_card_read_state.get_change_state_map(
        user.id, [target.id], context=context
    )[target.id]
    try:
        yield fixture
    finally:
        engine.dispose()
        if schema:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def reread(f, model=Card, key=None):
    with DbSession.atomic() as db:
        return db.exec(SqlBuilder.select.table(model).where(model.id == (key or f.card.id))).first()


def receipt_count(f):
    with f.engine.connect() as db:
        return db.execute(select(func.count()).select_from(CardChangeReceipt)).scalar_one()


@pytest.fixture
def native_delivery(monkeypatch):
    """Keep real producers/serialization; stop only at the native transport boundary."""
    from celery.app.task import Task
    from langboard_shared.core.broadcast import DispatcherQueue
    from langboard_shared.core.broker import Broker

    observed = []

    def socket(model, data=None):
        observed.append(("socket", DbSession.has_active_transaction(), model.model_dump()))

    def celery(task, args=(), kwargs=None, **options):
        observed.append((task.name, DbSession.has_active_transaction(), (args, kwargs)))

    def start_thread(thread):
        coroutine = thread._args[0]
        observed.append((coroutine.cr_code.co_name, DbSession.has_active_transaction(),
                         dict(coroutine.cr_frame.f_locals)))
        coroutine.close()

    monkeypatch.setattr(DispatcherQueue, "put", socket)
    monkeypatch.setattr(Task, "apply_async", celery)
    monkeypatch.setattr(Broker.celery, "send_task", lambda name, args=(), kwargs=None, **options:
                        observed.append((name, DbSession.has_active_transaction(), (args, kwargs))))
    monkeypatch.setattr(import_module("langboard_shared.core.broker.Broker").Thread, "start", start_thread)
    return observed


@pytest.mark.parametrize("outcome", ["commit", "rollback", "receipt_failure", "commit_failure"])
def test_native_comment_dispatch_waits_for_commit(counts, native_delivery, monkeypatch, outcome):
    from sqlalchemy.orm import Session

    f, observed = counts, native_delivery
    original_insert = DbSession.insert

    def insert(db, model):
        if outcome == "receipt_failure" and isinstance(model, CardChangeReceipt):
            raise RuntimeError("receipt failure")
        return original_insert(db, model)

    def fail_commit(session):
        raise RuntimeError("commit failure")

    monkeypatch.setattr(DbSession, "insert", insert)
    if outcome == "commit_failure":
        event.listen(Session, "before_commit", fail_commit)
    try:
        def run():
            with DbSession.atomic():
                comment = f.get(CardCommentService).create(
                    f.user, f.project, f.card, EditorContentModel(content="Before commit")
                )
                if outcome == "rollback":
                    raise RuntimeError("outer rollback")
                if outcome == "commit":
                    assert observed == [], "Native transport escaped the open receipt transaction"
                    comment.content.content = "Caller changed this after registration"
        if outcome == "commit":
            run()
        else:
            with pytest.raises(RuntimeError, match="rollback|failure"):
                run()
    finally:
        if outcome == "commit_failure":
            event.remove(Session, "before_commit", fail_commit)

    assert receipt_count(f) == int(outcome == "commit")
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardComment)).scalar_one() == int(outcome == "commit")
    if outcome != "commit":
        assert observed == [], "Rolled-back mutation escaped through native transport"
    else:
        assert len(observed) == 3
        assert all(not active for _, active, _ in observed)
        socket = observed[0][2]["data"]
        assert socket["data"]["comment"]["content"]["content"] == "Before commit"
        assert socket["publish_models"]["card_uids"] == [f.card.get_uid()]
        assert "card_comment_added" in observed[1][0]
        assert "card_comment_added" in observed[2][0]


@pytest.mark.parametrize("rollback", [False, True])
def test_native_producer_payload_order_and_outer_rollback(counts, native_delivery, rollback):
    from langboard_shared.core.storage import FileModel

    f, observed = counts, native_delivery
    comments, checklists, items = f.get(CardCommentService), f.get(ChecklistService), f.get(CheckitemService)
    attachments = f.get(CardAttachmentService)

    def run():
        with DbSession.atomic():
            comment = comments.create(f.user, f.project, f.card, EditorContentModel(content="Initial"))
            comments.update(f.user, f.project, f.card, comment, EditorContentModel(content="Edited"))
            comments.delete(f.user, f.project, f.card, comment)
            checklist = checklists.create(f.user, f.project, f.card, "Initial checklist")
            checklists.change_title(f.user, f.project, f.card, checklist, "Edited checklist")
            checklists.toggle_checked(f.user, f.project, f.card, checklist, True)
            item = items.create(f.user, f.project, f.card, checklist, "Initial item")
            items.change_title(f.user, f.project, f.card, item, "Edited item")
            items.toggle_checked(f.user, f.project, f.card, item, True)
            items.delete(f.user, f.project, f.card, item)
            checklists.delete(f.user, f.project, f.card, checklist)
            attachment = attachments.create(f.user, f.project, f.card, FileModel(
                filename="fixture.txt", original_filename="fixture.txt", storage_name="card_attachment",
                storage_type="local", path="/fixture.txt"))
            attachments.change_name(f.user, f.project, f.card, attachment, "renamed.txt")
            f.cards.update_assigned_users(f.user, f.project, f.card, [])
            f.cards.update_labels(f.user, f.project, f.card, [])
            assert observed == []
            if rollback:
                raise RuntimeError("outer rollback")

    if rollback:
        with pytest.raises(RuntimeError, match="outer rollback"):
            run()
        assert observed == []
        assert receipt_count(f) == 0
    else:
        run()
        assert all(not active for _, active, _ in observed)
        assert receipt_count(f) == 15
        sockets = [payload["data"] for name, _, payload in observed if name == "socket"]
        assert sockets[0]["data"]["comment"]["content"]["content"] == "Initial"
        assert sockets[1]["data"]["content"]["content"] == "Edited"
        assert sockets[2]["publish_models"]["event"].startswith("board:card:comment:deleted:")
        assert sockets[3]["data"]["checklist"]["title"] == "Initial checklist"
        assert sockets[4]["data"]["title"] == "Edited checklist"
        assert sum("card_comment_added" in name for name, _, _ in observed) == 2


def test_native_early_publisher_is_discarded_on_receipt_failure(counts, native_delivery, monkeypatch):
    f = counts
    checklist = f.get(ChecklistService).create(f.user, f.project, f.card, "Initial", dispatch_effects=False)
    original_insert = DbSession.insert

    def fail_receipt(db, model):
        if isinstance(model, CardChangeReceipt):
            raise RuntimeError("receipt failure")
        return original_insert(db, model)

    monkeypatch.setattr(DbSession, "insert", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt failure"):
        f.get(ChecklistService).change_title(f.user, f.project, f.card, checklist, "Rejected")
    assert native_delivery == []
    assert receipt_count(f) == 1
    assert reread(f, Checklist, checklist.id).title == "Initial"


@pytest.fixture
def notification_recipient(counts, monkeypatch):
    """Delivery proof begins after recipient/settings policy; persistence stays native."""
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    from langboard_shared.Env import Env

    f = counts
    notifications = f.get(NotificationService)
    monkeypatch.setattr(notifications, "_resolve_notification_recipient", lambda target, kind, refs: (f.other, refs))
    monkeypatch.setattr(notifications, "_get_service", lambda _: SimpleNamespace(has_unsubscription=lambda *a: False))
    monkeypatch.setattr(type(Env), "NOTIFICATION_EMAIL_OUTBOX_ENABLED", property(lambda _: True))
    return notifications


@pytest.mark.parametrize("operation", ["comment", "card", "create", "assignment", "standalone"])
@pytest.mark.parametrize("rollback", [False, True])
def test_native_notifications_remain_transactional(
    counts, native_delivery, notification_recipient, monkeypatch, operation, rollback
):
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility

    f, observed = counts, native_delivery
    mention = EditorContentModel(content=f"[**@Owner**]({f.other.get_uid()})")
    if operation in {"create", "assignment"}:
        with DbSession.atomic() as db:
            db.insert(ProjectAssignedUser(project_id=f.project.id, user_id=f.other.id))
    monkeypatch.setattr(f.cards, "default_creation_visibility", lambda *_, app_owned=False: CardVisibility.Shared)

    def mutate():
        if operation == "comment":
            f.get(CardCommentService).create(f.user, f.project, f.card, mention)
        elif operation == "card":
            f.cards.update(f.user, f.project, f.card, {"description": mention}, expected_description="Before")
        elif operation == "create":
            f.cards.create(f.user, f.project, f.column, "Assigned card", EditorContentModel(content="Body"),
                           assign_user_uids=[f.other.get_uid()])
        elif operation == "assignment":
            f.cards.update_assigned_users(f.user, f.project, f.card, [f.other.get_uid()])
        else:
            notification_recipient.notify_assigned_to_card(f.user, f.other, f.project, f.card)

    def run():
        with DbSession.atomic() as db:
            mutate()
            assert db.exec(select(func.count()).select_from(UserNotification)).first()[0] == 1
            assert db.exec(select(func.count()).select_from(NotificationEmailDelivery)).first()[0] == 1
            assert observed == []
            if rollback:
                raise RuntimeError("outer rollback")

    if operation == "standalone":
        if rollback:
            pytest.skip("Standalone notification has no caller transaction to roll back")
        mutate()
    elif rollback:
        with pytest.raises(RuntimeError, match="outer rollback"):
            run()
    else:
        run()
    with f.engine.connect() as db:
        expected = int(not rollback)
        assert db.execute(select(func.count()).select_from(UserNotification)).scalar_one() == expected
        assert db.execute(select(func.count()).select_from(NotificationEmailDelivery)).scalar_one() == expected
    assert all(not active for _, active, _ in observed)
    assert sum("publish_pending_work_events" in name for name, _, _ in observed) == int(not rollback)
    notifications = [payload for name, _, payload in observed if name == "socket" and
                     "notification" in str(payload["data"]["publish_models"])]
    assert len(notifications) == int(not rollback)


@pytest.mark.parametrize("rollback", [False, True])
def test_native_bot_mention_waits_for_commit(counts, native_delivery, notification_recipient, monkeypatch, rollback):
    from langboard_shared.domain.models import Bot
    from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType

    f = counts
    with DbSession.atomic() as db:
        bot = Bot(name="Mentioned", bot_uname="mentioned", app_api_token="test-only",
                  platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default)
        db.insert(bot)
    monkeypatch.setattr(notification_recipient, "_resolve_notification_recipient", lambda *a: None)

    def run():
        with DbSession.atomic():
            comment = f.get(CardCommentService).create(f.user, f.project, f.card,
                EditorContentModel(content=f"[**@Mentioned**]({bot.get_uid()})"))
            assert native_delivery == []
            comment.content.content = "Later caller mutation"
            if rollback:
                raise RuntimeError("outer rollback")

    if rollback:
        with pytest.raises(RuntimeError, match="outer rollback"):
            run()
    else:
        run()
    assert all(not active for _, active, _ in native_delivery)
    assert sum("bot_mentioned" in name for name, _, _ in native_delivery) == int(not rollback)
    assert receipt_count(f) == int(not rollback)


@pytest.mark.parametrize("rollback", [False, True])
def test_native_attachment_metadata_is_transactional_and_payload_is_frozen(counts, native_delivery, monkeypatch, rollback):
    from json import loads
    from langboard_shared.core.routing import SocketTopic
    from langboard_shared.core.storage import FileModel
    from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
    from langboard_shared.domain.services.factory.InternalBotService import InternalBotService

    f = counts
    attachments = f.get(CardAttachmentService)
    metadata = f.get(DoclingMetadataService)
    provider = f.get(InternalBotService)
    monkeypatch.setattr(provider, "get_document_vision_binding", lambda: SimpleNamespace(
        value='{"model_name":"test"}', get_uid=lambda: "fixture-binding"))
    monkeypatch.setattr(provider, "is_document_processing_enabled", lambda: True)
    monkeypatch.setattr(attachments, "_snapshot_embedding_config", lambda: None)
    monkeypatch.setattr(import_module("langboard_shared.domain.services.KeywordSearchSettings"), "enabled", lambda _: False)

    def run():
        with DbSession.atomic() as db:
            attachment = attachments.create(f.user, f.project, f.card, FileModel(
                filename="fixture.pdf", original_filename="fixture.pdf", storage_name="card_attachment",
                storage_type="local", path="/fixture.pdf"))
            assert db.exec(select(func.count()).select_from(CardMetadata)).first()[0] == 1
            document = metadata.get_document_by_attachment_uid(CardMetadata, f.card, attachment.get_uid())
            assert document["status"] == "pending"
            metadata.mark_document_processing(CardMetadata, f.card, attachment.get_uid(), attachment.filename,
                                              completed_pages=1, total_pages=2, generation=document["generation"])
            metadata.publish_update(CardMetadata, f.card, SocketTopic.BoardCard)
            assert native_delivery == []
            if rollback:
                raise RuntimeError("outer rollback")

    if rollback:
        with pytest.raises(RuntimeError, match="outer rollback"):
            run()
    else:
        run()
    assert all(not active for _, active, _ in native_delivery)
    assert receipt_count(f) == int(not rollback)
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardMetadata)).scalar_one() == int(not rollback)
        assert db.execute(select(func.count()).select_from(CardAttachment)).scalar_one() == int(not rollback)
    snapshots = [loads(payload["data"]["data"]["value"])[0] for name, _, payload in native_delivery
                 if name == "socket" and payload["data"]["publish_models"]["event"].startswith("metadata:updated:")]
    assert [document["status"] for document in snapshots] == ([] if rollback else ["pending", "processing"])
    jobs = [entry for entry in native_delivery if "docling" in entry[0].lower()]
    assert len(jobs) == int(not rollback)


def test_native_counts_ignore_global_gaps_and_preserve_cursor_baseline_manual(counts):
    f = counts
    with DbSession.atomic() as db:
        unrelated = Card(project_id=f.project.id, project_column_id=f.column.id, title="Other", visibility="SHARED")
        db.insert(unrelated)
    for target in (f.card, unrelated, unrelated, f.card):
        f.cards.mark_card_changed(target, "comment")
    assert f.state()["unread_change_count"] == 2
    assert f.state()["unread_change_count_status"] == "exact"
    assert f.state()["latest_change"]["seq"] == f.card.last_change_seq
    stale = f.card.model_copy(deep=True)
    f.cards.mark_card_changed(f.card, "description")
    f.repo.user_card_read_state.set_read_state(f.user, stale, True)
    assert f.state()["unread_change_count"] == 0
    f.repo.user_card_read_state.set_read_state(f.user, f.card, False)
    assert f.state()["has_unread_change"] is True
    assert f.state()["unread_change_count"] is None
    assert f.state()["unread_change_count_status"] == "manual_unread"
    f.repo.user_card_read_state.set_read_state(f.user, f.card, True)
    f.cards.mark_card_changed(f.card, "checklist")
    assert f.state()["unread_change_count"] == 1
    with DbSession.atomic() as db:
        f.member.card_unread_baseline_seq = f.card.last_change_seq
        db.update(f.member)
    assert f.state()["unread_change_count"] == 0
    f.cards.mark_card_changed(f.card, "attachment")
    assert f.state()["unread_change_count"] == 1


def test_legacy_gap_remains_unknown_after_new_writers_until_seen(counts):
    f = counts
    if f.engine.dialect.name == "postgresql":
        with f.engine.begin() as db:
            db.execute(text("SELECT setval('content_change_seq', 100)"))
    with f.engine.begin() as db:
        db.execute(update(Card).where(Card.id == f.card.id).values(last_change_seq=100, change_count_since_seq=100))
    f.cards.mark_card_changed(f.card, "comment")
    assert f.state()["unread_change_count_status"] == "unknown_history"
    assert f.state()["unread_change_count"] is None
    f.repo.user_card_read_state.set_read_state(f.user, f.card, True)
    f.cards.mark_card_changed(f.card, "comment")
    assert f.state()["unread_change_count"] == 1
    # An uninstrumented writer is detected even after native coverage started.
    with f.engine.begin() as db:
        db.execute(update(Card).where(Card.id == f.card.id).values(last_change_seq=f.card.last_change_seq + 1))
        if f.engine.dialect.name == "postgresql":
            db.execute(text("SELECT nextval('content_change_seq')"))
    assert f.state()["unread_change_count_status"] == "unknown_history"
    f.cards.mark_card_changed(f.card, "description")
    assert f.state()["unread_change_count_status"] == "unknown_history"


def test_receipt_insert_failure_rolls_back_domain_stamp_and_invalidation(counts, monkeypatch):
    f = counts
    comments = f.get(CardCommentService)
    original = DbSession.insert
    def fail_receipt(db, row):
        if isinstance(row, CardChangeReceipt):
            raise RuntimeError("receipt failure")
        return original(db, row)
    monkeypatch.setattr(DbSession, "insert", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt failure"):
        comments.create(f.user, f.project, f.card, EditorContentModel(content="Rollback"), dispatch_effects=False)
    assert receipt_count(f) == 0
    assert reread(f).last_change_seq == 0
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardComment)).scalar_one() == 0
    assert not f.published.called


def test_native_producer_families_share_receipts_and_outer_rollback(counts, monkeypatch):
    f = counts
    calls = [
        lambda: f.get(CardCommentService).create(f.user, f.project, f.card, EditorContentModel(content="Comment"), dispatch_effects=False),
        lambda: f.get(CardContentBlockService).create(f.user, f.project, f.card, "rich_text", {"text": "Block"}),
        lambda: f.get(ChecklistService).create(f.user, f.project, f.card, "Checklist", dispatch_effects=False),
    ]
    for index, call in enumerate(calls, 1):
        assert call() is not None
        assert f.state()["unread_change_count"] == index
    with DbSession.atomic() as db:
        checklist = db.exec(SqlBuilder.select.table(Checklist).where(Checklist.card_id == f.card.id)).first()
    item = f.get(CheckitemService).create(f.user, f.project, f.card, checklist, "Item", dispatch_effects=False)
    assert item is not None and f.state()["unread_change_count"] == 4
    from langboard_shared.core.storage import FileModel
    attachment = FileModel(filename="fixture.txt", original_filename="fixture.txt", storage_name="card_attachment",
                           storage_type="local", path="/fixture.txt")
    assert f.get(CardAttachmentService).create(f.user, f.project, f.card, attachment, dispatch_effects=False) is not None
    assert f.state()["unread_change_count"] == 5
    f.published.reset_mock()
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            calls[0]()
            calls[2]()
            assert not f.published.called
            raise RuntimeError("rollback")
    assert f.state()["unread_change_count"] == 5
    assert receipt_count(f) == 5
    assert not f.published.called


def test_description_compare_and_swap_failed_edit_has_no_receipt(counts, monkeypatch):
    f = counts
    monkeypatch.setattr(f.cards, "is_check_card", lambda _: False)
    monkeypatch.setattr(f.cards, "remove_completion_checklist", lambda _: None)
    # Suppress delivery after the real transaction while keeping native SQL.
    module = import_module(CardService.__module__)
    monkeypatch.setattr(module.CardPublisher, "updated", Mock())
    monkeypatch.setattr(module.CardActivityTask, "card_updated", Mock())
    monkeypatch.setattr(module.CardBotTask, "card_updated", Mock())
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    f.cards._raw_get_service = lambda cls: SimpleNamespace(notify_mentioned_in_card=lambda *a: None) if cls is NotificationService else f.get(cls)
    result = f.cards.update(f.user, f.project, f.card, {"description": EditorContentModel(content="Accepted")}, expected_description="Before")
    assert result["description"]["content"] == "Accepted"
    assert f.state()["unread_change_count"] == 1
    with pytest.raises(CardDescriptionConflict):
        f.cards.update(f.user, f.project, f.card, {"description": EditorContentModel(content="Rejected")}, expected_description="Before")
    assert reread(f).description.content == "Accepted"
    assert receipt_count(f) == 1


def test_acl_move_and_mirror_projection_have_no_count_disclosure(counts):
    f = counts
    f.cards.mark_card_changed(f.card, "comment")
    with DbSession.atomic() as db:
        private = Card(project_id=f.project.id, project_column_id=f.column.id, title="Private", visibility="PRIVATE", owner_user_id=f.other.id, created_by_user_id=f.other.id)
        internal = Card(project_id=f.project.id, project_column_id=f.column.id, title="Internal", visibility="INTERNAL")
        mirror = Card(project_id=f.project.id, project_column_id=f.column.id, title="Mirror", visibility="SHARED", source_type="project_card", source_uid=f.card.get_uid())
        for row in (private, internal, mirror):
            db.insert(row)
    for row in (private, internal, mirror):
        f.cards.mark_card_changed(row, "comment")
    state = f.repo.user_card_read_state.get_change_state_map(f.user.id, [f.card.id, private.id, internal.id, mirror.id], context=f.context)
    assert set(state) == {f.card.id, mirror.id}
    assert state[mirror.id]["unread_change_count_status"] == "not_applicable"
    assert not state[mirror.id]["has_unread_change"]
    assert mirror.last_change_seq == 0
    # A move retains receipt identity, while membership/baseline comes from destination.
    with DbSession.atomic() as db:
        destination = Project(owner_id=f.other.id, title="Destination")
        db.insert(destination)
        db.exec(update(Card).where(Card.id == f.card.id).values(project_id=destination.id))
    assert f.repo.user_card_read_state.get_change_state_map(f.user.id, [f.card.id], context=f.context) == {}
    with DbSession.atomic() as db:
        db.insert(ProjectAssignedUser(project_id=destination.id, user_id=f.user.id, card_unread_baseline_seq=f.card.last_change_seq))
    assert f.state()["unread_change_count"] == 0
    assert receipt_count(f) == 3


def test_count_query_is_one_primary_authorized_batch(counts, monkeypatch):
    f = counts
    ids = []
    with DbSession.atomic() as db:
        for index in range(40):
            card = Card(project_id=f.project.id, project_column_id=f.column.id, title=str(index), visibility="SHARED")
            db.insert(card)
            f.cards.mark_card_changed(card, "card")
            ids.append(card.id)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("replica read"))
    statements = []
    def capture(_connection, _cursor, statement, *_):
        statements.append(statement)
    event.listen(f.engine, "before_cursor_execute", capture)
    try:
        result = f.repo.user_card_read_state.get_change_state_map(f.user.id, ids, context=f.context)
    finally:
        event.remove(f.engine, "before_cursor_execute", capture)
    assert len(result) == 40 and all(row["unread_change_count"] == 1 for row in result.values())
    assert len(statements) == 1
    assert "project_activity" not in statements[0].lower()
    statements.clear()
    event.listen(f.engine, "before_cursor_execute", capture)
    try:
        batched = f.repo.user_card_read_state.get_change_state_map(
            f.user.id, ids + [SnowflakeID(index) for index in range(1, 201)], context=f.context
        )
    finally:
        event.remove(f.engine, "before_cursor_execute", capture)
    assert batched == result
    assert len(statements) == 2, "At most 200 card IDs per statement, never one query per row"



def test_postgresql_concurrent_native_comments_are_ordered_and_lossless(counts):
    f = counts
    if f.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock concurrency proof")
    comments = f.get(CardCommentService)
    barrier = Barrier(6)
    def write(index):
        barrier.wait(timeout=10)
        return comments.create(f.user, f.project.id, f.card.id, EditorContentModel(content=f"Concurrent {index}"), dispatch_effects=False)
    with ThreadPoolExecutor(max_workers=6) as pool:
        created = list(pool.map(write, range(6)))
    assert all(created)
    with f.engine.connect() as db:
        seqs = db.execute(select(CardChangeReceipt.change_seq).order_by(CardChangeReceipt.change_seq)).scalars().all()
        assert db.execute(select(func.count()).select_from(CardComment)).scalar_one() == 6
    latest = reread(f)
    assert len(set(seqs)) == 6 and latest.last_change_seq == max(seqs)
    assert latest.last_change_target_id in {comment.id for comment in created}
    assert f.state()["unread_change_count"] == 6
    assert f.published.call_count == 6


def test_postgresql_snapshot_keeps_authority_latest_and_count_together(counts):
    f = counts
    if f.engine.dialect.name != "postgresql":
        pytest.skip("Concurrent PostgreSQL snapshot proof")
    f.cards.mark_card_changed(f.card, "comment")
    entered, changed = Event(), Event()
    @card_change_snapshot
    def read(self, project, user):
        # Start the MVCC snapshot before the concurrent write and revocation.
        first = f.state()
        entered.set()
        assert changed.wait(10)
        second = f.state()
        return first, second
    def writer():
        assert entered.wait(10)
        f.cards.mark_card_changed(f.card, "description")
        with f.engine.begin() as db:
            db.execute(ProjectAssignedUser.__table__.delete().where(ProjectAssignedUser.id == f.member.id))
        changed.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(writer)
        before, after = read(f.cards, f.project, f.user)
        pending.result(timeout=10)
    assert before == after
    assert after["unread_change_count"] == 1
    assert f.repo.user_card_read_state.get_change_state_map(f.user.id, [f.card.id], context=f.context) == {}


def test_migration_has_truthful_boundary_and_refuses_receipt_loss(counts, monkeypatch):
    f = counts
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261023010000-c7921d41af65.py"
    spec = importlib.util.spec_from_file_location("count_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with f.engine.begin() as db:
        db.execute(text("DROP TABLE card_change_receipt"))
        db.execute(text("ALTER TABLE card DROP COLUMN change_count_since_seq"))
        db.execute(text("UPDATE card SET last_change_seq=77"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
        migration.upgrade()
        assert db.execute(text("SELECT change_count_since_seq FROM card")).scalar_one() == 77
        assert db.execute(text("SELECT count(*) FROM card_change_receipt")).scalar_one() == 0
        migration.downgrade()
        migration.upgrade()
    if f.engine.dialect.name == "postgresql":
        with f.engine.begin() as db:
            db.execute(text("SELECT setval('content_change_seq', 77)"))
    f.cards.mark_card_changed(f.card, "description")
    assert f.state()["unread_change_count_status"] == "unknown_history"
    with f.engine.begin() as db:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()


def test_card_creation_labels_assignment_relationship_and_column_move(counts, monkeypatch):
    f = counts
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibility
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    from langboard_shared.domain.services.factory.WorkflowStagePolicyService import WorkflowStagePolicyService
    module = import_module(CardService.__module__)
    relation_module = import_module(CardRelationshipService.__module__)
    monkeypatch.setattr(module, "CardActivityTask", Mock())
    monkeypatch.setattr(module, "CardBotTask", Mock())
    monkeypatch.setattr(relation_module, "CardRelationshipActivityTask", Mock())
    monkeypatch.setattr(relation_module, "CardBotTask", Mock())
    monkeypatch.setattr(f.cards, "default_creation_visibility", lambda *_, app_owned=False: CardVisibility.Shared)
    monkeypatch.setattr(f.cards, "_apply_title_labels", lambda _actor, _project, _card, title: title)
    monkeypatch.setattr(f.cards, "publish_work_states", Mock())
    monkeypatch.setattr(f.cards, "notify_order_changed", Mock())
    monkeypatch.setattr(CardPublisher, "assigned_users_updated", Mock())
    notification, policy = Mock(), Mock()
    f.cards._raw_get_service = lambda cls: notification if cls is NotificationService else policy if cls is WorkflowStagePolicyService else f.get(cls)
    created, _ = f.cards.create(f.user, f.project, f.column, "Created", EditorContentModel(content="Body"), dispatch_effects=False)
    assert f.state(created)["unread_change_count"] == 1
    with DbSession.atomic() as db:
        label = ProjectLabel(project_id=f.project.id, name="Label", color="#fff", description="Fixture")
        kind = GlobalCardRelationshipType(parent_name="Related", child_name="Related", machine_semantic="related")
        column = ProjectColumn(project_id=f.project.id, name="Next")
        for row in (label, kind, column):
            db.insert(row)
    assert f.cards.update_labels(f.user, f.project, f.card, [label], dispatch_effects=False)
    assert f.state()["unread_change_count"] == 1
    assert f.cards.update_assigned_users(f.user, f.project, f.card, [f.user.get_uid()])
    assert f.state()["unread_change_count"] == 2
    relations = f.get(CardRelationshipService)
    monkeypatch.setattr(relations, "dispatch_updated", Mock())
    assert relations.update(f.user, f.project, f.card, False, [(created.get_uid(), kind.get_uid())]) is not None
    assert f.state()["unread_change_count"] == 3
    assert f.cards._change_order(f.user, f.project, f.card, 0, column)
    assert f.state()["unread_change_count"] == 4
    assert reread(f).project_column_id == column.id
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardRelationship)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(CardAssignedUser)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(CardAssignedProjectLabel)).scalar_one() == 1


def test_board_wire_projection_uses_primary_snapshot_and_keeps_unread_card_cardinality(counts, monkeypatch):
    f = counts
    f.cards.mark_card_changed(f.card, "comment")
    f.cards.mark_card_changed(f.card, "description")
    # Enrichment outside the count contract is stubbed; real board/card/read/ACL
    # repositories and the board service projection remain exercised.
    monkeypatch.setattr(f.cards, "_resolve_internal_access", lambda _: False)
    monkeypatch.setattr(f.cards, "cross_board_eligibility", lambda rows: {row.id: True for row in rows})
    monkeypatch.setattr(f.cards, "get_active_workers", lambda *a, **k: {})
    monkeypatch.setattr(f.cards, "get_work_states", lambda rows, **k: {
        row.id: {"checklist_progress": {"total": 0, "completed": 0}} for row in rows
    })
    monkeypatch.setattr(f.repo.card, "get_board_creators", lambda *a, **k: {})
    monkeypatch.setattr(f.get(CardRelationshipService), "get_readable_incident_by_cards", lambda *a, **k: {})
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("stale replica"))
    rows, _ = f.cards.get_board_snapshot(f.project, f.user, channel=CollaborationChannel.HumanUI)
    assert len(rows) == 1
    row = rows[0]
    assert row["unread_change_count"] == 2
    assert row["unread_change_count_status"] == "exact"
    assert sum(bool(row["has_unread_change"]) for row in rows) == 1
    assert row["latest_change"]["seq"] == row["last_change_seq"] == f.card.last_change_seq
    assert row["latest_change"]["target_type"] == "description"
    with f.engine.connect() as db:
        assert db.execute(select(ProjectAssignedUser.board_seen_seq).where(ProjectAssignedUser.id == f.member.id)).scalar_one() == f.card.last_change_seq


def test_after_commit_payload_is_only_authorized_refetch_invalidation(monkeypatch):
    publish = Mock()
    monkeypatch.setattr(CardPublisher, "put_dispather", publish)
    card = Card(id=10, project_id=20, project_column_id=30, title="Fixture")
    CardPublisher.metadata_changed(card)
    payload, event_model = publish.call_args.args
    assert payload == {"updated_at": card.updated_at.isoformat(), "change_count_changed": True}
    assert event_model.card_uids == [card.get_uid()]
    assert event_model.event == f"board:card:details:changed:{card.get_uid()}"
    assert "change_count_changed" in event_model.data_keys


def test_postgresql_description_cas_competing_writers_commit_one_receipt(counts, monkeypatch):
    f = counts
    if f.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL competing CAS proof")
    monkeypatch.setattr(f.cards, "is_check_card", lambda _: False)
    monkeypatch.setattr(f.cards, "remove_completion_checklist", lambda _: None)
    module = import_module(CardService.__module__)
    monkeypatch.setattr(module.CardPublisher, "updated", Mock())
    monkeypatch.setattr(module.CardActivityTask, "card_updated", Mock())
    monkeypatch.setattr(module.CardBotTask, "card_updated", Mock())
    from langboard_shared.domain.services.factory.NotificationService import NotificationService
    f.cards._raw_get_service = lambda cls: SimpleNamespace(notify_mentioned_in_card=lambda *a: None) if cls is NotificationService else f.get(cls)
    barrier = Barrier(2)
    def write(label):
        barrier.wait(timeout=10)
        try:
            f.cards.update(f.user, f.project.id, f.card.id, {"description": EditorContentModel(content=label)}, expected_description="Before")
            return label
        except CardDescriptionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(write, ["Winner A", "Winner B"]))
    winners = [label for label in result if label is not None]
    assert len(winners) == 1
    assert reread(f).description.content == winners[0]
    assert f.state()["unread_change_count"] == receipt_count(f) == 1
    assert f.published.call_count == 1


def test_stale_deleted_card_cannot_commit_a_receiptless_comment(counts):
    f = counts
    with f.engine.begin() as db:
        db.execute(update(Card).where(Card.id == f.card.id).values(deleted_at=SafeDateTime.now()))
    with pytest.raises(ValueError, match="Card no longer available"):
        f.get(CardCommentService).create(f.user, f.project, f.card, EditorContentModel(content="Stale"), dispatch_effects=False)
    assert receipt_count(f) == 0
    with f.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(CardComment)).scalar_one() == 0
    assert not f.published.called


def test_comment_conditional_edit_preserves_current_content_and_effects(counts, native_delivery):
    f, observed = counts, native_delivery
    service = f.get(CardCommentService)
    comment = service.create(f.user, f.project, f.card, EditorContentModel(content="Original"), dispatch_effects=False)
    baseline = reread(f, CardComment, comment.id).api_response()["content_revision"]
    stale = comment.model_copy(deep=True)
    saved = service.update(f.user, f.project, f.card, comment, EditorContentModel(content="New server content"),
                           expected_revision=baseline)
    assert reread(f, CardComment, comment.id).content.content == "New server content"
    assert saved.api_response()["content_revision"] != baseline
    receipts, effects = receipt_count(f), len(observed)
    with pytest.raises(CardCommentConflict):
        service.update(f.user, f.project, f.card, stale, EditorContentModel(content="Old local draft"),
                       expected_revision=baseline)
    assert reread(f, CardComment, comment.id).content.content == "New server content"
    assert receipt_count(f) == receipts and len(observed) == effects
    assert service.update(f.user, f.project, f.card, saved, EditorContentModel(content="Next edit"),
                          expected_revision=saved.content_revision()) is not None
    assert reread(f, CardComment, comment.id).content.content == "Next edit"


def test_comment_revision_keeps_timezone_identity_and_anchor_changes(counts):
    from datetime import timedelta, timezone

    f = counts
    comment = f.get(CardCommentService).create(f.user, f.project, f.card,
        EditorContentModel(content="Original"), dispatch_effects=False)
    current = reread(f, CardComment, comment.id)
    baseline = current.content_revision()
    shifted = current.model_copy(deep=True)
    stamp = current.updated_at.replace(tzinfo=timezone.utc) if current.updated_at.tzinfo is None else current.updated_at
    shifted.updated_at = stamp.astimezone(timezone(timedelta(hours=9)))
    assert shifted.content_revision() == baseline
    shifted.anchor = {"type": "TextQuoteSelector", "version": 1, "exact": "Changed anchor"}
    assert shifted.content_revision() != baseline
    shifted.anchor = current.anchor
    shifted.updated_at = stamp + timedelta(microseconds=1)
    assert shifted.content_revision() != baseline


def test_comment_conditional_edit_rejects_deleted_or_foreign_comment(counts, native_delivery):
    f = counts
    service = f.get(CardCommentService)
    comment = service.create(f.user, f.project, f.card, EditorContentModel(content="Original"), dispatch_effects=False)
    baseline = comment.content_revision()
    assert service.update(f.other, f.project, f.card, comment, EditorContentModel(content="Unauthorized"),
                          expected_revision=baseline) is None
    with DbSession.atomic() as db:
        db.exec(update(CardComment).where(CardComment.id == comment.id).values(deleted_at=SafeDateTime.now()))
    before = receipt_count(f)
    with pytest.raises(CardCommentConflict):
        service.update(f.user, f.project, f.card, comment, EditorContentModel(content="Revived old draft"),
                       expected_revision=baseline)
    assert receipt_count(f) == before


def test_concurrent_comment_edit_has_one_winner_and_one_receipt(counts, monkeypatch):
    if counts.engine.dialect.name != "postgresql":
        pytest.skip("Concurrent row locking requires PostgreSQL")
    f = counts
    service = f.get(CardCommentService)
    comment = service.create(f.user, f.project, f.card, EditorContentModel(content="Original"), dispatch_effects=False)
    baseline, before = comment.content_revision(), receipt_count(f)
    gate = Barrier(2)
    monkeypatch.setattr(import_module(CardCommentService.__module__), "dispatch_after_commit", lambda *a, **k: None)

    def edit(text):
        independent = comment.model_copy(deep=True)
        gate.wait(timeout=5)
        try:
            service.update(f.user, f.project, f.card, independent, EditorContentModel(content=text),
                           expected_revision=baseline)
            return "saved"
        except CardCommentConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(edit, text) for text in ("First edit", "Second edit")]
        assert sorted(future.result(timeout=15) for future in futures) == ["conflict", "saved"]
    assert reread(f, CardComment, comment.id).content.content in {"First edit", "Second edit"}
    assert receipt_count(f) == before + 1


def test_comment_edit_route_returns_revision_and_conflict(counts, native_delivery, monkeypatch):
    import json
    from inspect import unwrap
    from fastapi import Request
    from langboard.routes.board import BoardCardCommentApi
    from langboard.routes.board.forms import UpdateCardCommentForm
    from langboard_shared.core.routing import ApiException

    f = counts
    comments = f.get(CardCommentService)
    comment = comments.create(f.user, f.project, f.card, EditorContentModel(content="Original"), dispatch_effects=False)
    baseline = comment.content_revision()
    monkeypatch.setattr(BoardCardCommentApi, "require_visible_card", lambda *a: f.card)
    monkeypatch.setattr(BoardCardCommentApi, "require_card_child", lambda *a: None)
    endpoint = unwrap(BoardCardCommentApi.update_card_comment)
    args = dict(project_uid=f.project.get_uid(), card_uid=f.card.get_uid(), request=Request({"type": "http"}),
                comment_uid=comment.get_uid(), user_or_bot=f.user, service=SimpleNamespace(card_comment=comments))
    response = endpoint(**args, comment=UpdateCardCommentForm(content="First route edit", expected_revision=baseline))
    payload = json.loads(response.body)
    assert payload["content_revision"] != baseline and payload["updated_at"]
    with pytest.raises(ApiException.Conflict_409):
        endpoint(**args, comment=UpdateCardCommentForm(content="Stale route edit", expected_revision=baseline))
    assert reread(f, CardComment, comment.id).content.content == "First route edit"
