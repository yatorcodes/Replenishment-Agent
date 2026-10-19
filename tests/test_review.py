"""Stage 4: human review checkpoint."""

import pytest

from app.db import fetch_one
from app.review import list_drafts, main, set_status
from app.tools import draft_purchase_order


def test_list_drafts_includes_seed_draft(db_path):
    drafts = list_drafts()
    skus = {d["sku"] for d in drafts}
    assert "WS-NATO-18-NVY" in skus
    assert all(d["status"] == "draft" for d in drafts)


def test_approve_draft(db_path):
    drafts = list_drafts()
    order_id = drafts[0]["id"]
    updated = set_status(order_id, "approved")
    assert updated["status"] == "approved"
    row = fetch_one("SELECT status FROM purchase_orders WHERE id = ?", (order_id,))
    assert row["status"] == "approved"


def test_reject_draft(db_path):
    result = draft_purchase_order(
        "WS-MESH-18-GLD",
        15,
        "Test reject path.",
    )
    updated = set_status(result["id"], "rejected")
    assert updated["status"] == "rejected"


def test_cannot_approve_twice(db_path):
    drafts = list_drafts()
    order_id = drafts[0]["id"]
    set_status(order_id, "approved")
    with pytest.raises(ValueError, match="only drafts"):
        set_status(order_id, "approved")


def test_cli_approve(db_path, capsys):
    drafts = list_drafts()
    order_id = drafts[0]["id"]
    main(["--approve", str(order_id)])
    out = capsys.readouterr().out
    assert "Approved" in out
    row = fetch_one("SELECT status FROM purchase_orders WHERE id = ?", (order_id,))
    assert row["status"] == "approved"


def test_cli_list(db_path, capsys):
    main(["--list"])
    out = capsys.readouterr().out
    assert "WS-NATO-18-NVY" in out


def test_agent_tools_never_approve(db_path):
    """Sanity: draft tool cannot create approved orders."""
    result = draft_purchase_order(
        "WS-CANV-20-KHK",
        20,
        "Still a draft.",
    )
    assert result["status"] == "draft"
