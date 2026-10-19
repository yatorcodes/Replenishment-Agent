"""Stage 3: agent loop with a mock Gemini client (no real API calls)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import AgentStopped, run_agent
from app.config import MAX_TOOL_CALLS
from app.db import fetch_all


class FakeTimeoutError(Exception):
    """Stand-in for a Gemini API timeout."""


def _part_text(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, function_call=None)


def _part_fc(name: str, args: dict[str, Any] | None) -> SimpleNamespace:
    return SimpleNamespace(
        text=None,
        function_call=SimpleNamespace(name=name, args=args),
    )


def _response(parts: list[Any]) -> SimpleNamespace:
    content = SimpleNamespace(role="model", parts=parts)
    candidate = SimpleNamespace(content=content)
    text = "".join(getattr(p, "text", None) or "" for p in parts)

    class _Resp(SimpleNamespace):
        @property
        def text(self):
            return text

    return _Resp(candidates=[candidate])


class ScriptedGemini:
    """
    Fake google-genai client.

    `scripts` is a list of steps. Each step is either:
      - ("text", "final message")
      - ("calls", [("tool_name", {args}), ...])
      - ("error", ExceptionInstance)
    """

    def __init__(self, scripts: list[tuple[str, Any]]):
        self.scripts = list(scripts)
        self.models = self
        self.calls = 0

    def generate_content(self, **kwargs):
        if not self.scripts:
            return _response([_part_text("Done.")])
        kind, payload = self.scripts.pop(0)
        self.calls += 1
        if kind == "error":
            raise payload
        if kind == "text":
            return _response([_part_text(payload)])
        if kind == "calls":
            parts = [_part_fc(name, args) for name, args in payload]
            return _response(parts)
        raise AssertionError(f"unknown script kind: {kind}")


def _factory(scripts: list[tuple[str, Any]]):
    client = ScriptedGemini(scripts)

    def factory():
        return client

    factory.client = client  # type: ignore[attr-defined]
    return factory


def test_agent_happy_path_drafts_and_notes(db_path):
    factory = _factory(
        [
            ("calls", [("read_notes", {})]),
            ("calls", [("get_inventory", {})]),
            (
                "calls",
                [
                    (
                        "draft_purchase_order",
                        {
                            "sku": "WS-RUBR-20-BLU",
                            "qty": 25,
                            "reason": "Low stock; need human review of supplier terms.",
                        },
                    )
                ],
            ),
            ("calls", [("save_note", {"text": "Drafted WS-RUBR-20-BLU."})]),
            ("text", "Drafted one order; awaiting human review."),
        ]
    )
    result = run_agent(client_factory=factory)
    assert result["ok"] is True
    assert result["tool_calls"] == 4

    audits = fetch_all(
        "SELECT step, tool FROM audit_log WHERE run_id = ? ORDER BY id",
        (result["run_id"],),
    )
    steps = [a["step"] for a in audits]
    assert "run_start" in steps
    assert "tool_call" in steps
    assert "tool_result" in steps
    assert "run_complete" in steps


def test_bad_tool_name_is_logged_not_crash(db_path):
    factory = _factory(
        [
            ("calls", [("not_a_real_tool", {"foo": 1})]),
            ("text", "Skipped unknown tool."),
        ]
    )
    result = run_agent(client_factory=factory)
    assert result["ok"] is True

    tool_results = fetch_all(
        "SELECT output FROM audit_log WHERE run_id = ? AND step = 'tool_result'",
        (result["run_id"],),
    )
    assert any("unknown tool" in (row["output"] or "") for row in tool_results)


def test_bad_arguments_are_logged_not_crash(db_path):
    factory = _factory(
        [
            # Missing required 'days'
            ("calls", [("get_sales_velocity", {"sku": "WS-NATO-20-BLK"})]),
            ("text", "Could not get velocity; stopping."),
        ]
    )
    result = run_agent(client_factory=factory)
    assert result["ok"] is True

    tool_results = fetch_all(
        "SELECT output FROM audit_log WHERE run_id = ? AND step = 'tool_result'",
        (result["run_id"],),
    )
    assert any("bad arguments" in (row["output"] or "") for row in tool_results)


def test_gemini_timeout_retries_then_stops(db_path, monkeypatch):
    monkeypatch.setattr("app.agent.time.sleep", lambda *_: None)
    factory = _factory(
        [
            ("error", FakeTimeoutError("deadline exceeded")),
            ("error", FakeTimeoutError("deadline exceeded")),
            ("error", FakeTimeoutError("deadline exceeded")),
        ]
    )
    result = run_agent(client_factory=factory)
    assert result["ok"] is False
    assert "failed after" in result["error"].lower() or "Gemini" in result["error"]

    errors = fetch_all(
        "SELECT step FROM audit_log WHERE run_id = ? AND step = 'model_error'",
        (result["run_id"],),
    )
    assert len(errors) == 3
    stopped = fetch_all(
        "SELECT output FROM audit_log WHERE run_id = ? AND step = 'run_stopped'",
        (result["run_id"],),
    )
    assert stopped
    assert "gemini_api_error_after_retries" in stopped[0]["output"]


def test_tool_call_cap_stops_run(db_path):
    # Cap at 2: first call ok, second call triggers stop before/at dispatch boundary.
    scripts: list[tuple[str, Any]] = [
        ("calls", [("get_inventory", {})]),
        ("calls", [("read_notes", {})]),
        ("calls", [("read_notes", {})]),  # would be 3rd if cap allowed
        ("text", "should not reach"),
    ]
    factory = _factory(scripts)
    result = run_agent(client_factory=factory, max_tool_calls=2)
    assert result["ok"] is False
    assert result["tool_calls"] == 2
    assert "cap" in result["error"].lower()

    stopped = fetch_all(
        "SELECT output FROM audit_log WHERE run_id = ? AND step = 'run_stopped'",
        (result["run_id"],),
    )
    assert stopped
    assert "max_tool_calls_reached" in stopped[0]["output"]


def test_max_tool_calls_constant():
    assert MAX_TOOL_CALLS == 25


def test_duplicate_draft_surfaced_via_tool_result(db_path):
    factory = _factory(
        [
            (
                "calls",
                [
                    (
                        "draft_purchase_order",
                        {
                            "sku": "WS-NATO-18-NVY",
                            "qty": 40,
                            "reason": "Trying again",
                        },
                    )
                ],
            ),
            ("text", "Duplicate rejected."),
        ]
    )
    result = run_agent(client_factory=factory)
    assert result["ok"] is True
    outputs = fetch_all(
        "SELECT output FROM audit_log WHERE run_id = ? AND step = 'tool_result'",
        (result["run_id"],),
    )
    parsed = json.loads(outputs[0]["output"])
    assert "already exists" in parsed["error"]
