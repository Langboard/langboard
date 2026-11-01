#!/usr/bin/env bash
set -euo pipefail

profile=${PROFILE:?PROFILE is required}
export PROJECT_NAME="${PROJECT_NAME:-langboard}"
mkdir -p /result/artifacts

case "$profile" in
  api)
    command=(uv run --no-sync pytest -q --junitxml=/result/artifacts/api.xml \
      src/api/tests/runtime/test_app.py \
      src/shared/py/langboard_shared/core/routing/AppExceptionHandlingRoute_test.py \
      src/shared/py/langboard_shared/core/caching/RedisCache_test.py \
      src/shared/py/langboard_shared/core/broker/RedisBroker_test.py \
      src/api/tests/runtime/test_external_redis_compose.py \
      src/api/tests/runtime/test_external_postgres_compose.py \
      src/api/tests/runtime/test_quickstart_powershell.py \
      src/api/tests/runtime/test_quickstart_bash.py \
      src/api/tests/runtime/test_install_environment_bootstrap.py \
      src/api/tests/runtime/test_kafka_healthcheck_compose.py \
      src/api/tests/runtime/test_kafka_transport.py \
      src/api/tests/runtime/test_external_kafka_compose.py \
      src/api/tests/runtime/test_broadcast_envelope.py \
      src/api/tests/runtime/test_external_phoenix_compose.py \
      src/api/tests/runtime/test_prebuilt_compose.py \
      src/api/tests/runtime/test_external_phoenix_ingress.py \
      src/api/tests/runtime/test_project_read_bootstrap.py \
      src/api/tests/runtime/test_read_phase_timing.py \
      src/api/tests/runtime/test_column_read_side_effects.py \
      src/api/tests/identity/test_employee_membership_settings.py \
      src/api/tests/identity/test_account_revocation_auth.py \
      src/api/tests/identity/test_user_settings_sign_in_history.py \
      src/api/tests/scim/test_group_identity.py \
      src/api/tests/scim/test_instance_directory_audit.py \
      src/api/tests/scim/test_group_provenance.py \
      src/api/tests/identity/test_group_directory.py \
      src/api/tests/identity/test_group_directory_http.py \
      src/api/tests/identity/test_group_directory_management.py \
      src/api/tests/identity/test_organization_project_authority.py \
      src/api/tests/identity/test_instance_tenant_cutover.py \
      src/api/tests/card_workspace/test_board_creator_projection.py \
      src/api/tests/card_workspace/test_graph_creation_authority.py \
      src/api/tests/card_visibility/test_card_timeline.py \
      src/api/tests/mcp_integration/test_card_timeline_mcp.py \
      src/api/tests/card_visibility/test_card_activity_visibility.py \
      src/api/tests/card_workspace/test_board_enrichment_visibility.py \
      src/api/tests/card_workspace/timers/test_timer_batch.py \
      src/api/tests/card_workspace/test_card_board_summary.py \
      src/api/tests/runtime/test_board_chat_availability.py \
      src/api/tests/core/test_unprivileged_cron.py \
      src/api/tests/core/test_recovery_entrypoints.py \
      src/api/tests/core/test_schema_import_cost.py \
      src/api/tests/core/test_factory_dependency_boundary.py \
      src/api/tests/core/test_logger_tracebacks.py \
      src/api/tests/core/test_ip_whitelist.py \
      src/api/tests/runtime/test_shared_form_validation.py \
      src/api/tests/apps/test_user_app_preferences_http.py \
      src/api/tests/apps/test_board_filter_preferences_http.py \
      src/api/tests/apps/test_app_execution_requests.py \
      src/api/tests/apps/test_app_execution_ack.py \
      src/api/tests/apps/test_app_execution_lease.py \
      src/api/tests/apps/test_app_execution_start.py \
      src/api/tests/apps/test_card_agent_execution.py \
      src/api/tests/card_workspace/test_detail_body_deferral.py \
      src/api/tests/apps/test_card_signal_projection.py \
      src/api/tests/apps/test_card_management_mcp.py \
      src/api/tests/apps/test_card_app_resources_mcp.py \
      src/api/tests/mcp_integration/test_agent_policy.py \
      src/api/tests/mcp_integration/test_native_oauth.py \
      src/api/tests/mcp_integration/test_native_account_revocation.py \
      src/api/tests/mcp_integration/test_work_plan_private_card.py \
      src/api/tests/mcp_integration/test_work_plan_mcp.py \
      src/api/tests/mcp_integration/test_mcp_api.py \
      src/api/tests/contracts/test_datamodel_generation.py \
      src/api/tests/project_labels/test_title_label_integration.py \
      src/api/langboard/routes/board/BoardCardApi_authority_test.py \
      src/api/tests/runtime/test_socket_auth.py \
      src/api/tests/card_visibility/test_socket_dispatch_http.py \
      src/api/tests/mcp_integration/test_card_mcp_contract.py \
      src/api/tests/card_visibility/test_visibility_transition.py \
      src/api/tests/card_visibility/test_policy.py \
      src/api/tests/card_visibility/test_search_source_scope.py \
      src/api/tests/card_visibility/test_private_policy.py \
      src/api/tests/card_visibility/test_metadata_visibility.py \
      src/api/tests/card_visibility/test_metadata_projection.py \
      src/api/tests/card_visibility/test_internal_access_mode.py \
      src/api/tests/card_visibility/test_whisper_threads.py \
      src/api/tests/dashboard/test_project_activity_priority.py \
      src/api/tests/dashboard/test_board_seen_cursor.py \
      src/api/tests/card_workspace/test_document_mcp_read.py::test_transcription_publication_rechecks_current_primary_binding \
      src/api/tests/card_workspace/test_document_mcp_read.py::test_vector_cleanup_uses_current_primary_binding \
      src/api/tests/card_workspace/test_document_cleanup_outbox.py \
      src/api/tests/card_workspace/test_document_scope_cleanup.py \
      src/api/tests/card_workspace/test_document_embedding_cache.py \
      src/shared/py/langboard_shared/tasks/docling/DocumentEmbeddingCache_test.py \
      src/api/tests/card_workspace/test_board_snapshot_checklists.py \
      src/api/tests/card_workspace/test_unread_card_summaries.py \
      src/api/tests/card_workspace/test_dashboard_unread_counts.py \
      src/api/tests/card_workspace/test_work_state_column_projection.py \
      src/api/tests/card_workspace/test_document_cleanup_broker.py \
      src/api/tests/dashboard/test_related_activity_projection.py \
      src/api/tests/dashboard/test_column_visibility_counts.py \
      src/api/tests/card_visibility/test_schedule_calendar.py \
      src/api/tests/card_visibility/test_schedule_workload.py \
      src/api/tests/runtime/test_board_automation_runs.py \
      src/api/tests/model_providers/test_image_generation.py \
      src/api/tests/model_providers/test_comment_compaction_binding.py \
      src/api/tests/model_providers/test_comment_compaction_snapshot.py \
      src/api/tests/model_providers/test_comment_compaction_store.py \
      src/api/tests/model_providers/test_comment_compaction_command.py \
      src/api/tests/model_providers/test_compaction_semantic_index.py \
      src/api/tests/model_providers/test_avatar_application.py \
      src/api/langboard/mcp_tools/UserMcp_search_test.py \
      src/api/tests/card_workspace/test_work_plan_receipt_metadata.py \
      src/api/tests/card_visibility/test_socket_subscription_batch.py)
    ;;
  worker)
    command=(uv run --no-sync pytest -q --junitxml=/result/artifacts/worker.xml \
      src/shared/py/langboard_shared/tasks/docling/DocumentVision_test.py \
      src/shared/py/langboard_shared/tasks/docling/DoclingMetadataTask_test.py \
      src/shared/py/langboard_shared/tasks/docling/DocumentKeywords_test.py)
    ;;
  ui)
    command=(bash -c 'node scripts/verify-ui-strings.mjs && node --experimental-strip-types --test src/pages/BoardPage/components/card/comment/commentChronology.test.ts src/pages/DashboardPage/components/ProjectWorkload.test.ts src/pages/DashboardPage/components/ProjectDiscovery.test.ts src/controllers/api/dashboard/projectWorkloadSubscriptions.test.ts src/core/helpers/setupApiErrorHandler.test.ts && node --test src/controllers/api/dashboard/useGetProjects.test.mjs src/core/stores/AuthStore.session.test.mjs src/controllers/api/metadata/useGetMetadata.test.mjs src/controllers/api/board/useGetProjectDetails.test.mjs src/controllers/api/card/useArchiveCard.test.mjs src/pages/BoardPage/BoardWikiPage.test.mjs && yarn test:socket-runtime && yarn test:asset-retention && yarn test:schedule-admission')
    ;;
  api-postgres)
    : "${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}"
    export SCHEDULE_WORKLOAD_TEST_URL="$LANGBOARD_FILE_TEST_DATABASE_URL"
    export LANGBOARD_COUNT_TEST_DATABASE_URL="$LANGBOARD_FILE_TEST_DATABASE_URL"
    export LANGBOARD_MIRROR_TEST_DATABASE_URL="$LANGBOARD_FILE_TEST_DATABASE_URL"
    command=(uv run --no-sync pytest -q --junitxml=/result/artifacts/api-postgres.xml \
      src/api/tests/mcp_integration/test_native_account_revocation.py \
      src/api/tests/apps/test_app_connection_auth_postgres.py \
      src/api/tests/identity/test_user_owner_postgres.py \
      src/api/tests/scim/test_project_role_projection_database.py \
      src/api/tests/scim/test_instance_directory_audit.py \
      src/api/tests/scim/test_group_provenance.py \
      src/api/tests/identity/test_group_directory.py \
      src/api/tests/identity/test_group_directory_http.py \
      src/api/tests/identity/test_group_directory_management.py \
      src/api/tests/identity/test_organization_project_authority.py \
      src/api/tests/identity/test_instance_tenant_cutover.py \
      src/api/tests/project_labels/test_title_label_integration.py \
      src/api/tests/apps/test_user_app_preferences_http.py \
      src/api/tests/apps/test_board_filter_preferences_http.py \
      src/api/tests/apps/test_app_execution_requests.py \
      src/api/tests/apps/test_app_execution_ack.py \
      src/api/tests/apps/test_app_execution_lease.py \
      src/api/tests/apps/test_app_execution_start.py \
      src/api/tests/apps/test_card_agent_execution.py \
      src/api/tests/card_workspace/test_detail_body_deferral.py \
      src/api/tests/apps/test_card_signal_projection.py \
      src/api/tests/apps/test_card_management_mcp.py \
      src/api/tests/apps/test_card_app_resources_mcp.py \
      src/api/tests/mcp_integration/test_work_plan_private_card.py \
      src/api/tests/migrations/test_phoenix_canary_union_postgres.py \
      src/api/tests/card_visibility/test_socket_dispatch_http.py \
      src/api/tests/card_visibility/test_socket_subscription_batch.py \
      src/api/tests/card_visibility/test_metadata_projection.py \
      src/api/tests/card_visibility/test_internal_access_mode.py \
      src/api/tests/card_visibility/test_whisper_threads.py \
      src/api/tests/dashboard/test_project_activity_priority.py \
      src/api/tests/card_workspace/test_work_plan_native_edit_concurrency.py \
      src/api/tests/card_workspace/test_image_comment_native_edit_concurrency.py \
      src/api/tests/card_workspace/test_compactor_native_edit_concurrency.py \
      src/api/tests/dashboard/test_board_seen_cursor.py \
      src/api/tests/card_workspace/test_document_mcp_read.py::test_transcription_publication_rechecks_current_primary_binding \
      src/api/tests/card_workspace/test_document_mcp_read.py::test_vector_cleanup_uses_current_primary_binding \
      src/api/tests/card_workspace/test_document_cleanup_outbox.py \
      src/api/tests/card_workspace/test_document_scope_cleanup.py \
      src/api/tests/card_workspace/test_document_embedding_cache.py \
      src/shared/py/langboard_shared/tasks/docling/DocumentEmbeddingCache_test.py \
      src/api/tests/card_workspace/test_board_creator_projection.py \
      src/api/tests/card_workspace/test_graph_creation_authority.py \
      src/api/tests/card_visibility/test_card_timeline.py \
      src/api/tests/mcp_integration/test_card_timeline_mcp.py \
      src/api/tests/card_visibility/test_card_activity_visibility.py \
      src/api/tests/card_workspace/test_board_snapshot_checklists.py \
      src/api/tests/card_workspace/test_unread_card_summaries.py \
      src/api/tests/card_workspace/test_dashboard_unread_counts.py \
      src/api/tests/card_workspace/test_work_state_column_projection.py \
      src/api/tests/card_workspace/test_document_cleanup_broker.py \
      src/api/tests/dashboard/test_related_activity_projection.py \
      src/api/tests/dashboard/test_column_visibility_counts.py \
      src/api/tests/card_visibility/test_schedule_calendar.py \
      src/api/tests/card_visibility/test_schedule_workload.py \
      src/api/tests/runtime/test_board_automation_runs.py \
      src/api/tests/model_providers/test_image_generation.py \
      src/api/tests/model_providers/test_comment_compaction_binding.py \
      src/api/tests/model_providers/test_comment_compaction_snapshot.py \
      src/api/tests/model_providers/test_comment_compaction_store.py \
      src/api/tests/model_providers/test_comment_compaction_command.py \
      src/api/tests/model_providers/test_compaction_semantic_index.py \
      src/api/tests/model_providers/test_avatar_application.py \
      src/api/langboard/mcp_tools/UserMcp_search_test.py \
      src/api/tests/card_workspace/timers/ \
      src/api/tests/card_visibility/test_search_source_scope.py)
    ;;
  work-plan-lock-cost)
    : "${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}"
    command=(env LANGBOARD_WORK_PLAN_LOCK_BENCHMARK=1 LANGBOARD_WORK_PLAN_LOCK_RESULT_DIR=/result/artifacts \
      uv run --no-sync pytest -q --junitxml=/result/artifacts/work-plan-lock-cost.xml \
      src/api/tests/card_workspace/test_work_plan_lock_cost_postgres.py)
    ;;
  *)
    echo "Unsupported validation profile: $profile" >&2
    exit 2
    ;;
esac

status=0
if ("${command[@]}") 2>&1 | tee "/result/artifacts/${profile}-validation.log"; then
  status=0
else
  status=$?
fi

reports='[]'
artifacts="[\"artifacts/${profile}-validation.log\"]"
if [[ -f "/result/artifacts/${profile}.xml" ]]; then
  reports="[\"artifacts/${profile}.xml\"]"
fi
if [[ "$profile" == work-plan-lock-cost ]]; then
  artifacts=$(python3 -c 'import json,pathlib; print(json.dumps(["artifacts/work-plan-lock-cost-validation.log", *["artifacts/" + p.name for p in sorted(pathlib.Path("/result/artifacts").glob("work-plan-lock-*.json"))]]))')
fi
printf '{"schemaVersion":1,"exitCode":%s,"reports":{"junit":%s,"artifacts":%s}}\n' \
  "$status" "$reports" "$artifacts" > /result/result.json
# The result stage must complete so the central contract can inspect exitCode.
# Setup failures above still terminate the stage through `set -e`.
if [[ "$profile" == api-postgres || "$profile" == work-plan-lock-cost ]]; then
  # Isolated integration uses the actual process exit code as well as the report.
  exit "$status"
fi
exit 0
