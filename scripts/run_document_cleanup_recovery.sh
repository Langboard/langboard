#!/bin/bash -l

cd /app || exit 1
flock -n -E 0 /tmp/langboard-document-cleanup-recovery.lock /app/.venv/bin/langboard run:document-cleanup:recover
