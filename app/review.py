"""Human checkpoint: list draft orders and approve or reject them."""

from __future__ import annotations

import argparse
import sys

from app.db import execute, fetch_all, fetch_one


def list_drafts() -> list[dict]:
    return fetch_all(
        """
        SELECT po.id, po.sku, po.qty, po.status, po.reason, po.created_at,
               p.name, p.stock, p.min_order_qty
        FROM purchase_orders po
        JOIN products p ON p.sku = po.sku
        WHERE po.status = 'draft'
        ORDER BY po.id
        """
    )


def set_status(order_id: int, status: str) -> dict:
    if status not in ("approved", "rejected"):
        raise ValueError("status must be 'approved' or 'rejected'")

    row = fetch_one(
        "SELECT id, status FROM purchase_orders WHERE id = ?",
        (order_id,),
    )
    if row is None:
        raise ValueError(f"no purchase order with id={order_id}")
    if row["status"] != "draft":
        raise ValueError(
            f"order {order_id} is '{row['status']}', only drafts can be reviewed"
        )

    execute(
        "UPDATE purchase_orders SET status = ? WHERE id = ? AND status = 'draft'",
        (status, order_id),
    )
    updated = fetch_one("SELECT * FROM purchase_orders WHERE id = ?", (order_id,))
    assert updated is not None
    return dict(updated)


def _print_drafts(drafts: list[dict]) -> None:
    if not drafts:
        print("No draft purchase orders.")
        return
    print(f"\n{len(drafts)} draft order(s) awaiting review:\n")
    for d in drafts:
        print(f"  [{d['id']}] {d['sku']} — {d['name']}")
        print(f"      qty: {d['qty']} (min_order_qty={d['min_order_qty']}, stock={d['stock']})")
        print(f"      created: {d['created_at']}")
        print(f"      reason: {d['reason']}")
        print()


def interactive_review() -> None:
    drafts = list_drafts()
    _print_drafts(drafts)
    if not drafts:
        return

    for d in drafts:
        while True:
            choice = input(
                f"Order {d['id']} ({d['sku']} x{d['qty']}) "
                f"[a]pprove / [r]eject / [s]kip: "
            ).strip().lower()
            if choice in ("a", "approve"):
                updated = set_status(d["id"], "approved")
                print(f"  -> approved (status={updated['status']})")
                break
            if choice in ("r", "reject"):
                updated = set_status(d["id"], "rejected")
                print(f"  -> rejected (status={updated['status']})")
                break
            if choice in ("s", "skip", ""):
                print("  -> skipped")
                break
            print("  Please enter a, r, or s.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Review draft purchase orders (human checkpoint)."
    )
    parser.add_argument(
        "--approve",
        type=int,
        metavar="ID",
        help="Approve a draft order by id (non-interactive)",
    )
    parser.add_argument(
        "--reject",
        type=int,
        metavar="ID",
        help="Reject a draft order by id (non-interactive)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List drafts and exit",
    )
    args = parser.parse_args(argv)

    if args.approve and args.reject:
        print("Choose only one of --approve or --reject.", file=sys.stderr)
        raise SystemExit(2)

    if args.list:
        _print_drafts(list_drafts())
        return

    if args.approve is not None:
        updated = set_status(args.approve, "approved")
        print(f"Approved order {updated['id']} ({updated['sku']} x{updated['qty']})")
        return

    if args.reject is not None:
        updated = set_status(args.reject, "rejected")
        print(f"Rejected order {updated['id']} ({updated['sku']} x{updated['qty']})")
        return

    if not sys.stdin.isatty():
        _print_drafts(list_drafts())
        print(
            "(non-interactive stdin; pass --approve/--reject/--list)",
            file=sys.stderr,
        )
        return

    interactive_review()


if __name__ == "__main__":
    main()
