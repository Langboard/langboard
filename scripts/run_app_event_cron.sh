#!/bin/bash
set -e
cd /app
export SENTRY_AUTO_ENABLING_INTEGRATIONS=false
/app/.venv/bin/python -m langboard_shared.tasks.webhooks.AppEventDeliveryWorker
