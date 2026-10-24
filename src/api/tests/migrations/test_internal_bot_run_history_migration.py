"""Fresh SQLite history and populated bot runs survive the history-reference revision."""

import runpy
from io import StringIO
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import JSON, Column, ForeignKey, Index, Integer, MetaData, String, Table, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


MIGRATIONS = Path(__file__).resolve().parents[2] / "langboard/migrations"
REVISION = MIGRATIONS / "versions/20260913085643-3a3d6b9eb3c2.py"


@pytest.mark.parametrize("interrupted", [False, True])
def test_history_reference_preserves_rows_indexes_and_foreign_keys(interrupted):
    metadata = MetaData()
    history = Table("chat_history", metadata, Column("id", Integer, primary_key=True))
    runs = Table(
        "internal_bot_run", metadata,
        Column("id", Integer, primary_key=True),
        Column("request_key", String, unique=True, nullable=False),
        Column("request_payload", JSON, nullable=False),
        Column("chat_history_id", Integer, ForeignKey("chat_history.id", name="fk_existing_history", ondelete="CASCADE")),
    )
    Index("ix_existing_request_key", runs.c.request_key)
    revision = runpy.run_path(str(REVISION))
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as db:
            db.exec_driver_sql("PRAGMA foreign_keys=ON")
            metadata.create_all(db)
            db.execute(history.insert(), [{"id": 1}, {"id": 2}])
            original = {"id": 41, "request_key": "existing-request", "request_payload": {"evidence": ["retained"]}, "chat_history_id": 1}
            db.execute(runs.insert(), original)
            context = MigrationContext.configure(db)
            if interrupted:
                # Reproduce the state left by the original failed SQLite ALTER.
                db.exec_driver_sql("ALTER TABLE internal_bot_run ADD COLUMN ai_chat_history_id BIGINT")
            with Operations.context(context):
                revision["upgrade"]()
            assert db.execute(text("SELECT id, request_key, request_payload, chat_history_id, ai_chat_history_id FROM internal_bot_run")).one() == (
                41, "existing-request", '{"evidence": ["retained"]}', 1, None,
            )
            assert {index["name"] for index in inspect(db).get_indexes("internal_bot_run")} == {"ix_existing_request_key"}
            assert inspect(db).get_unique_constraints("internal_bot_run")[0]["column_names"] == ["request_key"]
            foreign_keys = {key["name"]: key for key in inspect(db).get_foreign_keys("internal_bot_run")}
            assert foreign_keys["fk_existing_history"]["referred_table"] == "chat_history"
            added = foreign_keys["fk_internal_bot_run_ai_chat_history_id_chat_history"]
            assert added["constrained_columns"] == ["ai_chat_history_id"]
            assert added["options"]["ondelete"] == "CASCADE"
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(text("UPDATE internal_bot_run SET ai_chat_history_id=999 WHERE id=41"))
            db.execute(text("INSERT INTO internal_bot_run (id, request_key, request_payload, ai_chat_history_id) VALUES (42, 'new-request', '{}', 2)"))
            db.execute(history.delete().where(history.c.id == 2))
            assert db.execute(text("SELECT id FROM internal_bot_run")).scalars().all() == [41]
            with Operations.context(context):
                revision["downgrade"]()
            assert "ai_chat_history_id" not in {column["name"] for column in inspect(db).get_columns("internal_bot_run")}
            assert db.execute(runs.select()).mappings().one() == original
            assert inspect(db).get_indexes("internal_bot_run")[0]["name"] == "ix_existing_request_key"
            db.execute(history.delete().where(history.c.id == 1))
            assert db.execute(runs.select()).all() == []
    finally:
        engine.dispose()


def test_postgres_keeps_native_alter_statements_without_table_rebuild():
    revision = runpy.run_path(str(REVISION))
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        revision["upgrade"]()
        revision["downgrade"]()
    sql = output.getvalue()
    assert "ALTER TABLE internal_bot_run ADD COLUMN ai_chat_history_id BIGINT" in sql
    assert "FOREIGN KEY(ai_chat_history_id) REFERENCES chat_history (id) ON DELETE CASCADE" in sql
    assert "DROP CONSTRAINT fk_internal_bot_run_ai_chat_history_id_chat_history" in sql
    assert "DROP COLUMN ai_chat_history_id" in sql
    assert "CREATE TABLE" not in sql and "DROP TABLE" not in sql


def test_fresh_sqlite_replays_actual_history_to_current_head(tmp_path):
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.sqlite'}")
    try:
        with engine.connect() as db:
            config.attributes["connection"] = db
            command.upgrade(config, "head")
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
            tables = set(inspect(db).get_table_names())
            assert {"internal_bot_run", "workflow_effect_run", "document_embedding_cache", "document_embedding_cache_reference"} <= tables
            columns = {column["name"] for column in inspect(db).get_columns("internal_bot_run")}
            assert "ai_chat_history_id" in columns
            command.upgrade(config, "head")
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
            assert set(inspect(db).get_table_names()) == tables
    finally:
        engine.dispose()
