"""Exercise env.py's URL boundary without connecting to a database."""

import ast
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
import pytest
from alembic.config import Config
from langboard_shared.core.db.DbConfigHelper import DbConfigHelper
from sqlalchemy.engine import make_url


ENV_FILE = Path(__file__).resolve().parents[2] / "langboard/migrations/env.py"


@pytest.mark.parametrize("password", ["plain", "p@ss:word/with spaces", "100%secret", "%(missing)s", "한글密码"])
def test_migration_config_preserves_url_encoded_credentials(password: str) -> None:
    url = f"postgresql://fixture:{quote(password, safe='')}@localhost/fixture"
    config = Config()
    # Execute the production configuration statement against real Alembic
    # ConfigParser interpolation; do not run migrations or import the API.
    statement = next(
        node
        for node in ast.parse(ENV_FILE.read_text()).body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "config.set_main_option"
    )
    exec(
        compile(ast.Module(body=[statement], type_ignores=[]), str(ENV_FILE), "exec"),
        {"config": config, "DbConfigHelper": DbConfigHelper, "Env": SimpleNamespace(MAIN_DATABASE_URL=url)},
    )

    configured = config.get_main_option("sqlalchemy.url")
    assert configured == DbConfigHelper.get_sanitized_driver(url)
    assert config.get_section(config.config_ini_section)["sqlalchemy.url"] == configured
    parsed = make_url(configured)
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.password == password
