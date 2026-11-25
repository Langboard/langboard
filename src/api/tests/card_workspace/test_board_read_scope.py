"""Board response projections share one read snapshot, never a caller's write unit."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import User
from langboard_shared.domain.services.CardChangeTransaction import card_change_snapshot
from sqlalchemy import create_engine, event, literal, select
from sqlalchemy.pool import StaticPool


class BoardReadScopeTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        self.stack.enter_context(patch.object(DbEngine, "get_main_engine", return_value=self.engine))
        self.stack.enter_context(patch.object(DbEngine, "get_readonly_engine", side_effect=AssertionError("Replica checkout")))
        self.actor = User(id=1, firstname="Read", lastname="QA", email="read@example.invalid", password="fixture-only")
        self.checkouts = []
        event.listen(self.engine, "checkout", lambda *args: self.checkouts.append(1))

    @staticmethod
    @card_change_snapshot
    def projection(owner, project, actor):
        with DbSession.use(readonly=True) as db:
            return db, db.exec(select(literal(1))).all()

    def test_keyword_route_and_nested_projection_share_one_primary_checkout(self):
        @card_change_snapshot
        def route(project_uid, request, user_or_bot):
            with DbSession.use(readonly=True) as db:
                db.exec(select(literal(2))).all()
                nested_db, rows = self.projection(None, project_uid, user_or_bot)
                self.assertIs(db, nested_db)
                return rows

        self.assertEqual(route(project_uid="board", request=object(), user_or_bot=self.actor), [(1,)])
        self.assertEqual(len(self.checkouts), 1)
        self.assertFalse(DbSession.has_active_transaction())

    def test_unrelated_write_transaction_remains_rejected(self):
        with DbSession.atomic():
            with self.assertRaisesRegex(RuntimeError, "must own"):
                self.projection(None, "board", self.actor)

    def test_failed_read_does_not_authorize_a_later_write_transaction(self):
        @card_change_snapshot
        def fail(owner, project, actor):
            self.projection(owner, project, actor)
            raise ValueError("read failed")

        with self.assertRaisesRegex(ValueError, "read failed"):
            fail(None, "board", self.actor)
        self.assertFalse(DbSession.has_active_transaction())
        with DbSession.atomic():
            with self.assertRaisesRegex(RuntimeError, "must own"):
                self.projection(None, "board", self.actor)
        self.assertEqual(self.projection(None, "board", self.actor)[1], [(1,)])

    def test_non_user_projection_keeps_existing_no_snapshot_path(self):
        actor = object()

        @card_change_snapshot
        def read(owner, project, user_or_bot):
            self.assertIs(user_or_bot, actor)
            self.assertFalse(DbSession.has_active_transaction())
            return "unchanged"

        self.assertEqual(read(None, "board", user_or_bot=actor), "unchanged")
        self.assertEqual(self.checkouts, [])

    def test_after_commit_runs_outside_the_read_scope(self):
        observed = []

        @card_change_snapshot
        def read(owner, project, actor):
            with DbSession.use(readonly=False) as db:
                db.after_commit(lambda: observed.append(DbSession.has_active_transaction()))

        read(None, "board", self.actor)
        self.assertEqual(observed, [False])


if __name__ == "__main__":
    unittest.main()
