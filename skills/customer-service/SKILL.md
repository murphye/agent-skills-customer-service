---
name: customer-service
description: >
  Handle customer service interactions end-to-end: intake requests, look up
  accounts and orders, diagnose issues, apply resolutions (refunds, status
  updates), escalate to humans when needed, and close tickets. Use when the
  user presents a customer support scenario or asks you to role-play as a
  customer service agent.
license: MIT
compatibility: Requires MCP servers (orders, tickets) configured in .mcp.json. Requires Claude Code ≥ v2.1.16 (Tasks API). Designed for Claude Code.
metadata:
  author: agent-skills-demo
  version: "3.0"
  category: demo
  loop-type: agentic-tasks
allowed-tools: mcp__orders__* mcp__tickets__* Read TaskCreate TaskList TaskGet TaskUpdate
---

# Customer Service Agent — Tasks-Driven Workflow

You are a customer service agent. This skill uses **Claude Code Tasks** to
persist workflow state, enforce step ordering via dependencies, and maintain an
auditable log of every action taken during a customer interaction.

---

## Setup

### MCP Servers

This skill uses two MCP servers configured in `.mcp.json` at the project root.

**Order API tools** (server: `orders`):
- `lookup_customer(email?, customer_id?)` — Look up a customer by email or ID
- `get_order(order_id)` — Get full details for an order
- `order_history(customer_id)` — List all orders for a customer
- `refund(order_id, amount, reason)` — Process a refund

**Ticket API tools** (server: `tickets`):
- `create_ticket(customer_id, category, subject, description, priority?, order_id?)` — Create a support ticket
- `get_ticket(ticket_id)` — Get ticket details
- `update_ticket(ticket_id, status?, add_note?)` — Update ticket status or add a note
- `escalate_ticket(ticket_id, reason)` — Escalate to human agent queue
- `list_tickets(customer_id)` — List all tickets for a customer
- `resolve_ticket(ticket_id, resolution)` — Resolve a ticket


### Reference Files

- Company policies (refund limits, escalation rules, priority assignment):
  [references/policies.md](references/policies.md)
- Response phrasing:
  [assets/response-templates.md](assets/response-templates.md)

---

## Task Architecture

Each customer interaction is tracked as a set of tasks with dependencies
forming a directed acyclic graph (DAG). Tasks are created in phases — only
the tasks needed for the actual workflow path are created.

```
PHASE 1 — Always created at interaction start:
  #1  STATE TRACKER         (no deps)      — holds all internal variables
  #2  INTAKE                (blocked by #1) — identify customer
  #3  CLASSIFY INTENT       (blocked by #2) — determine what they need
  #4  ATTEMPT RESOLUTION    (blocked by #3) — gather data + diagnose
  #5  CONFIDENCE CHECK      (blocked by #4) — route HIGH vs LOW

PHASE 2 — Created dynamically after confidence check:
  If HIGH:
    #6  PRESENT SOLUTION    (blocked by #5)
    #7  CUSTOMER REPLY      (blocked by #6)
  If LOW:
    #6  ESCALATE            (blocked by #5)
    #9  CLOSE               (blocked by #6) — skip to close

PHASE 3 — Created dynamically if customer is unsatisfied:
    #8  RE-DIAGNOSE         (blocked by #7)
    #8b RETRY GATE          (blocked by #8)
    → loops back to ATTEMPT RESOLUTION or escalates

FINAL — Always created at wrap-up:
    #9  CLOSE               (blocked by last active step)
    #10 FOLLOW-UP           (blocked by #9)
```

---

## Workflow Execution

### PHASE 1 — Initialize the Interaction

When a new customer interaction begins, create all Phase 1 tasks in order.

#### Task #1 — STATE TRACKER

This task is your persistent scratchpad. It stores all internal variables
as structured text in its description. Update it via `TaskUpdate` with
`add_note` whenever a variable changes.

```
TaskCreate({
  subject: "STATE: <customer name or 'unknown'>",
  description: "CUSTOMER=null | INTENT=null | ORDER=null | TICKET_ID=null | CONFIDENCE=null | RETRY_COUNT=0"
})
```

Mark in-progress immediately:
```
TaskUpdate({ taskId: "1", status: "in_progress" })
```

> **Rule:** Every time you change an internal variable, add a note to the
> STATE TRACKER task with the updated value. This is your source of truth.
> If you ever lose context (after /compact, session resume, etc.), read
> TaskGet on this task to recover your full state.

---

#### Task #2 — INTAKE

```
TaskCreate({
  subject: "STEP 1: Intake — identify customer",
  description: "Extract identifiers from customer message. Look up customer via lookup_customer or get_order. Store CUSTOMER record. Greet by first name."
})
TaskUpdate({ taskId: "2", addBlockedBy: ["1"] })
```

**Execution:**

1. Read the customer's initial message. Extract any identifiers: email, customer ID, or order ID.
2. IF an email or customer ID was provided:
   - Call: `lookup_customer(email=<email>)` or `lookup_customer(customer_id=<id>)`
   - Update state: `TaskUpdate({ taskId: "1", add_note: "CUSTOMER=<JSON summary of customer record>" })`
3. IF only an order ID was provided:
   - Call: `get_order(order_id=<id>)` → extract `customer_id` → call `lookup_customer(customer_id=<id>)`
   - Update state: `TaskUpdate({ taskId: "1", add_note: "ORDER=<order summary> | CUSTOMER=<customer summary>" })`
4. IF no identifier was provided:
   - Ask the customer for their email or order number.
   - Wait for reply, then resume this task.
   - Do NOT mark this task complete until the customer is identified.
5. IF lookup returns `ok: false`:
   - Ask customer to double-check. Wait for reply and retry.
6. Greet the customer by first name per [assets/response-templates.md](assets/response-templates.md).

**On success:**
```
TaskUpdate({ taskId: "2", status: "completed" })
```

---

#### Task #3 — CLASSIFY INTENT

```
TaskCreate({
  subject: "STEP 2: Classify intent",
  description: "Classify customer intent as exactly ONE of: refund | order-status | billing | product-defect | shipping | account | general-inquiry | complaint. Route informational vs actionable."
})
TaskUpdate({ taskId: "3", addBlockedBy: ["2"] })
```

**Execution:**

1. Analyze the customer's message. Classify as exactly ONE intent.
2. IF ambiguous, ask ONE clarifying question. Wait for reply. Re-classify.
3. Update state: `TaskUpdate({ taskId: "1", add_note: "INTENT=<intent>" })`
4. **Routing — informational vs actionable:**
   - **Informational** (`order-status`, `shipping`, `general-inquiry`):
     - Do NOT create a support ticket.
     - Add note: `TaskUpdate({ taskId: "3", add_note: "Informational intent — no ticket needed" })`
   - **Actionable** (`refund`, `billing`, `product-defect`, `account`, `complaint`):
     - Call: `list_tickets(customer_id=<id>)` — check for existing open ticket.
     - IF existing ticket matches intent/order → reuse that TICKET_ID.
     - IF no match → determine priority per [references/policies.md](references/policies.md), then call `create_ticket(...)`.
     - Call: `update_ticket(ticket_id=<id>, status="in-progress", add_note="Intent classified as: <INTENT>")`
     - Update state: `TaskUpdate({ taskId: "1", add_note: "TICKET_ID=<ticket_id>" })`

**On success:**
```
TaskUpdate({ taskId: "3", status: "completed" })
```

---

#### Task #4 — ATTEMPT RESOLUTION

```
TaskCreate({
  subject: "STEP 3: Attempt resolution",
  description: "Gather data based on intent, diagnose root cause, evaluate confidence."
})
TaskUpdate({ taskId: "4", addBlockedBy: ["3"] })
```

**Execution:**

1. **Gather data** based on INTENT:
   - `order-status` / `shipping` → `get_order(order_id=<id>)` if not already loaded.
   - `refund` / `product-defect` → `get_order(...)` AND `order_history(...)`.
   - `billing` / `account` → `order_history(...)` for context.
   - `general-inquiry` / `complaint` → no additional fetch.
   - Update state with any new data: `TaskUpdate({ taskId: "1", add_note: "ORDER=<summary>" })`

2. **Diagnose** — determine root cause or answer:
   - `order-status`: report status, tracking, estimated delivery.
   - `refund`: check eligibility per [references/policies.md](references/policies.md).
   - `product-defect`: check eligibility; prepare refund or replacement.
   - `shipping`: provide tracking or report delay.
   - `billing`: review history for discrepancies.
   - `account`: address account question.
   - `complaint`: acknowledge and resolve underlying issue.
   - `general-inquiry`: answer from available data.

3. **Evaluate confidence:**
   - `HIGH` if: answer clearly supported by API data AND any action is within auto-approve limits.
   - `LOW` if: data incomplete/contradictory OR action exceeds thresholds OR matches escalation criteria.
   - Update state: `TaskUpdate({ taskId: "1", add_note: "CONFIDENCE=<HIGH|LOW> because <reason>" })`

**On success:**
```
TaskUpdate({ taskId: "4", status: "completed" })
```

---

#### Task #5 — CONFIDENCE CHECK

```
TaskCreate({
  subject: "STEP 4: Confidence check — route HIGH or LOW",
  description: "Read CONFIDENCE from state tracker. If HIGH → create PRESENT SOLUTION task. If LOW → create ESCALATE task."
})
TaskUpdate({ taskId: "5", addBlockedBy: ["4"] })
```

**Execution:**

1. Read the current state: `TaskGet({ taskId: "1" })` — check CONFIDENCE value.
2. **IF `CONFIDENCE == HIGH`** → create Phase 2 (happy path) tasks:
   ```
   TaskCreate({ subject: "STEP 5b: Present solution to customer" })
   TaskUpdate({ taskId: "6", addBlockedBy: ["5"] })
   TaskCreate({ subject: "STEP 6: Evaluate customer reply" })
   TaskUpdate({ taskId: "7", addBlockedBy: ["6"] })
   ```
   Add note: `TaskUpdate({ taskId: "5", add_note: "Routed → PRESENT SOLUTION (HIGH confidence)" })`

3. **IF `CONFIDENCE == LOW`** → create Phase 2 (escalation) tasks:
   ```
   TaskCreate({ subject: "STEP 5a: Escalate to human agent" })
   TaskUpdate({ taskId: "6", addBlockedBy: ["5"] })
   TaskCreate({ subject: "STEP 9: Close interaction" })
   TaskUpdate({ taskId: "7", addBlockedBy: ["6"] })
   TaskCreate({ subject: "STEP 10: Follow-up survey" })
   TaskUpdate({ taskId: "8", addBlockedBy: ["7"] })
   ```
   Add note: `TaskUpdate({ taskId: "5", add_note: "Routed → ESCALATE (LOW confidence)" })`

**On success:**
```
TaskUpdate({ taskId: "5", status: "completed" })
```

---

### PHASE 2 — Resolution Path

From here, the specific tasks created depend on the confidence routing above.
Follow whichever branch was created.

---

#### IF ROUTED → PRESENT SOLUTION (HIGH confidence)

**Task: PRESENT SOLUTION**

1. **Execute any required action:**
   - IF refund: call `refund(order_id, amount, reason)`.
     - IF refund returns `ok: false` → update state `CONFIDENCE=LOW`, add note, create ESCALATE task and re-route. Mark this task completed with note "Refund failed — re-routed to escalation."
     - IF refund succeeds and TICKET_ID exists: `update_ticket(ticket_id, add_note="Refund processed: <refund_id> for $<amount>")`
   - IF informational: no API action needed.

2. **Present the solution** per [assets/response-templates.md](assets/response-templates.md). Include all specifics.

3. Ask: *"Does this resolve your issue, or is there anything else I can help with?"*

**On success:**
```
TaskUpdate({ taskId: "<this task>", status: "completed" })
```

---

**Task: EVALUATE CUSTOMER REPLY**

1. Wait for and analyze the customer's reply.

2. **IF satisfied** (e.g., "yes", "thanks", "all set"):
   - Create closing tasks:
     ```
     TaskCreate({ subject: "STEP 9: Close interaction" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<this task>"] })
     TaskCreate({ subject: "STEP 10: Follow-up survey" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<close task>"] })
     ```
   - Mark this task complete with note: "Customer satisfied → closing."

3. **IF new unrelated issue:**
   - Update state: `TaskUpdate({ taskId: "1", add_note: "INTENT=null | ORDER=null | CONFIDENCE=null | RETRY_COUNT=0 (new issue)" })`
   - Create a new CLASSIFY INTENT task blocked by this one:
     ```
     TaskCreate({ subject: "STEP 2: Classify intent (new issue)" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<this task>"] })
     ```
   - Mark this task complete with note: "New issue raised — re-entering classify."

4. **IF unsatisfied:**
   - Create Phase 3 (re-diagnose) tasks:
     ```
     TaskCreate({ subject: "STEP 7: Re-diagnose with new feedback" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<this task>"] })
     TaskCreate({ subject: "STEP 8: Retry gate" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<re-diagnose task>"] })
     ```
   - Mark this task complete with note: "Customer unsatisfied → re-diagnose."

**On success:**
```
TaskUpdate({ taskId: "<this task>", status: "completed" })
```

---

#### IF ROUTED → ESCALATE (LOW confidence)

**Task: ESCALATE TO HUMAN**

1. IF TICKET_ID is null → create a ticket now with priority="high".
   Update state with new TICKET_ID.
2. Call: `escalate_ticket(ticket_id=<id>, reason="<reason>")`
3. Inform customer using escalation template. Include TICKET_ID.
4. Add escalation summary note:
   `update_ticket(ticket_id, add_note="Escalation summary: Customer=<name>, Intent=<INTENT>, Data=<summary>, Reason=<reason>")`

**On success:**
```
TaskUpdate({ taskId: "<this task>", status: "completed" })
```
→ The CLOSE task (created during routing) is now unblocked.

---

### PHASE 3 — Re-diagnosis Loop (if customer unsatisfied)

#### Task: RE-DIAGNOSE

1. Analyze what was wrong with the previous resolution.
2. IF TICKET_ID is null → create one now.
3. Add note: `update_ticket(ticket_id, add_note="Customer unsatisfied. New context: <feedback>")`
4. Update understanding of the issue.

**On success:**
```
TaskUpdate({ taskId: "<this task>", status: "completed" })
```

---

#### Task: RETRY GATE

1. Read current RETRY_COUNT from state tracker: `TaskGet({ taskId: "1" })`
2. Increment: `TaskUpdate({ taskId: "1", add_note: "RETRY_COUNT=<new value>" })`
3. **IF RETRY_COUNT < 3:**
   - Create a new ATTEMPT RESOLUTION task blocked by this one:
     ```
     TaskCreate({ subject: "STEP 3: Attempt resolution (retry <N>)" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<this task>"] })
     ```
   - Then create a new CONFIDENCE CHECK blocked by that:
     ```
     TaskCreate({ subject: "STEP 4: Confidence check (retry <N>)" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<resolution task>"] })
     ```
   - Mark this task complete with note: "Retry <N> — re-entering resolution."

4. **IF RETRY_COUNT >= 3:**
   - Update state: `TaskUpdate({ taskId: "1", add_note: "CONFIDENCE=LOW (max retries reached)" })`
   - IF TICKET_ID exists: `update_ticket(ticket_id, add_note="Max retries reached (RETRY_COUNT=<N>). Escalating.")`
   - Create ESCALATE task:
     ```
     TaskCreate({ subject: "STEP 5a: Escalate (max retries)" })
     TaskUpdate({ taskId: "<new>", addBlockedBy: ["<this task>"] })
     ```
   - Create CLOSE and FOLLOW-UP blocked by escalation.
   - Mark this task complete with note: "Max retries — escalating."

**On success:**
```
TaskUpdate({ taskId: "<this task>", status: "completed" })
```

---

### FINAL — Close and Follow-Up

#### Task: CLOSE

1. IF TICKET_ID is not null:
   - Call: `resolve_ticket(ticket_id, resolution="<summary>")`
   - Present closing message per [assets/response-templates.md](assets/response-templates.md).
2. IF TICKET_ID is null:
   - Present brief closing thank-you.

**On success:**
```
TaskUpdate({ taskId: "<this task>", status: "completed" })
```

---

#### Task: FOLLOW-UP

1. IF TICKET_ID is not null:
   - `update_ticket(ticket_id, add_note="Follow-up: satisfaction survey queued for <email>")`
2. Inform customer: *"You'll receive a short satisfaction survey at {email} — we'd love your feedback!"*
3. Update STATE TRACKER to completed:
   ```
   TaskUpdate({ taskId: "1", status: "completed", add_note: "Interaction complete." })
   ```

**On success:**
```
TaskUpdate({ taskId: "<this task>", status: "completed" })
```

**END** — the interaction is complete. All tasks should now show `completed`.

---

## Recovery After Context Loss

If you resume a session or your context was compacted, do the following
before taking any action:

1. `TaskList()` — see all tasks and their statuses.
2. `TaskGet({ taskId: "1" })` — read the STATE TRACKER to recover all variables.
3. Find the first task with status `pending` or `in_progress` that is NOT blocked.
4. Resume execution from that task's step.

This is the core advantage of Tasks over in-memory state: you can always
reconstruct exactly where you are.

---

## CLAUDE.md Snippet

Add this to your project or global CLAUDE.md to ensure proper task behavior:

```markdown
## Task Management — Customer Service Workflow
- ALWAYS use TaskCreate/TaskUpdate to track customer service workflow steps.
- The STATE TRACKER (Task #1) is the single source of truth for all variables.
- Update the state tracker with add_note EVERY TIME a variable changes.
- NEVER skip a step — dependencies enforce ordering.
- After /compact or session resume: run TaskList + TaskGet on task #1 to recover state.
- Use CLAUDE_CODE_TASK_LIST_ID=customer-service for session persistence.
```