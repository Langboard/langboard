#!/usr/bin/env bash
set -eu

profile=${PROFILE:?PROFILE is required}
mkdir -p /result/artifacts

case "$profile" in
  api)
    export PROJECT_NAME="${PROJECT_NAME:-langboard}"
    command=(uv run --no-sync pytest -q --junitxml=/result/artifacts/api.xml \
      src/api/tests/runtime/test_app.py \
      src/shared/py/langboard_shared/core/caching/RedisCache_test.py \
      src/api/tests/runtime/test_project_read_bootstrap.py \
      src/api/tests/runtime/test_board_chat_availability.py \
      src/api/tests/core/test_unprivileged_cron.py \
      src/api/tests/core/test_logger_tracebacks.py \
      src/api/tests/core/test_ip_whitelist.py \
      src/api/tests/runtime/test_shared_form_validation.py \
      src/api/tests/contracts/test_datamodel_generation.py \
      src/api/langboard/routes/board/BoardCardApi_authority_test.py \
      src/api/tests/runtime/test_socket_auth.py \
      src/api/tests/card_visibility/test_socket_dispatch_http.py \
      src/api/tests/mcp_integration/test_card_mcp_contract.py \
      src/api/tests/card_visibility/test_visibility_transition.py \
      src/api/tests/card_visibility/test_policy.py \
      src/api/tests/card_visibility/test_private_policy.py \
      src/api/tests/card_visibility/test_metadata_visibility.py \
      src/api/tests/card_visibility/test_metadata_projection.py \
      src/api/tests/dashboard/test_project_activity_priority.py \
      src/api/tests/dashboard/test_related_activity_projection.py \
      src/api/tests/card_workspace/test_work_plan_receipt_metadata.py \
      src/api/tests/card_visibility/test_socket_subscription_batch.py)
    ;;
  worker)
    command=(uv run --no-sync pytest -q --junitxml=/result/artifacts/worker.xml \
      src/shared/py/langboard_shared/tasks/docling/DocumentVision_test.py \
      src/shared/py/langboard_shared/tasks/docling/DocumentKeywords_test.py)
    ;;
  ui)
    command=(bash -c 'node --experimental-strip-types --test src/pages/DashboardPage/components/ProjectWorkload.test.ts src/core/helpers/setupApiErrorHandler.test.ts && node --test src/core/stores/AuthStore.session.test.mjs src/controllers/api/metadata/useGetMetadata.test.mjs src/controllers/api/board/useGetProjectDetails.test.mjs && yarn test:socket-runtime && yarn test:asset-retention')
    ;;
  api-postgres)
    export PROJECT_NAME="${PROJECT_NAME:-langboard}"
    : "${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}"
    command=(uv run --no-sync pytest -q --junitxml=/result/artifacts/api-postgres.xml \
      src/api/tests/card_visibility/test_socket_dispatch_http.py \
      src/api/tests/card_visibility/test_socket_subscription_batch.py \
      src/api/tests/card_visibility/test_metadata_projection.py \
      src/api/tests/dashboard/test_project_activity_priority.py \
      src/api/tests/dashboard/test_related_activity_projection.py \
      src/api/tests/card_workspace/timers/)
    ;;
  *)
    echo "Unsupported validation profile: $profile" >&2
    exit 2
    ;;
esac

status=0
if ("${command[@]}") >"/result/artifacts/${profile}-validation.log" 2>&1; then
  status=0
else
  status=$?
fi

reports='[]'
if [[ -f "/result/artifacts/${profile}.xml" ]]; then
  reports="[\"artifacts/${profile}.xml\"]"
fi
printf '{"schemaVersion":1,"exitCode":%s,"reports":{"junit":%s,"artifacts":["artifacts/%s-validation.log"]}}\n' \
  "$status" "$reports" "$profile" > /result/result.json
# The result stage must complete so the central contract can inspect exitCode.
# Setup failures above still terminate the stage through `set -e`.
if [[ "$profile" == api-postgres ]]; then
  # Isolated integration uses the actual process exit code as well as the report.
  exit "$status"
fi
exit 0
