"""Regression contract for legacy checkitem enum storage."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MIGRATION = ROOT / "src/api/langboard/migrations/versions/20260806132500-a4c79b2e1d63.py"


def test_checkitem_status_migration_matches_enum_like_varchar_storage() -> None:
    """Both historical enum columns must converge on current varchar storage."""

    source = MIGRATION.read_text(encoding="utf-8")

    assert 'down_revision: Union[str, None] = "09fd3bc91042"' in source
    assert source.count("TYPE VARCHAR USING status::text") == 2
    assert 'op.execute("DROP TYPE IF EXISTS checkitemstatus")' in source
    assert source.count("::checkitemstatus") == 2


def test_sqlite_status_migration_preserves_existing_values_and_replays():
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("status_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for table in ("checkitem", "checkitem_timer_record"):
            connection.execute(sa.text(f"CREATE TABLE {table} (id BIGINT PRIMARY KEY, status VARCHAR)"))
            connection.execute(sa.text(f"INSERT INTO {table} VALUES (1, 'Started'), (2, 'Paused'), (3, 'Stopped')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()
        migration.downgrade()
        for table in ("checkitem", "checkitem_timer_record"):
            assert connection.execute(sa.text(f"SELECT status FROM {table} ORDER BY id")).scalars().all() == [
                "Started", "Paused", "Stopped"
            ]
    engine.dispose()
