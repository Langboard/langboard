#!/bin/bash -l

cd /app
/app/.venv/bin/langboard run:notification:cron "$@"

exit 0
