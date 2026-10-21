#!/bin/bash -l

cd /app || exit 1
flock -n -E 0 /tmp/langboard-workflow-compaction.lock /app/.venv/bin/python -m langboard_shared.tasks.bots.WorkflowCompactionTask
