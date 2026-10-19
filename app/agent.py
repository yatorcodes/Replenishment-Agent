"""Plain Python Gemini tool loop. No agent frameworks."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Callable

from dotenv import load_dotenv

from app.config import MAX_TOOL_CALLS, MODEL_NAME
from app.db import execute
from app.tools import TOOL_DECLARATIONS, dispatch_tool

load_dotenv()

SYSTEM_INSTRUCTION = """You are a replenishment agent for a small online store that sells watch straps.

Your job each run:
1. Read prior notes (memory).
2. Check inventory for products that look low or have bad data (e.g. negative stock).
3. For candidates, check sales velocity and supplier terms.
4. Draft purchase orders when needed, with a clear reason. Never approve orders — a human reviews drafts.
5. Save a short note summarizing what you found for the next run.

Rules:
- Only use the provided tools.
- If a tool returns an error, work around it or skip that SKU; do not invent supplier data.
- Prefer a few high-quality drafts over many speculative ones.
- When finished, reply with a brief plain-text summary (no more tool calls).
"""

# Injectable client factory for tests.
ClientFactory = Callable[[], Any]


class AgentStopped(Exception):
    """Raised when the run stops cleanly after retries or the tool-call cap."""


def _log(
    run_id: str,
    step: str,
    tool: str | None = None,
    input_data: Any = None,
    output_data: Any = None,
) -> None:
    execute(
        "INSERT INTO audit_log (run_id, step, tool, input, output) VALUES (?, ?, ?, ?, ?)",
        (
            run_id,
            step,
            tool,
            None if input_data is None else json.dumps(input_data, default=str),
            None if output_data is None else json.dumps(output_data, default=str),
        ),
    )


def _parse_args(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    return {}


def _extract_function_calls(response: Any) -> list[Any]:
    calls: list[Any] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                calls.append(fc)
    return calls


def _response_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
        if text:
            return str(text)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _build_client() -> Any:
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise AgentStopped("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)
    # Attach types module for callers that need Tool / GenerateContentConfig.
    client._types = types  # type: ignore[attr-defined]
    return client


def _generate_with_retry(
    client: Any,
    contents: list[Any],
    run_id: str,
    *,
    max_attempts: int = 3,
) -> Any:
    """Call Gemini; retry twice on API/timeout errors, then stop cleanly."""
    from google.genai import types

    tools = [types.Tool(function_declarations=TOOL_DECLARATIONS)]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=tools,
        temperature=0.2,
    )

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        _log(
            run_id,
            "model_call",
            tool=MODEL_NAME,
            input_data={"attempt": attempt, "message_count": len(contents)},
        )
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config=config,
            )
            _log(
                run_id,
                "model_response",
                tool=MODEL_NAME,
                output_data={
                    "text": _response_text(response),
                    "function_calls": [
                        {
                            "name": getattr(fc, "name", None),
                            "args": _parse_args(getattr(fc, "args", None)),
                        }
                        for fc in _extract_function_calls(response)
                    ],
                },
            )
            return response
        except Exception as exc:  # noqa: BLE001 — treat any SDK failure as retryable
            last_error = exc
            _log(
                run_id,
                "model_error",
                tool=MODEL_NAME,
                output_data={"attempt": attempt, "error": str(exc)},
            )
            if attempt < max_attempts:
                time.sleep(0.5 * attempt)

    _log(
        run_id,
        "run_stopped",
        output_data={
            "reason": "gemini_api_error_after_retries",
            "error": str(last_error),
        },
    )
    raise AgentStopped(f"Gemini API failed after {max_attempts} attempts: {last_error}")


def run_agent(
    *,
    client_factory: ClientFactory | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
) -> dict[str, Any]:
    """
    Run one replenishment pass.
    Returns a summary dict with run_id, tool_calls, and final text.
    """
    from google.genai import types

    run_id = str(uuid.uuid4())
    _log(run_id, "run_start", input_data={"model": MODEL_NAME, "max_tool_calls": max_tool_calls})

    factory = client_factory or _build_client
    try:
        client = factory()
    except AgentStopped as exc:
        _log(run_id, "run_stopped", output_data={"reason": str(exc)})
        return {"run_id": run_id, "ok": False, "error": str(exc), "tool_calls": 0}

    contents: list[Any] = [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    text=(
                        "Run a replenishment check. Identify low or problematic stock, "
                        "draft orders where appropriate, and save a memory note."
                    )
                )
            ],
        )
    ]

    tool_calls = 0
    final_text = ""

    try:
        while True:
            response = _generate_with_retry(client, contents, run_id)
            function_calls = _extract_function_calls(response)

            if not function_calls:
                final_text = _response_text(response)
                _log(run_id, "run_complete", output_data={"summary": final_text})
                break

            # Append model turn, then tool responses.
            model_content = response.candidates[0].content
            contents.append(model_content)

            tool_response_parts: list[Any] = []
            for fc in function_calls:
                if tool_calls >= max_tool_calls:
                    _log(
                        run_id,
                        "run_stopped",
                        output_data={
                            "reason": "max_tool_calls_reached",
                            "max_tool_calls": max_tool_calls,
                        },
                    )
                    raise AgentStopped(
                        f"Tool-call cap reached ({max_tool_calls}); stopping run."
                    )

                name = getattr(fc, "name", None) or ""
                args = _parse_args(getattr(fc, "args", None))
                tool_calls += 1

                _log(run_id, "tool_call", tool=name, input_data=args)
                result_json = dispatch_tool(name, args)
                _log(
                    run_id,
                    "tool_result",
                    tool=name,
                    output_data=json.loads(result_json),
                )

                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=name or "unknown",
                        response=json.loads(result_json),
                    )
                )

            contents.append(
                types.Content(role="user", parts=tool_response_parts)
            )

    except AgentStopped as exc:
        return {
            "run_id": run_id,
            "ok": False,
            "error": str(exc),
            "tool_calls": tool_calls,
            "summary": final_text,
        }

    return {
        "run_id": run_id,
        "ok": True,
        "tool_calls": tool_calls,
        "summary": final_text,
    }


def main() -> None:
    result = run_agent()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
