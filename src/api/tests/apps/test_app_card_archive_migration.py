# ruff: noqa: F811
"""Archive history cannot be discarded by a schema downgrade."""

import importlib
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import AppCardArchive
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401


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
