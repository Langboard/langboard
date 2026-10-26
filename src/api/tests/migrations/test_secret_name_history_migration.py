"""Additive private locator migration preserves rows and refuses protection loss."""

import ast
import importlib.util
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db.DbEngine import DbEngine


ROOT = Path(__file__).resolve().parents[4]
MIGRATION = ROOT / "src/api/langboard/migrations/versions/20261026010000-cdef901b2345.py"


def migration(connection):
    spec = importlib.util.spec_from_file_location("name_history_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def test_exact_linear_head():
    revisions = {}
    for path in MIGRATION.parent.glob("*.py"):
        facts = {}
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                        facts[target.id] = ast.literal_eval(node.value)
        if "revision" in facts:
            revisions[facts["revision"]] = facts["down_revision"]
    parents = {parent for value in revisions.values() for parent in (value if isinstance(value, tuple) else (value,))}
    assert set(revisions) - parents == {"cdef901b2345"}
    assert revisions["cdef901b2345"] == "bdef890a1234"


def test_additive_nullable_and_guarded_downgrade(board):
    engine = DbEngine.get_main_engine()
    table = sa.Table(
        "secret_reference",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
    )
    with engine.begin() as connection:
        table.create(connection)
        connection.execute(table.insert().values(id=1, name="safe/existing"))
        module = migration(connection)
        module.upgrade()
        columns = {item["name"]: item for item in sa.inspect(connection).get_columns(table.name)}
        assert columns["protection_locator"]["nullable"]
        assert connection.execute(sa.text("SELECT name, protection_locator FROM secret_reference")).all() == [
            ("safe/existing", None)
        ]
        connection.execute(sa.text("UPDATE secret_reference SET protection_locator = 'opaque-vault-object'"))
        with pytest.raises(RuntimeError, match="Cannot discard retained secret name protection"):
            module.downgrade()
        assert "protection_locator" in {item["name"] for item in sa.inspect(connection).get_columns(table.name)}
        connection.execute(sa.text("UPDATE secret_reference SET protection_locator = NULL"))
        module.downgrade()
        assert "protection_locator" not in {item["name"] for item in sa.inspect(connection).get_columns(table.name)}
        assert connection.execute(sa.select(table)).all() == [(1, "safe/existing")]


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_postgresql_upgrade_transaction_rollback(board):
    engine = DbEngine.get_main_engine()
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE secret_reference (id BIGINT PRIMARY KEY, name TEXT NOT NULL)"))
        connection.execute(sa.text("INSERT INTO secret_reference VALUES (1, 'safe/existing')"))
    with engine.connect() as connection:
        transaction = connection.begin()
        migration(connection).upgrade()
        transaction.rollback()
        assert "protection_locator" not in {
            item["name"] for item in sa.inspect(connection).get_columns("secret_reference")
        }
        assert connection.execute(sa.text("SELECT name FROM secret_reference")).scalar_one() == "safe/existing"
