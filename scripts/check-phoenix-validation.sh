#!/usr/bin/env bash
set -u

mkdir -p /result/artifacts
status=0
(
  : "${LANGBOARD_FILE_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && : "${LANGBOARD_OUTBOX_TEST_DATABASE_URL:?A disposable PostgreSQL fixture is required}" \
    && make test_socket_phoenix \
    && make lint_socket_phoenix \
    && make test_socket_phoenix_editor_cluster \
    && uv run pytest -p no:tach -q src/api/tests/apps
) > /result/artifacts/phoenix-integration.log 2>&1 || status=$?

printf '{"schemaVersion":1,"exitCode":%s,"reports":{"junit":[],"artifacts":["artifacts/phoenix-integration.log"]}}\n' \
  "$status" > /result/result.json
exit "$status"
