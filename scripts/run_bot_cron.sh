#!/bin/bash -l

cd /app
/app/.venv/bin/langboard run:bot:cron "$@"

exit 0
