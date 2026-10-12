# ruff: noqa: F811
"""Archive history cannot be discarded by a schema downgrade."""

import importlib
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import AppCardArchive, BoardAppBinding
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from sqlalchemy.exc import IntegrityError


def test_archive_revision_remains_unique_after_model_refactor(board):
    AppCardArchive.__table__.create(DbEngine.get_main_engine(), checkfirst=True)

    def receipt(revision):
        return AppCardArchive(card_id=11, project_id=22, actor_id=33, app_key="example",
                              card_revision=revision, receipt={"state": "retained"})

    with DbSession.atomic() as db:
        db.insert(receipt(1))
    with pytest.raises(IntegrityError):
        with DbSession.atomic() as db:
            db.insert(receipt(1))
    with DbSession.atomic() as db:
        db.insert(receipt(2))
        rows = db.exec(SqlBuilder.select.table(AppCardArchive)).all()
        assert sorted(row.card_revision for row in rows) == [1, 2]


def test_archive_history_survives_downgrade(board, monkeypatch):
    migration = importlib.import_module("langboard.migrations.versions.20261011130000-c2b5ec40f6d8")
    engine = DbEngine.get_main_engine()
    with engine.begin() as db:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
        migration.upgrade()
        migration.downgrade()
        migration.upgrade()
    with DbSession.atomic() as db:
        db.insert(AppCardArchive(card_id=11, project_id=22, actor_id=33, app_key="example",
                                 card_revision=1, receipt={"state": "retained", "external_action": "none"}))
    with pytest.raises(RuntimeError, match="Cannot discard app archive history"):
        with engine.begin() as db:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
            migration.downgrade()
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppCardArchive)).first().receipt["state"] == "retained"


def test_existing_binding_archive_sync_defaults_off_and_active_downgrade_denied(board, monkeypatch):
    from sqlalchemy import text

    migration = importlib.import_module("langboard.migrations.versions.20261011140000-d3c6fd51a7e9")
    engine = DbEngine.get_main_engine()
    with DbSession.atomic() as db:
        row = BoardAppBinding(project_id=board[2].id, app_key="archive-example")
        db.insert(row)
    with engine.begin() as db:
        db.execute(text("ALTER TABLE board_app_binding DROP COLUMN archive_sync_types"))
        db.execute(text("ALTER TABLE board_app_binding DROP COLUMN archive_sync_revision"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
        migration.upgrade()
    with DbSession.atomic() as db:
        migrated = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == row.id)).first()
        assert migrated.archive_sync_types == [] and migrated.archive_sync_revision == 1
        migrated.archive_sync_types = ["issue"]
        migrated.archive_sync_revision = 2
        db.update(migrated)
    with pytest.raises(RuntimeError, match="Disable archive synchronization"):
        with engine.begin() as db:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
            migration.downgrade()
