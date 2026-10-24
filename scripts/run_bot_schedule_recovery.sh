#!/bin/bash
set -e
cd /app
/app/.venv/bin/langboard run:bot:cron recover
