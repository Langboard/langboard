# ruff: noqa: F811 - pytest resolves the imported fixture by parameter name.
"""Populated wiki alias migration retains rows and refuses destructive downgrade."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db.DbEngine import DbEngine
from src.api.tests.card_visibility.conftest import current_card as current_card


pytestmark = pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)


def test_populated_upgrade_repeat_and_guarded_downgrade(current_card):
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261025020000-facd678e9012.py"
    spec = spec_from_file_location("wiki_replacement_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = DbEngine.get_main_engine()
    with engine.begin() as db:
        db.execute(sa.text("CREATE TABLE project_wiki_attachment (id BIGINT PRIMARY KEY, filename TEXT NOT NULL)"))
        db.execute(sa.text("INSERT INTO project_wiki_attachment VALUES (1, 'retained.pdf')"))
        migration.op = Operations(MigrationContext.configure(db))
        migration.upgrade()
        migration.upgrade()
        assert db.execute(sa.text("SELECT filename, replaced_by_id FROM project_wiki_attachment")).one() == (
            "retained.pdf",
            None,
        )
        db.execute(sa.text("UPDATE project_wiki_attachment SET replaced_by_id = 2"))
        with pytest.raises(RuntimeError, match="retained wiki"):
            migration.downgrade()
        assert "replaced_by_id" in {col["name"] for col in sa.inspect(db).get_columns("project_wiki_attachment")}
        db.execute(sa.text("UPDATE project_wiki_attachment SET replaced_by_id = NULL"))
        migration.downgrade()
        assert db.execute(sa.text("SELECT filename FROM project_wiki_attachment")).scalar_one() == "retained.pdf"
