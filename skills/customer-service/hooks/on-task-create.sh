#!/usr/bin/env bash
set -euo pipefail

# Marks the session as "tasks initialized" the first time TaskCreate is called.
# This flag is checked by require-tasks.sh to verify ordering.

EVENT=$(cat)
SESSION_ID=$(echo "$EVENT" | jq -r '.session_id // "default"')

touch "/tmp/cs_tasks_${SESSION_ID}"

exit 0