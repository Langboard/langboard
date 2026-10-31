"""Real database preservation of legacy and bound native approval rows."""

import importlib.util
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from test_decision_flow import native  # noqa: F401


def test_approval_binding_migration_preserves_legacy_rows_on_both_databases(native, tmp_path):  # noqa: F811
    source_engine = native[0]
    engine = source_engine
    if engine.dialect.name == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'legacy-approvals.sqlite'}")
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024120000-8abc901d2345.py"
    spec = importlib.util.spec_from_file_location("approval_preservation_delta", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.connect() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
            transaction = connection.begin()
            try:
                if engine.dialect.name == "postgresql":
                    schema = "approval_preservation_" + uuid4().hex
                    connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
                    connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
                connection.exec_driver_sql("CREATE TABLE decision_case(id BIGINT PRIMARY KEY)")
                connection.exec_driver_sql(
                    "CREATE TABLE graph_approval_request(id BIGINT PRIMARY KEY, thread_id TEXT NOT NULL)"
                )
                connection.exec_driver_sql("INSERT INTO graph_approval_request VALUES (1,'retained')")
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                assert connection.exec_driver_sql(
                    "SELECT id,thread_id,decision_case_id FROM graph_approval_request"
                ).one() == (1, "retained", None)
                connection.exec_driver_sql("INSERT INTO graph_approval_request VALUES (2,'legacy',NULL)")
                connection.exec_driver_sql("INSERT INTO decision_case VALUES (42)")
                connection.exec_driver_sql("UPDATE graph_approval_request SET decision_case_id=42 WHERE id=1")
                for value in (42, 99):
                    with pytest.raises(IntegrityError):
                        with connection.begin_nested():
                            connection.exec_driver_sql(
                                "UPDATE graph_approval_request SET decision_case_id=%s WHERE id=2" % value
                            )
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(RuntimeError, match="Cannot discard"):
                        migration.downgrade()
                assert connection.exec_driver_sql(
                    "SELECT id,thread_id,decision_case_id FROM graph_approval_request ORDER BY id"
                ).all() == [(1, "retained", 42), (2, "legacy", None)]
                connection.exec_driver_sql("UPDATE graph_approval_request SET decision_case_id=NULL")
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                assert connection.exec_driver_sql(
                    "SELECT id,thread_id FROM graph_approval_request ORDER BY id"
                ).all() == [(1, "retained"), (2, "legacy")]
            finally:
                transaction.rollback()
    finally:
        if engine is not source_engine:
            engine.dispose()
