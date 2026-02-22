#!/usr/bin/env bash
# PostToolUse: mcp__orders__.* | mcp__tickets__.*
#
# After any order or ticket API call, checks whether the task graph was
# initialized first. If not, injects corrective feedback so the agent
# creates the task graph before continuing.
#
# Note: PostToolUse cannot block the call that already happened, but the
# feedback message (exit 2) is injected into the agent's context, causing
# it to pause and correct course.

EVENT=$(cat)
SESSION_ID=$(echo "$EVENT" | jq -r '.session_id // "default"')

FLAG_FILE="/tmp/cs_tasks_${SESSION_ID}"

if [ ! -f "$FLAG_FILE" ]; then
  echo "Workflow violation: you called an order or ticket API before initializing the task graph."
  echo "Per the customer-service skill, TaskCreate must be called to set up the State Tracker"
  echo "and Phase 1 tasks before any order or ticket API is used."
  echo ""
  echo "The data from this API call is still available — please now:"
  echo "  1. Call TaskCreate for the State Tracker and mark it in_progress"
  echo "  2. Call TaskCreate for Intake, Classify Intent, Attempt Resolution, and Confidence Check"
  echo "     with the correct addBlockedBy dependencies"
  echo "  3. Continue the workflow from Intake using the data already retrieved"
  exit 2
fi

exit 0
