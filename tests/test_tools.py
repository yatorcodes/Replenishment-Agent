"""Stage 2: tool validation and business rules."""

import json

import pytest

from app.db import execute, fetch_one
from app.tools import (
    ToolError,
    dispatch_tool,
    draft_purchase_order,
    get_inventory,
    get_sales_velocity,
    get_supplier_terms,
    read_notes,
    save_note,
)


def test_get_inventory_returns_products(db_path):
    items = get_inventory()
    assert len(items) == 12
    assert all("sku" in p and "stock" in p for p in items)


def test_negative_stock_visible_in_inventory(db_path):
    items = {p["sku"]: p for p in get_inventory()}
    assert items["WS-LTHR-22-TAN"]["stock"] < 0


def test_get_sales_velocity_ok(db_path):
    result = get_sales_velocity("WS-NATO-20-BLK", 30)
    assert result["sku"] == "WS-NATO-20-BLK"
    assert result["days"] == 30
    assert result["total_units"] >= 0
    assert result["avg_daily_units"] >= 0


def test_get_sales_velocity_rejects_bad_args(db_path):
    with pytest.raises(ToolError):
        get_sales_velocity("", 30)
    with pytest.raises(ToolError):
        get_sales_velocity("WS-NATO-20-BLK", 0)
    with pytest.raises(ToolError):
        get_sales_velocity("WS-NATO-20-BLK", -5)
    with pytest.raises(ToolError):
        get_sales_velocity("NO-SUCH-SKU", 30)


def test_get_supplier_terms_ok(db_path):
    terms = get_supplier_terms("WS-NATO-20-BLK")
    assert terms["supplier"] == "StrapCo EU"
    assert terms["lead_time_days"] == 7


def test_get_supplier_terms_missing_lead_time(db_path):
    with pytest.raises(ToolError, match="lead_time_days missing"):
        get_supplier_terms("WS-RUBR-20-BLU")


def test_notes_roundtrip(db_path):
    before = read_notes()
    saved = save_note("Check rubber blue again next run.")
    assert saved["saved"] is True
    after = read_notes()
    assert len(after) == len(before) + 1
    assert after[0]["note"] == "Check rubber blue again next run."


def test_save_note_rejects_empty(db_path):
    with pytest.raises(ToolError):
        save_note("   ")


def test_draft_purchase_order_success(db_path):
    result = draft_purchase_order(
        "WS-RUBR-22-BLK",
        25,
        "Stock trending down vs velocity.",
    )
    assert result["status"] == "draft"
    assert result["qty"] == 25
    row = fetch_one("SELECT * FROM purchase_orders WHERE id = ?", (result["id"],))
    assert row["status"] == "draft"


def test_draft_rejects_negative_qty(db_path):
    with pytest.raises(ToolError, match="negative"):
        draft_purchase_order("WS-MESH-20-SLV", -1, "bad")


def test_draft_rejects_below_min_order_qty(db_path):
    with pytest.raises(ToolError, match="min_order_qty"):
        draft_purchase_order("WS-MESH-20-SLV", 5, "too small")


def test_draft_rejects_duplicate(db_path):
    # Seed already has a draft for WS-NATO-18-NVY
    with pytest.raises(ToolError, match="already exists"):
        draft_purchase_order("WS-NATO-18-NVY", 40, "duplicate attempt")


def test_draft_rejects_unknown_sku(db_path):
    with pytest.raises(ToolError, match="unknown sku"):
        draft_purchase_order("FAKE-SKU", 20, "nope")


def test_dispatch_unknown_tool(db_path):
    payload = json.loads(dispatch_tool("explode_warehouse", {}))
    assert "error" in payload
    assert "unknown tool" in payload["error"]


def test_dispatch_bad_arguments(db_path):
    payload = json.loads(
        dispatch_tool("get_sales_velocity", {"sku": "WS-NATO-20-BLK"})
    )
    assert "error" in payload
    assert "bad arguments" in payload["error"]


def test_dispatch_tool_error_as_json(db_path):
    payload = json.loads(
        dispatch_tool(
            "draft_purchase_order",
            {"sku": "WS-NATO-18-NVY", "qty": 40, "reason": "dup"},
        )
    )
    assert "error" in payload
    assert "already exists" in payload["error"]


def test_agent_cannot_set_approved_via_draft(db_path):
    result = draft_purchase_order(
        "WS-CANV-18-RED",
        20,
        "Normal draft.",
    )
    assert result["status"] == "draft"
    # Even if someone tries to write approved directly, tools never do it.
    row = fetch_one("SELECT status FROM purchase_orders WHERE id = ?", (result["id"],))
    assert row["status"] == "draft"
