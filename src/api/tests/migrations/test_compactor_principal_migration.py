"""Migration retains manual principals, original foreign keys and automatic evidence."""

import runpy
from io import StringIO
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


VERSIONS = Path(__file__).parents[2] / "langboard/migrations/versions"
REVISION = VERSIONS / "20261022010000-b4a719de028c.py"


def test_populated_sqlite_retains_manual_rows_foreign_keys_and_profile_history():
    engine = create_engine("sqlite://")
    migration = runpy.run_path(str(REVISION))
    try:
        with engine.begin() as db:
            db.exec_driver_sql("PRAGMA foreign_keys=ON")
            for table in ("user", "card", "internal_bot"):
                db.exec_driver_sql(f'CREATE TABLE "{table}" (id BIGINT PRIMARY KEY)')
                db.exec_driver_sql(f'INSERT INTO "{table}" VALUES (1)')
            with Operations.context(MigrationContext.configure(db)):
                runpy.run_path(str(VERSIONS / "20261021020000-c4a8f16d3092.py"))["upgrade"]()
                runpy.run_path(str(VERSIONS / "20261021040000-e7b942cd815f.py"))["upgrade"]()
            db.execute(text("INSERT INTO card_comment_compaction (id,created_at,updated_at,request_key,card_id,user_id,internal_bot_id,source_revision,provider_revision,policy_version,source_manifest,status,attempt) VALUES (1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'manual-key',1,1,1,'source','provider',1,'[]','pending',0)"))
            db.execute(text("INSERT INTO workflow_effect_run (id,created_at,updated_at,stage_id,stage_key,stage_revision,project_id,card_id,actor_id,actor_type,source,status,effects,started_at,finished_at,duration_ms,result) VALUES (1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,'review','revision',1,1,1,'user','manual','succeeded','[]',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,0,'{}')"))
            before = db.execute(text("SELECT * FROM card_comment_compaction")).mappings().one()
            history = db.execute(text("SELECT * FROM workflow_effect_run")).mappings().one()
            with Operations.context(MigrationContext.configure(db)):
                migration["upgrade"]()
            assert db.execute(text("SELECT * FROM card_comment_compaction")).mappings().one() == before
            assert db.execute(text("SELECT * FROM workflow_effect_run")).mappings().one() == history
            assert {fk["referred_table"] for fk in inspect(db).get_foreign_keys("card_comment_compaction")} == {"user", "card", "internal_bot"}
            db.execute(text("UPDATE card_comment_compaction SET user_id=NULL"))
            db.execute(text("UPDATE workflow_effect_run SET actor_type='profile',source='continuous'"))
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(text("UPDATE card_comment_compaction SET internal_bot_id=999"))
            with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match="principal evidence"):
                migration["downgrade"]()
            assert db.execute(text("SELECT user_id FROM card_comment_compaction")).scalar_one() is None
            db.execute(text("UPDATE card_comment_compaction SET user_id=1"))
            db.execute(text("UPDATE workflow_effect_run SET actor_type='user',source='manual'"))
            with Operations.context(MigrationContext.configure(db)):
                migration["downgrade"]()
            assert not next(c for c in inspect(db).get_columns("card_comment_compaction") if c["name"] == "user_id")["nullable"]
            assert db.execute(text("SELECT * FROM card_comment_compaction")).mappings().one() == before
    finally:
        engine.dispose()


def test_postgres_offline_upgrade_keeps_native_alter_without_dropping_tables():
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        runpy.run_path(str(REVISION))["upgrade"]()
    sql = output.getvalue()
    assert "ALTER COLUMN user_id DROP NOT NULL" in sql
    assert "'profile'" in sql
    assert "DROP TABLE" not in sql
