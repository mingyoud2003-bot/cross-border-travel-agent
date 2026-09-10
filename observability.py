from __future__ import annotations

import json
from typing import Any

from agents.items import ToolCallItem, ToolCallOutputItem


def parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _tool_name(item: ToolCallItem) -> str | None:
    name = getattr(item, "tool_name", None)
    if name:
        return name
    raw = getattr(item, "raw_item", None)
    if isinstance(raw, dict):
        return raw.get("name")
    return getattr(raw, "name", None)


def _tool_arguments(item: ToolCallItem) -> Any:
    raw = getattr(item, "raw_item", None)
    arguments = raw.get("arguments") if isinstance(raw, dict) else getattr(
        raw, "arguments", None
    )
    return parse_json(arguments)


def tool_timeline(new_items: list[Any]) -> list[dict[str, Any]]:
    """Create a compact, local trace without exposing prompts or credentials."""

    pending: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    for item in new_items:
        if isinstance(item, ToolCallItem):
            event = {
                "tool": _tool_name(item),
                "arguments": _tool_arguments(item),
                "status": "called",
                "output": None,
            }
            pending.append(event)
            timeline.append(event)
        elif isinstance(item, ToolCallOutputItem):
            output = parse_json(item.output)
            target = next(
                (event for event in pending if event["status"] == "called"),
                None,
            )
            if target is None:
                continue
            target["output"] = output
            target["status"] = (
                str(output.get("status", "completed"))
                if isinstance(output, dict)
                else "completed"
            )
    return timeline


def usage_snapshot(result: Any) -> dict[str, int]:
    usage = result.context_wrapper.usage
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }
