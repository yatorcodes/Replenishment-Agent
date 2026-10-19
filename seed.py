"""Seed the SQLite database with fixed, reproducible fake data."""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

from app.config import DB_PATH
from app.db import get_connection, init_db, set_db_path

# Fixed seed so every seed run produces the same dataset.
RANDOM_SEED = 42


PRODUCTS = [
    # Normal products
    ("WS-NATO-20-BLK", "NATO Strap 20mm Black", 48, 20, 4.50),
    ("WS-NATO-20-OLV", "NATO Strap 20mm Olive", 35, 20, 4.50),
    ("WS-LTHR-18-BRN", "Leather Strap 18mm Brown", 22, 10, 12.00),
    ("WS-LTHR-20-BLK", "Leather Strap 20mm Black", 18, 10, 12.00),
    ("WS-RUBR-22-BLK", "Rubber Strap 22mm Black", 55, 25, 8.00),
    ("WS-MESH-20-SLV", "Mesh Bracelet 20mm Silver", 40, 15, 15.00),
    ("WS-CANV-20-KHK", "Canvas Strap 20mm Khaki", 30, 20, 5.50),
    # Low stock — will get an existing draft order in seed
    ("WS-NATO-18-NVY", "NATO Strap 18mm Navy", 6, 20, 4.50),
    # Negative stock (deliberate bad data)
    ("WS-LTHR-22-TAN", "Leather Strap 22mm Tan", -3, 10, 13.00),
    # Low stock, no supplier lead time (supplier row missing lead_time)
    ("WS-RUBR-20-BLU", "Rubber Strap 20mm Blue", 5, 25, 8.00),
    ("WS-MESH-18-GLD", "Mesh Bracelet 18mm Gold", 28, 15, 16.00),
    ("WS-CANV-18-RED", "Canvas Strap 18mm Red", 42, 20, 5.50),
]

# sku -> (supplier, lead_time_days). WS-RUBR-20-BLU has None lead time on purpose.
SUPPLIERS = {
    "WS-NATO-20-BLK": ("StrapCo EU", 7),
    "WS-NATO-20-OLV": ("StrapCo EU", 7),
    "WS-LTHR-18-BRN": ("Heritage Leather", 14),
    "WS-LTHR-20-BLK": ("Heritage Leather", 14),
    "WS-RUBR-22-BLK": ("FlexBand Asia", 21),
    "WS-MESH-20-SLV": ("MetalWorks", 10),
    "WS-CANV-20-KHK": ("StrapCo EU", 7),
    "WS-NATO-18-NVY": ("StrapCo EU", 7),
    "WS-LTHR-22-TAN": ("Heritage Leather", 14),
    "WS-RUBR-20-BLU": ("FlexBand Asia", None),  # missing lead time
    "WS-MESH-18-GLD": ("MetalWorks", 10),
    "WS-CANV-18-RED": ("StrapCo EU", 7),
}

# Base daily velocity used to generate 60 days of sales.
VELOCITY = {
    "WS-NATO-20-BLK": 1.2,
    "WS-NATO-20-OLV": 0.8,
    "WS-LTHR-18-BRN": 0.5,
    "WS-LTHR-20-BLK": 0.6,
    "WS-RUBR-22-BLK": 1.0,
    "WS-MESH-20-SLV": 0.4,
    "WS-CANV-20-KHK": 0.7,
    "WS-NATO-18-NVY": 0.9,
    "WS-LTHR-22-TAN": 0.3,
    "WS-RUBR-20-BLU": 0.8,
    "WS-MESH-18-GLD": 0.35,
    "WS-CANV-18-RED": 0.6,
}


def _generate_sales(rng: random.Random, today: date) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for sku, base in VELOCITY.items():
        for days_ago in range(60):
            day = today - timedelta(days=days_ago)
            # Poisson-ish: round of noisy base rate, clamped to non-negative.
            noise = rng.uniform(0.4, 1.6)
            units = max(0, int(round(base * noise + rng.choice([0, 0, 0, 1]))))
            if units > 0:
                rows.append((sku, day.isoformat(), units))
    return rows


def seed(db_path: str = DB_PATH) -> None:
    set_db_path(db_path)
    path = Path(db_path)
    if path.exists():
        path.unlink()

    init_db()
    rng = random.Random(RANDOM_SEED)
    today = date(2026, 10, 19)
    sales = _generate_sales(rng, today)

    with get_connection() as conn:
        conn.executemany(
            "INSERT INTO products (sku, name, stock, min_order_qty, unit_cost) "
            "VALUES (?, ?, ?, ?, ?)",
            PRODUCTS,
        )
        conn.executemany(
            "INSERT INTO suppliers (sku, supplier, lead_time_days) VALUES (?, ?, ?)",
            [(sku, name, lead) for sku, (name, lead) in SUPPLIERS.items()],
        )
        conn.executemany(
            "INSERT INTO sales (sku, date, units) VALUES (?, ?, ?)",
            sales,
        )
        # Existing draft for the deliberately low NATO 18mm Navy SKU.
        conn.execute(
            "INSERT INTO purchase_orders (sku, qty, status, reason, created_at) "
            "VALUES (?, ?, 'draft', ?, ?)",
            (
                "WS-NATO-18-NVY",
                40,
                "Stock was low on previous run; draft awaiting human review.",
                "2026-10-18T10:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO agent_notes (note, created_at) VALUES (?, ?)",
            (
                "Watch for WS-NATO-18-NVY and WS-RUBR-20-BLU — both look tight.",
                "2026-10-18T10:05:00",
            ),
        )

    print(f"Seeded {db_path}")
    print(f"  products: {len(PRODUCTS)}")
    print(f"  sales rows: {len(sales)}")
    print("  intentional issues:")
    print("    - WS-LTHR-22-TAN has negative stock (-3)")
    print("    - WS-RUBR-20-BLU has no supplier lead_time_days")
    print("    - WS-NATO-18-NVY is low and already has a draft PO")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the store database.")
    parser.add_argument("--db", default=DB_PATH, help="Path to SQLite file")
    args = parser.parse_args()
    seed(args.db)


if __name__ == "__main__":
    main()
