# /// script
# requires-python = ">=3.10"
# dependencies = ["fastmcp"]
# ///
"""
Mock Order & Account MCP Server — simulates an order management system.

Run:
    uv run server.py
    # or with stdio transport:
    uv run server.py --transport stdio
"""

import copy
from datetime import datetime, timedelta, timezone

from fastmcp import FastMCP

mcp = FastMCP(
    "Orders",
    instructions="Simulated order management system for testing",
)

# ---------------------------------------------------------------------------
# Stateful data store (deep-copied from defaults so state persists across
# calls within a session but can be reset between test runs)
# ---------------------------------------------------------------------------

_DEFAULT_CUSTOMERS = {
    "C-1001": {
        "customer_id": "C-1001",
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "phone": "+1-555-0101",
        "tier": "gold",
        "account_created": "2023-06-15",
        "lifetime_spend": 2489.97,
        "open_tickets": 0,
    },
    "C-1002": {
        "customer_id": "C-1002",
        "name": "Bob Martinez",
        "email": "bob.m@example.com",
        "phone": "+1-555-0102",
        "tier": "silver",
        "account_created": "2024-01-22",
        "lifetime_spend": 349.50,
        "open_tickets": 0,
    },
    "C-1003": {
        "customer_id": "C-1003",
        "name": "Carol Wei",
        "email": "carol.wei@example.com",
        "phone": "+1-555-0103",
        "tier": "standard",
        "account_created": "2025-03-10",
        "lifetime_spend": 79.99,
        "open_tickets": 0,
    },
}

_DEFAULT_ORDERS = {
    "ORD-5001": {
        "order_id": "ORD-5001",
        "customer_id": "C-1001",
        "status": "delivered",
        "placed_at": "2025-12-01T10:30:00Z",
        "delivered_at": "2025-12-05T14:22:00Z",
        "total": 129.99,
        "items": [
            {"sku": "WH-1000XM5", "name": "Wireless Headphones", "qty": 1, "price": 129.99}
        ],
        "shipping_carrier": "FedEx",
        "tracking_number": "FX-789012345",
        "refund_eligible": True,
    },
    "ORD-5002": {
        "order_id": "ORD-5002",
        "customer_id": "C-1001",
        "status": "shipped",
        "placed_at": "2026-01-28T09:15:00Z",
        "delivered_at": None,
        "total": 259.98,
        "items": [
            {"sku": "KB-MX3S", "name": "Mechanical Keyboard", "qty": 1, "price": 179.99},
            {"sku": "MP-XL", "name": "Desk Mat XL", "qty": 1, "price": 79.99},
        ],
        "shipping_carrier": "UPS",
        "tracking_number": "1Z999AA10123456784",
        "refund_eligible": False,
    },
    "ORD-5003": {
        "order_id": "ORD-5003",
        "customer_id": "C-1002",
        "status": "processing",
        "placed_at": "2026-02-14T16:45:00Z",
        "delivered_at": None,
        "total": 349.50,
        "items": [
            {"sku": "MON-27K", "name": '27" 4K Monitor', "qty": 1, "price": 349.50}
        ],
        "shipping_carrier": None,
        "tracking_number": None,
        "refund_eligible": True,
    },
    "ORD-5004": {
        "order_id": "ORD-5004",
        "customer_id": "C-1003",
        "status": "delivered",
        "placed_at": "2026-01-10T08:00:00Z",
        "delivered_at": "2026-01-14T11:30:00Z",
        "total": 79.99,
        "items": [
            {"sku": "CHG-USB-C", "name": "USB-C Fast Charger", "qty": 1, "price": 39.99},
            {"sku": "CBL-USBC-2M", "name": "USB-C Cable 2m", "qty": 2, "price": 20.00},
        ],
        "shipping_carrier": "USPS",
        "tracking_number": "9400111899223100001",
        "refund_eligible": True,
    },
}


class Store:
    """Mutable session state that can be reset between test runs."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.customers: dict = copy.deepcopy(_DEFAULT_CUSTOMERS)
        self.orders: dict = copy.deepcopy(_DEFAULT_ORDERS)
        self.refunds: list[dict] = []
        self._email_index: dict = {
            c["email"]: cid for cid, c in self.customers.items()
        }

    def customer_by_email(self, email: str) -> dict | None:
        cid = self._email_index.get(email)
        return self.customers.get(cid) if cid else None


store = Store()

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def lookup_customer(
    email: str | None = None,
    customer_id: str | None = None,
) -> dict:
    """Look up a customer by email address or customer ID.

    Provide at least one of `email` or `customer_id`.
    """
    if email:
        customer = store.customer_by_email(email)
        if not customer:
            return {"ok": False, "error": f"No customer found with email '{email}'"}
        return {"ok": True, "customer": customer}
    if customer_id:
        customer = store.customers.get(customer_id)
        if not customer:
            return {"ok": False, "error": f"No customer found with id '{customer_id}'"}
        return {"ok": True, "customer": customer}
    return {"ok": False, "error": "Provide email or customer_id"}


@mcp.tool()
def get_order(order_id: str) -> dict:
    """Get full details for a single order by its order ID."""
    order = store.orders.get(order_id)
    if not order:
        return {"ok": False, "error": f"No order found with id '{order_id}'"}
    return {"ok": True, "order": order}


@mcp.tool()
def order_history(customer_id: str) -> dict:
    """Return all orders for a given customer, newest first."""
    if customer_id not in store.customers:
        return {"ok": False, "error": f"No customer found with id '{customer_id}'"}
    orders = sorted(
        [o for o in store.orders.values() if o["customer_id"] == customer_id],
        key=lambda o: o["placed_at"],
        reverse=True,
    )
    return {
        "ok": True,
        "customer_id": customer_id,
        "order_count": len(orders),
        "orders": orders,
    }


@mcp.tool()
def refund(order_id: str, amount: float, reason: str) -> dict:
    """Process a refund for an order.

    Args:
        order_id: The order to refund.
        amount: Dollar amount to refund (must not exceed order total).
        reason: Free-text reason for the refund.
    """
    order = store.orders.get(order_id)
    if not order:
        return {"ok": False, "error": f"No order found with id '{order_id}'"}
    if not order["refund_eligible"]:
        return {
            "ok": False,
            "error": f"Order {order_id} is not eligible for refund (status: {order['status']})",
        }
    if amount > order["total"]:
        return {
            "ok": False,
            "error": f"Refund amount ${amount:.2f} exceeds order total ${order['total']:.2f}",
        }

    refund_record = {
        "refund_id": f"REF-{abs(hash(order_id)) % 90000 + 10000}",
        "order_id": order_id,
        "amount": amount,
        "reason": reason,
        "status": "approved",
        "estimated_credit": (
            datetime.now(timezone.utc) + timedelta(days=5)
        ).strftime("%Y-%m-%d"),
    }
    store.refunds.append(refund_record)

    # Mark order as no longer refund-eligible after a full refund
    if amount >= order["total"]:
        order["refund_eligible"] = False
        order["status"] = "refunded"

    return {"ok": True, "refund": refund_record}


@mcp.tool()
def reset_state() -> dict:
    """Reset all data back to its original defaults. Useful between test runs."""
    store.reset()
    return {"ok": True, "message": "State reset to defaults"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()