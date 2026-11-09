#!/usr/bin/env bash
set -u

result_dir="${1:-/result}"
mkdir -p "$result_dir/artifacts"
status=0
if [[ "${2:-}" != --run ]]; then
  rm -f -- "$result_dir/result.json"
  : > "$result_dir/artifacts/phoenix-integration.log"
  # Leave time for the native collector before its 1,800-second execution fence.
  timeout --signal=TERM --kill-after=10s 1740s "$BASH" "$0" "$result_dir" --run 2>> "$result_dir/artifacts/phoenix-integration.log" || status=$?
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
    && PYTHONPATH=.:src/graph:src/api:src/shared/py uv run --project src/graph --no-sync python -m pytest -p no:tach -p src.api.tests.native_fixtures -q --junitxml="$result_dir/graph-contracts.xml" src/graph/tests/test_agent_loop.py src/graph/tests/test_bot_status_map.py src/graph/tests/test_graph_service_auth.py src/graph/tests/test_comment_compaction_draft.py src/graph/tests/test_graph_privacy.py src/graph/tests/test_history_authority.py src/graph/tests/test_board_history_authority.py src/graph/tests/test_chat_history_authority.py src/graph/tests/test_postgres_checkpoint_url.py \
    && bash scripts/check-card-workspace.sh "$result_dir" \
    && bash scripts/check-app-contracts.sh "$result_dir/app-contracts.xml" \
    && PYTHONPATH=. uv run pytest -p no:tach -q --junitxml="$result_dir/compactor-contracts.xml" src/api/tests/model_providers/test_compactor_profile_provisioning.py src/api/tests/model_providers/test_compaction_workflow_history.py src/api/tests/model_providers/test_compactor_board_authority.py src/api/tests/model_providers/test_comment_compaction_snapshot.py src/api/tests/model_providers/test_automatic_archive_command.py src/api/tests/model_providers/test_comment_compaction_command.py src/api/tests/model_providers/test_compaction_semantic_index.py src/api/tests/model_providers/test_comment_compaction_binding.py src/api/tests/model_providers/test_comment_compaction_store.py src/api/tests/migrations/test_compactor_board_grant_migration.py src/api/tests/migrations/test_compactor_principal_migration.py src/api/tests/migrations/test_workflow_compaction_schedule_migration.py src/api/tests/migrations/test_workflow_stage_residency_migration.py \
    && PYTHONPATH=. uv run pytest -p no:tach -q --junitxml="$result_dir/workflow-contracts.xml" src/api/tests/workflow_stages src/api/tests/card_workspace/test_queries.py src/api/tests/card_workspace/test_native_adapter.py src/api/tests/mcp_integration/test_user_mcp.py src/shared/py/langboard_shared/domain/services/factory/ProjectColumnService_test.py src/api/tests/webhooks/test_webhook_contract.py src/api/tests/webhooks/test_work_event_contract.py src/api/tests/card_visibility/test_notification_visibility.py src/api/tests/card_visibility/test_socket_dispatch_http.py src/api/tests/migrations/test_phoenix_canary_union_postgres.py src/api/tests/core/test_recovery_entrypoints.py src/api/tests/core/test_schema_import_cost.py src/api/tests/core/test_factory_dependency_boundary.py src/shared/py/langboard_shared/core/logger/LogFileHandler_test.py src/shared/py/langboard_shared/core/logger/Telemetry_test.py src/shared/py/langboard_shared/core/routing/AppExceptionHandlingRoute_test.py src/api/tests/project_templates/test_template_authorization_http.py src/api/tests/project_templates/test_native_atomic_creation.py src/api/tests/scim/test_project_role_projection_database.py src/api/tests/project_labels/test_title_label_integration.py
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
