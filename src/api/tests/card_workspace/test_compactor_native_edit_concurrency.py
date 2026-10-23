"""Compactor authority locks coexist with native edit FK checks and serialize grants."""

import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import Card, CardChangeReceipt, InternalBot
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.CompactorBoardGrant import CompactorBoardGrant
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services.CompactorBoardAuthority import (
    CompactorAuthorityError,
    require_compactor_card,
    set_compactor_board_grant,
)
from langboard_shared.domain.services.factory.CardService import CardService
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from src.api.tests.card_workspace.test_work_plan_native_edit_concurrency import native_edit_board as native_edit_board


@pytest.fixture
def compactor_edit_board(native_edit_board):  # noqa: F811 - imported native fixture
    f = native_edit_board
    for model in (InternalBot, CompactorBoardGrant):
        model.__table__.create(f.engine, checkfirst=True)
    with DbSession.atomic() as db:
        f.profile = InternalBot(
            bot_type=InternalBotType.CommentCompaction,
            display_name="Compactor",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(f.profile)
        db.insert(
            CompactorBoardGrant(
                project_id=f.project.id,
                internal_bot_id=f.profile.id,
                granted_by_user_id=f.other.id,
                can_read=True,
                can_compact=True,
                can_archive=True,
            )
        )
    return f


@pytest.mark.parametrize("action", ["compact", "archive"])
def test_native_edit_and_compactor_authority_both_commit(compactor_edit_board, monkeypatch, action):
    f = compactor_edit_board
    card_locked, project_locked = threading.Event(), threading.Event()
    worker = threading.local()
    original_mark = CardService.mark_card_changed

    def mark_changed(service, card, *args, **kwargs):
        if getattr(worker, "role", None) == "editor":
            card_locked.set()
            assert project_locked.wait(5), "Compactor never acquired its project guard"
        return original_mark(service, card, *args, **kwargs)

    def after_sql(_connection, _cursor, statement, _parameters, _context, _many):
        if getattr(worker, "role", None) == "compactor" and "FROM project" in statement and "FOR " in statement:
            project_locked.set()

    monkeypatch.setattr(CardService, "mark_card_changed", mark_changed)
    event.listen(f.engine, "after_cursor_execute", after_sql)

    def edit():
        worker.role = "editor"
        return f.cards.update(f.user, f.project, f.card, {"title": "Native edit during compactor authority"})

    def authorize():
        worker.role = "compactor"
        with DbSession.atomic() as db:
            _, _, current = require_compactor_card(db, f.project.id, f.profile.id, f.card.id, action=action)
            return current.title

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            editor = pool.submit(edit)
            assert card_locked.wait(5), "Native edit never acquired its card row"
            compactor = pool.submit(authorize)
            assert editor.result(timeout=15)["title"] == "Native edit during compactor authority"
            assert compactor.result(timeout=15) == "Native edit during compactor authority"
    finally:
        event.remove(f.engine, "after_cursor_execute", after_sql)
    with f.engine.connect() as db:
        assert (
            db.execute(select(Card.title).where(Card.id == f.card.id)).scalar_one()
            == "Native edit during compactor authority"
        )
        assert db.execute(select(func.count()).select_from(CardChangeReceipt)).scalar_one() == 1


@pytest.mark.parametrize("action", ["compact", "archive"])
def test_grant_revocation_waits_then_rejects_new_effect(compactor_edit_board, action):
    f = compactor_edit_board
    locked, release = threading.Event(), threading.Event()

    def authorized_effect():
        with DbSession.atomic() as db:
            require_compactor_card(db, f.project.id, f.profile.id, f.card.id, action=action)
            locked.set()
            assert release.wait(5), "Revocation did not finish its lock probe"

    with ThreadPoolExecutor(max_workers=1) as pool:
        effect = pool.submit(authorized_effect)
        try:
            assert locked.wait(5)
            with pytest.raises(OperationalError) as rejected:
                with DbSession.atomic() as db:
                    db.exec(text("SET LOCAL lock_timeout = '100ms'"))
                    set_compactor_board_grant(
                        f.other, f.project.id, f.profile.id, can_read=False, can_compact=False, can_archive=False
                    )
            assert rejected.value.orig.sqlstate == "55P03"
        finally:
            release.set()
        effect.result(timeout=15)
    set_compactor_board_grant(f.other, f.project.id, f.profile.id, can_read=False, can_compact=False, can_archive=False)
    with pytest.raises(CompactorAuthorityError) as denied:
        with DbSession.atomic() as db:
            require_compactor_card(db, f.project.id, f.profile.id, f.card.id, action=action)
    assert denied.value.code == "forbidden"
