"""Retain wide attachment sizes and enforce personal collection ownership."""

import importlib
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import AttachmentStorageObject
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_collection_owner_migration_preserves_rows_and_rejects_orphans(board, monkeypatch):
    engine = DbEngine.get_main_engine()
    original = importlib.import_module("langboard.migrations.versions.20261022040000-f86d4ce73a02")
    repaired = importlib.import_module("langboard.migrations.versions.20261023020000-d8032e52b076")
    with engine.begin() as db:
        operations = Operations(MigrationContext.configure(db))
        monkeypatch.setattr(original, "op", operations)
        monkeypatch.setattr(repaired, "op", operations)
        original.upgrade()
        db.execute(text(
            "INSERT INTO user_project_collection (id,created_at,updated_at,user_id,revision,collections) "
            "VALUES (900,now(),now(),1,3,'[{\"name\":\"retained\"}]'),(901,now(),now(),9999,4,'[]')"
        ))

        def snapshot():
            return db.execute(text("SELECT * FROM user_project_collection ORDER BY id")).all()

        before = snapshot()
        with pytest.raises(RuntimeError, match="unmatched owners"):
            repaired.upgrade()
        assert snapshot() == before
        assert inspect(db).get_foreign_keys("user_project_collection") == []
        db.execute(text("UPDATE user_project_collection SET user_id=2 WHERE id=901"))
        before = snapshot()
        repaired.upgrade()
        assert snapshot() == before
        assert any(fk["constrained_columns"] == ["user_id"] and fk["options"].get("ondelete") == "CASCADE"
                   for fk in inspect(db).get_foreign_keys("user_project_collection"))
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(text("UPDATE user_project_collection SET user_id=9999 WHERE id=901"))
        with db.begin_nested() as savepoint:
            db.execute(text('DELETE FROM "user" WHERE id=1'))
            assert db.execute(text("SELECT count(*) FROM user_project_collection WHERE id=900")).scalar_one() == 0
            savepoint.rollback()
        assert snapshot() == before
        repaired.downgrade()
        assert snapshot() == before
        assert inspect(db).get_foreign_keys("user_project_collection") == []


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_attachment_object_model_round_trips_sizes_above_32_bits(board):
    engine = DbEngine.get_main_engine()
    AttachmentStorageObject.__table__.create(engine)
    with engine.begin() as db:
        db.execute(text(
            "INSERT INTO attachment_storage_object "
            "(id,created_at,updated_at,storage_type,content_sha256,size_bytes,allocation_token,ready) "
            "VALUES (900,now(),now(),'test',:digest,:size,'retained-size',false)"
        ), {"digest": "a" * 64, "size": 2**40})
        assert db.execute(text("SELECT size_bytes FROM attachment_storage_object WHERE id=900")).scalar_one() == 2**40
