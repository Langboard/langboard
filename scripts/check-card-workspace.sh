#!/usr/bin/env bash
set -eu

# The direct CI job and shared native integration run the same commands.
export PROJECT_NAME="${PROJECT_NAME:-langboard}"
export LANGBOARD_TEST_DATABASE_URL="${LANGBOARD_TEST_DATABASE_URL:-${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}}"
result_dir="${1:-$(mktemp -d)}"
mkdir -p "$result_dir"
report_number=0
run_pytest() {
  report_number=$((report_number + 1))
  uv run --no-sync pytest -q --junitxml="$result_dir/card-workspace-$report_number.xml" "$@"
}

uv run --no-sync ruff check src/api/langboard/routes/mcp/McpApi.py src/api/langboard/card_workspace src/api/langboard/mcp_tools/CardMcp.py src/api/langboard/routes/board/BoardCardApi.py src/api/tests/card_workspace src/api/tests/mcp_integration/test_card_mcp_contract.py src/api/tests/mcp_integration/test_card_workspace_transport.py src/shared/py/langboard_shared/domain/services/factory/CardService.py src/shared/py/langboard_shared/domain/services/factory/CardRelationshipService.py src/shared/py/langboard_shared/domain/services/factory/ChecklistService.py src/shared/py/langboard_shared/domain/services/factory/CheckitemService.py
run_pytest src/shared/py/langboard_shared/domain/services/factory/CardContentBlockService_batch_test.py src/shared/py/langboard_shared/domain/services/factory/ChecklistService_batch_test.py src/api/tests/card_workspace/test_commands.py src/api/tests/card_workspace/test_queries.py src/api/tests/card_workspace/test_completion_checklist_service.py src/api/tests/card_workspace/test_graph_patch_route.py src/api/tests/card_workspace/test_card_relationship_concurrency.py src/api/tests/mcp_integration/test_card_mcp_contract.py src/api/tests/mcp_integration/test_card_workspace_transport.py
run_pytest src/api/tests/card_workspace/test_native_adapter.py src/api/tests/workflow_stages/test_work_queries.py
run_pytest src/api/tests/card_workspace/timers/
run_pytest src/api/tests/card_workspace/test_checklist_projection.py src/api/tests/card_workspace/test_native_adapter.py -k checklist_projection
run_pytest src/api/tests/card_workspace/test_linked_wikis.py
run_pytest src/api/tests/card_workspace/test_recent_card_availability.py src/shared/py/langboard_shared/infrastructure/repositories/factory/CardRepository_test.py src/api/tests/card_visibility/test_search_source_scope.py
run_pytest src/api/tests/core/test_snowflake_storage_safety.py src/api/tests/project_labels/test_builtin_global_labels.py
run_pytest src/api/tests/search/test_keyword_search_connection.py
