"""Exercise the real migration graph against an empty database."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[4]


def test_merge_parents_do_not_include_their_ancestors():
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    for revision in script.walk_revisions():
        parents = revision._normalized_down_revisions
        for parent in parents:
            for other in parents:
                if other != parent:
                    ancestors = {item.revision for item in script.iterate_revisions(other, "base")}
                    assert parent not in ancestors, (revision.revision, parent, other)


def test_fresh_sqlite_upgrade_and_repeated_head_preserve_seed_data(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    env = {
        **os.environ,
        "PROJECT_NAME": "langboard",
        "PROJECT_SHORT_NAME": "lb",
        "MAIN_DATABASE_URL": database_url,
        "READONLY_DATABASE_URL": database_url,
        "PYTHONPATH": os.pathsep.join(
            str(ROOT / path)
            for path in (
                "src/shared/py",
                "src/api",
                tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["sources"]["langboard-sdk"]["path"],
            )
        ),
    }
    engine = sa.create_engine(database_url)
    try:
        snapshots = []
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            with engine.connect() as connection:
                tables = sa.inspect(connection).get_table_names()
                snapshots.append(
                    {
                        table: connection.execute(sa.text(f'SELECT * FROM "{table}"')).all()
                        for table in tables
                    }
                )
                assert {"card", "project", "project_label", "execution_receipt"} <= set(tables)
                foreign_keys = sa.inspect(connection).get_foreign_keys("project")
                assert any(key["referred_table"] == "organization" for key in foreign_keys)
                assert connection.execute(sa.text("SELECT COUNT(*) FROM project_template")).scalar_one() > 0
        assert snapshots[0] == snapshots[1]
    finally:
        engine.dispose()
