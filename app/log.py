"""Print a readable audit timeline for a given agent run_id."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.db import fetch_all


def load_timeline(run_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT id, run_id, step, tool, input, output, created_at
        FROM audit_log
        WHERE run_id = ?
        ORDER BY id
        """,
        (run_id,),
    )


def _pretty(raw: str | None) -> str:
    if raw is None:
        return ""
    try:
        return json.dumps(json.loads(raw), indent=2)
    except (json.JSONDecodeError, TypeError):
        return raw


def format_timeline(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No audit entries for that run_id."

    lines: list[str] = [
        f"Audit timeline for run {rows[0]['run_id']} ({len(rows)} events)",
        "=" * 60,
    ]
    for row in rows:
        header = f"[{row['created_at']}] #{row['id']}  {row['step']}"
        if row["tool"]:
            header += f"  tool={row['tool']}"
        lines.append(header)
        if row["input"]:
            lines.append("  input:")
            for line in _pretty(row["input"]).splitlines():
                lines.append(f"    {line}")
        if row["output"]:
            lines.append("  output:")
            for line in _pretty(row["output"]).splitlines():
                lines.append(f"    {line}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show audit log for an agent run.")
    parser.add_argument("run_id", help="Run UUID from the agent")
    args = parser.parse_args(argv)

    rows = load_timeline(args.run_id)
    text = format_timeline(rows)
    print(text)
    if not rows:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
