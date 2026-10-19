"""Stage 1: database schema and seed integrity."""

from app.db import fetch_all, fetch_one


def test_seed_creates_expected_product_count(db_path):
    products = fetch_all("SELECT * FROM products")
    assert len(products) == 12


def test_negative_stock_sku_present(db_path):
    row = fetch_one("SELECT stock FROM products WHERE sku = ?", ("WS-LTHR-22-TAN",))
    assert row is not None
    assert row["stock"] < 0


def test_missing_lead_time_sku(db_path):
    row = fetch_one(
        "SELECT lead_time_days FROM suppliers WHERE sku = ?",
        ("WS-RUBR-20-BLU",),
    )
    assert row is not None
    assert row["lead_time_days"] is None


def test_existing_draft_for_low_sku(db_path):
    row = fetch_one(
        "SELECT * FROM purchase_orders WHERE sku = ? AND status = 'draft'",
        ("WS-NATO-18-NVY",),
    )
    assert row is not None
    assert row["qty"] > 0


def test_sales_cover_last_60_days(db_path):
    rows = fetch_all("SELECT DISTINCT date FROM sales ORDER BY date")
    assert len(rows) >= 50  # most days have at least one sale across SKUs


def test_seed_is_deterministic(tmp_path):
    from app.db import set_db_path
    from seed import seed

    a = str(tmp_path / "a.db")
    b = str(tmp_path / "b.db")
    seed(a)
    set_db_path(a)
    sales_a = fetch_all("SELECT sku, date, units FROM sales ORDER BY id")
    seed(b)
    set_db_path(b)
    sales_b = fetch_all("SELECT sku, date, units FROM sales ORDER BY id")
    assert sales_a == sales_b
