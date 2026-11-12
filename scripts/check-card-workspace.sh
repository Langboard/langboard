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


# Exercise mutation-owner atomicity and relationship ACLs before unrelated suites.
LANGBOARD_COUNT_TEST_DATABASE_URL="$LANGBOARD_TEST_DATABASE_URL" run_pytest src/api/tests/mcp_integration/test_checklist_update_atomic.py src/api/tests/card_workspace/test_relationship_scope_writes.py

# Verify native attachment authority and real bounded media decoding.
export LANGBOARD_FILE_TEST_DATABASE_URL="${LANGBOARD_FILE_TEST_DATABASE_URL:-$LANGBOARD_TEST_DATABASE_URL}"
command -v ffmpeg >/dev/null
uv run --no-sync ruff check src/shared/py/langboard_shared/domain/services/factory/CardAttachmentService.py src/api/langboard/media src/api/langboard/routes/board/BoardCardAttachmentStoryboardApi.py src/api/tests/card_visibility/test_attachment_authorized_read.py src/api/tests/card_visibility/test_attachment_storyboard.py src/api/tests/media/test_storyboard.py
run_pytest src/api/tests/card_visibility/test_attachment_authorized_read.py src/api/tests/card_visibility/test_attachment_storyboard.py src/api/tests/media/test_storyboard.py

# Verify legacy description conversion boundaries
run_pytest src/api/tests/contracts/test_content_block_migration.py
uv run --no-sync ruff check src/api/langboard/routes/mcp/McpApi.py src/api/langboard/card_workspace src/api/langboard/mcp_tools/CardMcp.py src/api/langboard/routes/board/BoardCardApi.py src/api/tests/card_workspace src/api/tests/mcp_integration/test_card_mcp_contract.py src/api/tests/mcp_integration/test_card_workspace_transport.py src/shared/py/langboard_shared/domain/services/factory/CardService.py src/shared/py/langboard_shared/domain/services/factory/CardRelationshipService.py src/shared/py/langboard_shared/domain/services/factory/ChecklistService.py src/shared/py/langboard_shared/domain/services/factory/CheckitemService.py
run_pytest src/shared/py/langboard_shared/domain/services/factory/CardContentBlockService_batch_test.py src/shared/py/langboard_shared/domain/services/factory/ChecklistService_batch_test.py src/api/tests/card_workspace/test_commands.py src/api/tests/card_workspace/test_queries.py src/api/tests/card_workspace/test_completion_checklist_service.py src/api/tests/card_workspace/test_graph_patch_route.py src/api/tests/card_workspace/test_card_relationship_concurrency.py src/api/tests/mcp_integration/test_card_mcp_contract.py src/api/tests/mcp_integration/test_card_workspace_transport.py src/api/tests/mcp_integration/test_checklist_actions.py

# Verify native priority persistence, current authority and MCP dispatch
LANGBOARD_COUNT_TEST_DATABASE_URL="${LANGBOARD_TEST_DATABASE_URL:?PostgreSQL service URL is required}" LANGBOARD_MIRROR_TEST_DATABASE_URL="$LANGBOARD_TEST_DATABASE_URL" run_pytest src/api/tests/card_priority
run_pytest src/api/tests/card_workspace/test_native_adapter.py src/api/tests/workflow_stages/test_work_queries.py src/api/tests/card_workspace/test_safe_datetime.py

# Verify native work-plan preview rejections on PostgreSQL
run_pytest src/api/tests/mcp_integration/test_work_plan_private_card.py -k 'native_cardification_preview_rejection_boundary and postgresql'
run_pytest src/api/tests/card_workspace/timers/
run_pytest src/api/tests/card_workspace/test_checklist_projection.py -k checklist_projection
run_pytest src/api/tests/card_workspace/test_linked_wikis.py
run_pytest src/api/tests/card_workspace/test_recent_card_availability.py src/shared/py/langboard_shared/infrastructure/repositories/factory/CardRepository_test.py src/api/tests/card_visibility/test_search_source_scope.py

# Verify PRIVATE relationship boundaries on SQLite and PostgreSQL
run_pytest src/api/tests/card_visibility/test_private_relationship_reads_wave6.py src/api/tests/card_visibility/test_bot_relationship_private_source.py src/api/tests/card_visibility/test_bot_private_snapshot.py

# Verify committed projections against replica lag
LANGBOARD_PROJECTION_TEST_DATABASE_URL="${LANGBOARD_TEST_DATABASE_URL:?PostgreSQL service URL is required}" run_pytest src/api/tests/card_workspace/test_committed_move_projection.py
run_pytest src/api/tests/core/test_snowflake_storage_safety.py src/api/tests/project_labels/test_builtin_global_labels.py

# Verify direct ID allocation and request idempotency on PostgreSQL
run_pytest src/api/tests/core/test_direct_snowflake_allocation.py src/api/tests/runtime/test_internal_bot_run.py src/api/tests/runtime/test_ollama_model_pull.py
run_pytest src/api/tests/search/test_keyword_search_connection.py

# Verify Schedule calendar and workload
run_pytest src/api/tests/card_visibility/test_schedule_calendar.py src/api/tests/card_visibility/test_schedule_workload.py

# Verify Schedule workload on native PostgreSQL
SCHEDULE_WORKLOAD_TEST_URL="$LANGBOARD_TEST_DATABASE_URL" run_pytest src/api/tests/card_visibility/test_schedule_workload.py

# Verify native automation summary and detail audiences
run_pytest src/api/tests/runtime/test_board_automation_runs.py src/api/tests/runtime/test_board_automation_loading.py

# Verify bounded Wiki, Relations and My Work contracts
run_pytest src/api/langboard/wiki_workspace src/api/tests/card_workspace/test_card_relation_summaries.py src/api/tests/card_workspace/test_card_relationship_graph.py src/api/tests/test_dashboard_my_work.py

# Verify Wiki generation race on native PostgreSQL
LANGBOARD_WIKI_TEST_DATABASE_URL="${LANGBOARD_TEST_DATABASE_URL:?PostgreSQL service URL is required}" run_pytest src/api/langboard/wiki_workspace/infrastructure_test.py -k postgresql

# Verify My Work snapshot and relation authority on native PostgreSQL
LANGBOARD_SUMMARY_TEST_DATABASE_URL="$LANGBOARD_TEST_DATABASE_URL" run_pytest src/api/tests/test_dashboard_my_work.py src/api/tests/card_workspace/test_card_relation_summaries.py

# Verify authoritative card change counts on native PostgreSQL
LANGBOARD_COUNT_TEST_DATABASE_URL="${LANGBOARD_TEST_DATABASE_URL:?PostgreSQL service URL is required}" run_pytest src/api/tests/card_workspace/test_work_plan_native_edit_concurrency.py src/api/tests/card_workspace/test_image_comment_native_edit_concurrency.py src/api/tests/card_workspace/test_compactor_native_edit_concurrency.py src/api/tests/card_workspace/test_card_change_counts.py src/api/tests/card_workspace/test_unread_card_summaries.py src/api/tests/card_workspace/test_dashboard_unread_counts.py

# Verify bounded legacy work query counts
run_pytest src/api/tests/dashboard/test_column_visibility_counts.py -k multipurpose
