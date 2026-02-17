# Agent Skill Test Harness

A simple, `claude` CLI-powered test runner for multi-turn agent skill conversations.

## How it works

```
┌─────────────────────────────────────────────────────────────────┐
│  YAML Test Plan                                                 │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐   │
│  │ Customer 1 │→│ Agent     │→│ Customer 2 │→│ Agent     │→…  │
│  └───────────┘  └───────────┘  └───────────┘  └───────────┘   │
└────────────────────────┬────────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │ Tool-call    │ │ Outcome      │ │ LLM-as-judge │
  │ regex checks │ │ assertions   │ │ quality eval │
  └──────────────┘ └──────────────┘ └──────────────┘
```

Each customer message is sent to `claude -p` with `--output-format stream-json`.
The session is continued across turns via `--resume <session_id>`.
After all turns complete, three layers of assertions run:

| Layer                | How it works                              | When to use                          |
|---------------------|-------------------------------------------|--------------------------------------|
| **Tool-call patterns** | Regex against MCP tool call commands      | Verify specific MCP tools were called |
| **Outcome checks**     | String/bool search across full transcript | Verify end-state (escalated, refunded, etc.) |
| **Quality checks**     | Separate `claude -p` call as LLM judge   | Tone, politeness, explanation quality |

## Quick start

```bash
# Install the one dependency
pip install pyyaml

# Run a single test
python run_test.py scenarios/order_status_happy.yaml --skill-dir /path/to/your/skill

# Run all tests in a directory
python run_test.py scenarios/ -d /path/to/your/skill

# Verbose mode (shows CLI commands and stderr)
python run_test.py scenarios/ -d /path/to/your/skill --verbose

# Generate a JSON report
python run_test.py scenarios/ -d /path/to/your/skill --json-report results.json

# Pass extra flags to claude
python run_test.py scenarios/escalate_max_retries.yaml \
  --extra-flags --model sonnet --max-turns 20
```

## YAML test plan format

```yaml
name: my-test                          # Test identifier
description: >                         # What this test verifies
  One-liner or paragraph.

turns:                                 # Conversation flow
  - role: customer                     # Send a customer message
    message: "Hi, I need help…"

  - role: await_agent                  # Marker: agent responds (automatic)

  - role: customer                     # Next customer follow-up
    message: "That doesn't work…"

  - role: await_agent

# ── Assertions (all optional) ──

expected_tool_calls:                   # Regex against MCP tool calls
  must_include:
    - pattern: "mcp__order-api__lookup_customer"
  must_not_include:
    - pattern: "mcp__order-api__refund"

expected_outcomes:                     # Key-value checks on transcript
  escalated: true                      # bool → domain-specific tool check
  customer_identified: "C-1001"        # string → exact match in transcript
  refund_processed: false

quality_checks:                        # LLM-as-judge natural language
  - "Agent greeted customer by name"
  - "Agent explained the refund policy"
```

## MCP tool call matching

The agent uses MCP server tools instead of CLI scripts. Tool calls appear in
the test harness as reconstructed strings like:

```
mcp__order-api__lookup_customer email=bob.m@example.com
mcp__ticket-api__create_ticket customer_id=C-1001 category=refund ...
mcp__order-api__refund order_id=ORD-5001 amount=129.99 reason=Defective product
```

Write `expected_tool_calls` patterns as regex matching against these strings.

## State reset between tests

Before each test, the harness calls both `mcp__order-api__reset_state` and
`mcp__ticket-api__reset_state` to restore seed data. This ensures test
isolation — refunds processed in one test don't affect the next.

## Architecture decisions

**Why `stream-json` over `json`?**
`stream-json` gives us individual messages (including `tool_use` blocks) as they stream in. The final `json` output only gives the collapsed result. We need tool-level granularity.

**Why `--resume` for multi-turn?**
Claude Code sessions persist across calls. We capture the `session_id` from the first turn's output and pass `--resume <id>` for all subsequent turns. This maintains the full conversation context.

**Why `--dangerously-skip-permissions`?**
In test/CI environments, there's nobody to click "allow" on permission prompts. This flag lets the agent run tools freely. Your skill's CLAUDE.md and settings.json should already define the allowed tools.

**Why a separate LLM call for quality checks?**
Deterministic regex checks catch structural correctness (did the agent call the right MCP tool?). But "did the agent sound empathetic?" requires judgment. A second `claude -p` call with a judge prompt evaluates the full transcript against natural-language criteria.

## CI integration

```bash
#!/bin/bash
set -e
python run_test.py scenarios/ \
  --skill-dir ./my-skill \
  --json-report test-results.json

# Exit code is 0 if all pass, 1 if any fail
```

The JSON report at `test-results.json` contains structured results suitable for CI dashboards.

## Extending the harness

Some ideas for more sophisticated testing:

- **Parameterized tests**: Add a `variables` block to YAML and template customer messages with `{order_id}`, `{email}`, etc.
- **Snapshot testing**: Save the full transcript and diff against a known-good baseline.
- **Cost tracking**: Parse `total_cost_usd` from the result message and fail if a test exceeds a budget.
- **Parallel execution**: Run independent tests in parallel with `&` / `xargs -P`.
