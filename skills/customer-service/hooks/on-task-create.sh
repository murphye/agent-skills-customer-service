#!/usr/bin/env bash
set -euo pipefail

# Marks the session as "tasks initialized" the first time TaskCreate is called.
# This flag is checked by require-tasks.sh to verify ordering.

EVENT=$(cat)
SESSION_ID=$(echo "$EVENT" | jq -r '.session_id // "default"')

# Dump raw stdin for debugging
echo "$INPUT" > /tmp/cs_hook_debug_on_task_create.json

# Try to extract session_id
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)

# Fallback: touch a generic flag if jq fails or session_id is missing
if [ -z "$SESSION_ID" ]; then
  touch "/tmp/cs_hook_fired"
  exit 0
fi

touch "/tmp/cs_tasks_${SESSION_ID}"
exit 0





POLICY="$(cd "$(dirname "$0")" && pwd)/policy.rego"
EVENT=$(cat)
[ -z "$EVENT" ] && exit 0

SESSION_ID=$(echo "$EVENT" | jq -r '.session_id // "default"')
STATE="/tmp/cs_workflow_${SESSION_ID}.json"

# Inject current state into the raw event, single OPA call
RESULT=$(echo "$EVENT" \
    | jq --argjson state "$(cat "$STATE" 2>/dev/null || echo '{}')" '. + {state: $state}' \
    | opa eval -d "$POLICY" -I 'data.cs.workflow.action' 2>/dev/null \
    | jq '.result[0].expressions[0].value // {}') || exit 0

# Cleanup if OPA says so, otherwise write state
if echo "$RESULT" | jq -e '.cleanup' >/dev/null 2>&1; then
    rm -f "$STATE"
else
    NEW_STATE=$(echo "$RESULT" | jq '.write_state // null')
    if [ "$NEW_STATE" != "null" ]; then
        echo "$NEW_STATE" > "$STATE"
    fi
fi

# Deny if OPA returned a message
DENY=$(echo "$RESULT" | jq -r '.deny_message // empty')
if [ -n "$DENY" ]; then
    echo "$DENY" >&2
    exit 2
fi




