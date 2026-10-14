#!/usr/bin/env bash
set -eu

report=${1:-app-contracts.xml}
export PYTHONPATH=src/api/tests/apps${PYTHONPATH:+:$PYTHONPATH}
uv run --no-sync pytest -p no:tach -o log_cli=false -o faulthandler_timeout=60 -q --junitxml="$report" \
  src/api/tests/apps/test_signal_inbox.py::test_registered_external_signal_policy_drives_inbox_and_card_evidence \
  src/api/tests/apps/test_app_package.py \
  src/api/tests/apps/test_app_package_http.py \
  src/api/tests/apps/test_workflow_api.py \
  src/api/tests/apps/test_workflow_http.py \
  src/api/tests/apps/test_external_provider_signal_contracts.py \
  src/api/tests/apps/test_app_card_interactions.py \
  src/api/tests/apps/test_app_card_creation.py \
  src/api/tests/card_visibility/test_creation_visibility.py \
  src/api/tests/card_visibility/test_visibility_transition.py \
  src/api/tests/card_visibility/test_policy.py \
  src/api/tests/card_visibility/test_private_policy.py \
  src/api/tests/apps/test_app_card_archive_migration.py \
  src/api/tests/apps/test_app_retained_history_migrations.py \
  src/api/tests/apps/test_app_card_archive_delivery.py \
  src/api/tests/apps/test_card_app_ownership.py \
  src/api/tests/apps/test_connection_ownership_revision.py \
  src/api/tests/apps/test_app_event_destination.py \
  src/api/tests/apps/test_app_event_delivery.py \
  src/api/tests/apps/test_app_event_http.py \
  src/api/tests/apps/test_app_execution_control.py \
  src/api/tests/webhooks/test_execution_readiness_uow_postgres.py \
  src/api/tests/apps/test_app_governance.py \
  src/api/tests/apps/test_sdk_app_registry_http.py \
  src/api/tests/apps/test_app_composition.py \
  src/api/tests/apps/test_binding_storage.py \
  src/api/tests/apps/test_sdk_resources_http.py \
  src/api/tests/apps/test_registered_connection_context.py
