"""Agent tools with strict input validation. Model output is never trusted as state."""

from __future__ import annotations

import json
from typing import Any, Callable

from app.db import execute, fetch_all, fetch_one


class ToolError(Exception):
    """Raised when a tool is called with invalid input or hits a business rule."""


def get_inventory() -> list[dict[str, Any]]:
    """Return all products with current stock levels."""
    return fetch_all(
        "SELECT sku, name, stock, min_order_qty, unit_cost FROM products ORDER BY sku"
    )


def get_sales_velocity(sku: str, days: int) -> dict[str, Any]:
    """Average daily units sold for a SKU over the last N days."""
    if not isinstance(sku, str) or not sku.strip():
        raise ToolError("sku must be a non-empty string")
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        raise ToolError("days must be a positive integer")
    if days > 365:
        raise ToolError("days must be <= 365")

    product = fetch_one("SELECT sku FROM products WHERE sku = ?", (sku,))
    if product is None:
        raise ToolError(f"unknown sku: {sku}")

    row = fetch_one(
        """
        SELECT COALESCE(SUM(units), 0) AS total_units
        FROM sales
        WHERE sku = ?
          AND date >= date('now', ?)
        """,
        (sku, f"-{days} days"),
    )
    total = int(row["total_units"]) if row else 0
    avg = total / days
    return {
        "sku": sku,
        "days": days,
        "total_units": total,
        "avg_daily_units": round(avg, 3),
    }


def get_supplier_terms(sku: str) -> dict[str, Any]:
    """Supplier name and lead time for a SKU."""
    if not isinstance(sku, str) or not sku.strip():
        raise ToolError("sku must be a non-empty string")

    product = fetch_one("SELECT sku FROM products WHERE sku = ?", (sku,))
    if product is None:
        raise ToolError(f"unknown sku: {sku}")

    row = fetch_one(
        "SELECT sku, supplier, lead_time_days FROM suppliers WHERE sku = ?",
        (sku,),
    )
    if row is None:
        raise ToolError(f"no supplier record for sku: {sku}")
    if row["lead_time_days"] is None:
        raise ToolError(f"supplier lead_time_days missing for sku: {sku}")

    return {
        "sku": row["sku"],
        "supplier": row["supplier"],
        "lead_time_days": int(row["lead_time_days"]),
    }


def read_notes() -> list[dict[str, Any]]:
    """Return agent notes (memory between runs), newest first."""
    return fetch_all(
        "SELECT id, note, created_at FROM agent_notes ORDER BY id DESC"
    )


def save_note(text: str) -> dict[str, Any]:
    """Persist a note for future runs."""
    if not isinstance(text, str) or not text.strip():
        raise ToolError("text must be a non-empty string")
    note_id = execute(
        "INSERT INTO agent_notes (note) VALUES (?)",
        (text.strip(),),
    )
    return {"id": note_id, "saved": True}


def draft_purchase_order(sku: str, qty: int, reason: str) -> dict[str, Any]:
    """Create a draft purchase order. Never approves. Rejects invalid/duplicate drafts."""
    if not isinstance(sku, str) or not sku.strip():
        raise ToolError("sku must be a non-empty string")
    if not isinstance(qty, int) or isinstance(qty, bool):
        raise ToolError("qty must be an integer")
    if qty < 0:
        raise ToolError("qty must not be negative")
    if qty == 0:
        raise ToolError("qty must be greater than zero")
    if not isinstance(reason, str) or not reason.strip():
        raise ToolError("reason must be a non-empty string")

    product = fetch_one(
        "SELECT sku, min_order_qty, stock FROM products WHERE sku = ?",
        (sku,),
    )
    if product is None:
        raise ToolError(f"unknown sku: {sku}")

    min_qty = int(product["min_order_qty"])
    if qty < min_qty:
        raise ToolError(
            f"qty {qty} is below min_order_qty {min_qty} for sku {sku}"
        )

    existing = fetch_one(
        "SELECT id FROM purchase_orders WHERE sku = ? AND status = 'draft'",
        (sku,),
    )
    if existing is not None:
        raise ToolError(
            f"draft purchase order already exists for sku {sku} "
            f"(id={existing['id']})"
        )

    order_id = execute(
        "INSERT INTO purchase_orders (sku, qty, status, reason) "
        "VALUES (?, ?, 'draft', ?)",
        (sku, qty, reason.strip()),
    )
    return {
        "id": order_id,
        "sku": sku,
        "qty": qty,
        "status": "draft",
        "reason": reason.strip(),
    }


TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "get_inventory": get_inventory,
    "get_sales_velocity": get_sales_velocity,
    "get_supplier_terms": get_supplier_terms,
    "read_notes": read_notes,
    "save_note": save_note,
    "draft_purchase_order": draft_purchase_order,
}


def dispatch_tool(name: str, arguments: dict[str, Any] | None) -> str:
    """
    Run a named tool with validated arguments.
    Always returns a JSON string (success or error payload) for the model.
    """
    if name not in TOOL_REGISTRY:
        return json.dumps({"error": f"unknown tool: {name}"})

    args = arguments if isinstance(arguments, dict) else {}
    fn = TOOL_REGISTRY[name]

    try:
        result = fn(**args)
        return json.dumps({"ok": True, "result": result}, default=str)
    except TypeError as exc:
        return json.dumps({"error": f"bad arguments for {name}: {exc}"})
    except ToolError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 — surface unexpected failures to audit log
        return json.dumps({"error": f"tool failed: {exc}"})


# Declarations for Gemini function calling (google-genai SDK format).
TOOL_DECLARATIONS = [
    {
        "name": "get_inventory",
        "description": "List all products with current stock, min order qty, and unit cost.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_sales_velocity",
        "description": "Get total and average daily sales for a SKU over the last N days.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Product SKU"},
                "days": {
                    "type": "integer",
                    "description": "Lookback window in days (1-365)",
                },
            },
            "required": ["sku", "days"],
        },
    },
    {
        "name": "get_supplier_terms",
        "description": "Get supplier name and lead time in days for a SKU.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Product SKU"},
            },
            "required": ["sku"],
        },
    },
    {
        "name": "read_notes",
        "description": "Read notes saved by the agent in previous runs (memory).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "save_note",
        "description": "Save a short note for future agent runs.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Note text to store"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "draft_purchase_order",
        "description": (
            "Create a draft purchase order for human review. "
            "Does not approve or place the order. "
            "Rejects duplicates, negative qty, and qty below min_order_qty."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Product SKU"},
                "qty": {"type": "integer", "description": "Order quantity"},
                "reason": {
                    "type": "string",
                    "description": "Why this order is needed",
                },
            },
            "required": ["sku", "qty", "reason"],
        },
    },
]
