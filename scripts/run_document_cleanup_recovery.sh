#!/bin/bash -l

cd /app || exit 1
export SENTRY_AUTO_ENABLING_INTEGRATIONS=false
flock -n -E 0 /tmp/langboard-document-cleanup-recovery.lock /app/.venv/bin/python -m langboard_shared.tasks.docling.DocumentRecovery
