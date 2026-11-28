#!/usr/bin/env bash
set -u

result_dir="${1:-/result}"
mkdir -p "$result_dir/artifacts"
status=0
if [[ "${2:-}" != --run ]]; then
  rm -f -- "$result_dir/result.json"
  : > "$result_dir/artifacts/phoenix-integration.log"
  # Leave time for the native collector before its 1,800-second execution fence.
  python_bin=$(uv run --no-sync python -c 'import sys; print(sys.executable)') || status=$?
  if [[ "$status" == 0 ]]; then
    timeout --signal=TERM --kill-after=10s 1740s "$python_bin" scripts/image_build/run_phoenix_contracts.py "$0" "$result_dir" >> "$result_dir/artifacts/phoenix-integration.log" 2>&1 || status=$?
  fi
else
(
  : "${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && : "${LANGBOARD_OUTBOX_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && : "${LANGBOARD_EFFECT_TEST_DATABASE:?A disposable PostgreSQL fixture is required}" \
    && : "${LANGBOARD_TEMPLATE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && test -s conftest.py \
    && test -s scripts/sdk/verify_python_artifact_test.py \
    && test -s src/shared/py/langboard_shared/domain/services/factory/ProjectColumnService_test.py \
    && test -s src/shared/py/langboard_shared/core/logger/LogFileHandler_test.py \
    && test -s src/shared/py/langboard_shared/core/logger/Telemetry_test.py \
    && test -s src/shared/py/langboard_shared/core/routing/AppExceptionHandlingRoute_test.py \
    && uv run python - <<'PY' \
    && uv run python scripts/sdk/verify_python_artifact.py \
    && uv run python -m unittest discover -s scripts/sdk -p verify_python_artifact_test.py \
    && uv run --no-sync python -m unittest discover -s scripts/image_build -v \
    && node scripts/sdk/check_panel_artifact.mjs \
    && make test_socket_phoenix_integration \
    && make lint_socket_phoenix \
    && make test_socket_phoenix_editor_cluster \
    && PYTHONPATH=.:src/graph:src/api:src/shared/py uv run --project src/graph --no-sync python -m pytest -p no:tach -p src.api.tests.native_fixtures -q --junitxml="$result_dir/graph-contracts.xml" src/graph/tests/test_agent_loop.py src/graph/tests/test_bot_status_map.py src/graph/tests/test_graph_service_auth.py src/graph/tests/test_comment_compaction_draft.py src/graph/tests/test_graph_privacy.py src/graph/tests/test_history_authority.py src/graph/tests/test_board_history_authority.py src/graph/tests/test_chat_history_authority.py src/graph/tests/test_postgres_checkpoint_url.py
import os
from sqlalchemy import create_engine, text

for name in ("LANGBOARD_FILE_TEST_DATABASE_URL", "LANGBOARD_OUTBOX_TEST_DATABASE_URL", "LANGBOARD_EFFECT_TEST_DATABASE", "LANGBOARD_TEMPLATE_TEST_DATABASE_URL"):
    engine = create_engine(os.environ[name], connect_args={"connect_timeout": 10})
    try:
        if engine.dialect.name != "postgresql":
            raise RuntimeError(f"{name} must select PostgreSQL")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
PY
) > "$result_dir/artifacts/phoenix-integration.log" 2>&1 || status=$?
  exit "$status"
fi

uv run --no-sync python - "$status" "$result_dir" <<'PYRESULT' || exit 1
import json
import sys
from pathlib import Path

root = Path(sys.argv[2])
reports = [name for name in ("graph-contracts.xml", "app-contracts.xml", "compactor-contracts.xml", "workflow-contracts.xml") if (root / name).is_file()]
reports.extend(path.name for path in sorted(root.glob("card-workspace-*.xml")))
pending = root / "result.pending.json"
pending.write_text(json.dumps({
    "schemaVersion": 1,
    "exitCode": int(sys.argv[1]),
    "reports": {"junit": reports, "artifacts": ["artifacts/phoenix-integration.log"]},
}) + "\n")
pending.replace(root / "result.json")
PYRESULT
exit "$status"
