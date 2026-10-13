#!/usr/bin/env bash
set -u

mkdir -p /result/artifacts
status=0
(
  : "${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && : "${LANGBOARD_OUTBOX_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && : "${LANGBOARD_EFFECT_TEST_DATABASE:?A disposable PostgreSQL fixture is required}" \
    && uv run python - <<'PY' \
    && uv run python scripts/sdk/verify_python_artifact.py \
    && uv run python -m unittest discover -s scripts/sdk -p verify_python_artifact_test.py \
    && node scripts/sdk/check_panel_artifact.mjs \
    && make test_socket_phoenix \
    && make lint_socket_phoenix \
    && make test_socket_phoenix_editor_cluster \
    && bash scripts/check-app-contracts.sh /result/app-contracts.xml \
    && PYTHONPATH=. uv run pytest -p no:tach -q --junitxml=/result/workflow-contracts.xml src/api/tests/workflow_stages src/api/tests/card_workspace/test_queries.py src/api/tests/card_workspace/test_native_adapter.py src/api/tests/mcp_integration/test_user_mcp.py src/shared/py/langboard_shared/domain/services/factory/ProjectColumnService_test.py src/api/tests/webhooks/test_webhook_contract.py src/api/tests/migrations/test_phoenix_canary_union_postgres.py src/shared/py/langboard_shared/core/logger/LogFileHandler_test.py src/api/tests/project_templates/test_template_authorization_http.py
import os
from sqlalchemy import create_engine, text

for name in ("LANGBOARD_FILE_TEST_DATABASE_URL", "LANGBOARD_OUTBOX_TEST_DATABASE_URL", "LANGBOARD_EFFECT_TEST_DATABASE"):
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

uv run --no-sync python - "$status" <<'PYRESULT' || exit 1
import json
import sys
from pathlib import Path

root = Path("/result")
reports = [name for name in ("app-contracts.xml", "workflow-contracts.xml") if (root / name).is_file()]
(root / "result.json").write_text(json.dumps({
    "schemaVersion": 1,
    "exitCode": int(sys.argv[1]),
    "reports": {"junit": reports, "artifacts": ["artifacts/phoenix-integration.log"]},
}) + "\n")
PYRESULT
exit "$status"
