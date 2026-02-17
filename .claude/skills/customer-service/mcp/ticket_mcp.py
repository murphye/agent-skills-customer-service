# /// script
# requires-python = ">=3.10"
# dependencies = ["fastmcp"]
# ///
"""
Mock Ticketing / Support MCP Server — simulates a customer support ticket system.

Run:
    uv run ticket_mcp.py
"""

import copy
import random
from datetime import datetime, timezone

from fastmcp import FastMCP

mcp = FastMCP(
    "Mock Ticket API",
    instructions="Simulated customer support ticket system for testing",
)

# ---------------------------------------------------------------------------
# Stateful data store (starts empty — tickets are created during interactions)
# ---------------------------------------------------------------------------

_DEFAULT_TICKETS = {}

VALID_CATEGORIES = [
    "refund", "order-status", "billing", "product-defect",
    "shipping", "account", "general-inquiry", "complaint",
]
VALID_STATUSES = [
    "open", "in-progress", "waiting-on-customer",
    "escalated", "resolved", "closed",
]
VALID_PRIORITIES = ["low", "medium", "high", "urgent"]


class Store:
    """Mutable session state that can be reset between test runs."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.tickets: dict = copy.deepcopy(_DEFAULT_TICKETS)


store = Store()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_ticket_id() -> str:
    return f"TKT-{random.randint(8001, 9999)}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_ticket(
    customer_id: str,
    category: str,
    subject: str,
    description: str,
    priority: str = "medium",
    order_id: str | None = None,
) -> dict:
    """Create a new support ticket.

    Args:
        customer_id: The customer ID (e.g. "C-1001").
        category: One of: refund, order-status, billing, product-defect,
                  shipping, account, general-inquiry, complaint.
        subject: Brief summary of the issue.
        description: Detailed description of the issue.
        priority: One of: low, medium, high, urgent. Defaults to medium.
        order_id: Optional related order ID.
    """
    if category not in VALID_CATEGORIES:
        return {"ok": False, "error": f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}"}
    if priority not in VALID_PRIORITIES:
        return {"ok": False, "error": f"Invalid priority '{priority}'. Must be one of: {VALID_PRIORITIES}"}

    tid = _new_ticket_id()
    ticket = {
        "ticket_id": tid,
        "customer_id": customer_id,
        "category": category,
        "subject": subject,
        "description": description,
        "priority": priority,
        "status": "open",
        "order_id": order_id,
        "assigned_to": "auto-agent",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "notes": [],
        "resolution": None,
    }
    store.tickets[tid] = ticket
    return {"ok": True, "message": f"Ticket {tid} created successfully", "ticket": ticket}


@mcp.tool()
def get_ticket(ticket_id: str) -> dict:
    """Get full details for a single ticket by its ticket ID."""
    ticket = store.tickets.get(ticket_id)
    if not ticket:
        return {"ok": False, "error": f"No ticket found with id '{ticket_id}'"}
    return {"ok": True, "ticket": ticket}


@mcp.tool()
def update_ticket(
    ticket_id: str,
    status: str | None = None,
    add_note: str | None = None,
) -> dict:
    """Update an existing ticket's status and/or add a note.

    Args:
        ticket_id: The ticket to update.
        status: New status. One of: open, in-progress, waiting-on-customer,
                escalated, resolved, closed.
        add_note: Free-text note to append to the ticket.
    """
    ticket = store.tickets.get(ticket_id)
    if not ticket:
        return {"ok": False, "error": f"No ticket found with id '{ticket_id}'"}

    changes = []
    if status:
        if status not in VALID_STATUSES:
            return {"ok": False, "error": f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}"}
        old_status = ticket["status"]
        ticket["status"] = status
        changes.append(f"status: {old_status} -> {status}")
    if add_note:
        ticket["notes"].append({
            "text": add_note,
            "author": "auto-agent",
            "timestamp": _now_iso(),
        })
        changes.append("note added")

    ticket["updated_at"] = _now_iso()
    return {"ok": True, "ticket_id": ticket_id, "changes": changes, "ticket": ticket}


@mcp.tool()
def escalate_ticket(ticket_id: str, reason: str) -> dict:
    """Escalate a ticket to the human agent queue.

    Args:
        ticket_id: The ticket to escalate.
        reason: Why this ticket needs human attention.
    """
    ticket = store.tickets.get(ticket_id)
    if not ticket:
        return {"ok": False, "error": f"No ticket found with id '{ticket_id}'"}

    ticket["status"] = "escalated"
    ticket["assigned_to"] = "human-agent-queue"
    ticket["priority"] = "high" if ticket["priority"] in ("low", "medium") else ticket["priority"]
    ticket["updated_at"] = _now_iso()
    ticket["notes"].append({
        "text": f"ESCALATED: {reason}",
        "author": "auto-agent",
        "timestamp": _now_iso(),
    })
    return {"ok": True, "message": f"Ticket {ticket_id} escalated to human agent queue", "ticket": ticket}


@mcp.tool()
def list_tickets(customer_id: str) -> dict:
    """List all tickets for a given customer, newest first.

    Args:
        customer_id: The customer whose tickets to list.
    """
    tickets = sorted(
        [t for t in store.tickets.values() if t["customer_id"] == customer_id],
        key=lambda t: t["created_at"],
        reverse=True,
    )
    return {"ok": True, "customer_id": customer_id, "count": len(tickets), "tickets": tickets}


@mcp.tool()
def resolve_ticket(ticket_id: str, resolution: str) -> dict:
    """Resolve a ticket with a resolution summary.

    Args:
        ticket_id: The ticket to resolve.
        resolution: Summary of how the issue was resolved.
    """
    ticket = store.tickets.get(ticket_id)
    if not ticket:
        return {"ok": False, "error": f"No ticket found with id '{ticket_id}'"}

    ticket["status"] = "resolved"
    ticket["resolution"] = resolution
    ticket["updated_at"] = _now_iso()
    ticket["notes"].append({
        "text": f"RESOLVED: {resolution}",
        "author": "auto-agent",
        "timestamp": _now_iso(),
    })
    return {"ok": True, "message": f"Ticket {ticket_id} resolved", "ticket": ticket}


@mcp.tool()
def reset_state() -> dict:
    """Reset all ticket data back to its original defaults. Useful between test runs."""
    store.reset()
    return {"ok": True, "message": "Ticket state reset to defaults"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
