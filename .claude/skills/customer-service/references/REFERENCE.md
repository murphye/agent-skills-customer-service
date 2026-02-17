# Customer Service Skill — Technical Reference

## Script Inventory

| Script | Purpose | Invocation |
|--------|---------|------------|
| `order_api.py` | Mock order & account management API | `uv run scripts/order_api.py` |
| `ticket_api.py` | Mock support ticket system API | `uv run scripts/ticket_api.py` |

All scripts use PEP 723 inline metadata and are run from the **skill root**: `cd <skill-root> && uv run scripts/<script>.py <command>`.

---

## order-api Commands

### `lookup-customer`
Look up a customer record by email or customer ID.

```bash
uv run scripts/order_api.py lookup-customer --email alice@example.com
uv run scripts/order_api.py lookup-customer --customer-id C-1001
```

Returns: `{ ok, customer: { customer_id, name, email, phone, tier, account_created, lifetime_spend, open_tickets } }`

### `get-order`
Retrieve details for a single order.

```bash
uv run scripts/order_api.py get-order --order-id ORD-5001
```

Returns: `{ ok, order: { order_id, customer_id, status, placed_at, delivered_at, total, items[], shipping_carrier, tracking_number, refund_eligible } }`

Order statuses: `processing`, `shipped`, `delivered`, `cancelled`

### `order-history`
Retrieve all orders for a customer, sorted newest first.

```bash
uv run scripts/order_api.py order-history --customer-id C-1001
```

Returns: `{ ok, customer_id, order_count, orders[] }`

### `refund`
Process a refund against a specific order. Succeeds only if refund-eligible and amount <= order total.

```bash
uv run scripts/order_api.py refund --order-id ORD-5001 --amount 129.99 --reason "Defective product"
```

Returns on success: `{ ok, refund: { refund_id, order_id, amount, reason, status, estimated_credit } }`

---

## ticket-api Commands

### `create`
Create a new support ticket.

```bash
uv run scripts/ticket_api.py create \
  --customer-id C-1001 \
  --category refund \
  --subject "Defective headphones" \
  --description "Left ear stopped working after 2 weeks" \
  --priority high \
  --order-id ORD-5001
```

Valid categories: `refund`, `order-status`, `billing`, `product-defect`, `shipping`, `account`, `general-inquiry`, `complaint`

Valid priorities: `low`, `medium`, `high`, `urgent`

### `get`
Retrieve a ticket by ID.

```bash
uv run scripts/ticket_api.py get --ticket-id TKT-8001
```

### `update`
Update a ticket's status and/or add an internal note.

```bash
uv run scripts/ticket_api.py update --ticket-id TKT-8001 --status in-progress --add-note "Investigating order"
```

Valid statuses: `open`, `in-progress`, `waiting-on-customer`, `escalated`, `resolved`, `closed`

### `escalate`
Escalate a ticket to the human agent queue. Automatically raises priority to `high` if currently lower.

```bash
uv run scripts/ticket_api.py escalate --ticket-id TKT-8001 --reason "Customer requests supervisor"
```

### `list`
List all tickets for a customer.

```bash
uv run scripts/ticket_api.py list --customer-id C-1002
```

### `resolve`
Mark a ticket as resolved with a resolution summary.

```bash
uv run scripts/ticket_api.py resolve --ticket-id TKT-8001 --resolution "Refund issued for $129.99"
```

---

## Mock Data Summary

### Customers
| ID | Name | Email | Tier |
|----|------|-------|------|
| C-1001 | Alice Johnson | alice@example.com | gold |
| C-1002 | Bob Martinez | bob.m@example.com | silver |
| C-1003 | Carol Wei | carol.wei@example.com | standard |

### Orders
| ID | Customer | Status | Total | Refund Eligible |
|----|----------|--------|-------|-----------------|
| ORD-5001 | C-1001 | delivered | $129.99 | Yes |
| ORD-5002 | C-1001 | shipped | $259.98 | No |
| ORD-5003 | C-1002 | processing | $349.50 | Yes |
| ORD-5004 | C-1003 | delivered | $79.99 | Yes |

### Pre-existing Tickets

None — the ticket store starts empty. Tickets are created during interactions as needed.
