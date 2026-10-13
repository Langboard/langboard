#!/usr/bin/env bash
set -u

mkdir -p /result/artifacts
status=0
(
  : "${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && : "${LANGBOARD_OUTBOX_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && uv run python - <<'PY' \
    && make test_socket_phoenix \
    && make lint_socket_phoenix \
    && make test_socket_phoenix_editor_cluster \
    && uv run pytest -p no:tach -q --junitxml=/result/app-contracts.xml src/api/tests/apps
import os
from sqlalchemy import create_engine, text

for name in ("LANGBOARD_FILE_TEST_DATABASE_URL", "LANGBOARD_OUTBOX_TEST_DATABASE_URL"):
    engine = create_engine(os.environ[name], connect_args={"connect_timeout": 10})
    try:
        if engine.dialect.name != "postgresql":
            raise RuntimeError(f"{name} must select PostgreSQL")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
PY
) > /result/artifacts/phoenix-integration.log 2>&1 || status=$?

junit='[]'
if [[ -f /result/app-contracts.xml ]]; then
  junit='["app-contracts.xml"]'
fi
printf '{"schemaVersion":1,"exitCode":%s,"reports":{"junit":%s,"artifacts":["artifacts/phoenix-integration.log"]}}\n' \
  "$status" "$junit" > /result/result.json
exit "$status"
