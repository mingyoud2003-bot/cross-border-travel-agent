from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from state import TravelState
from transport_provider import STATIONS


ControlReason = Literal[
    "confirmation",
    "clarification",
    "validation",
    "unsupported_route",
]


@dataclass(frozen=True)
class ControlledTurn:
    """A deterministic reply that bypasses the model and all business tools."""

    message: str
    reason: ControlReason


FIELD_LABELS = {
    "origin": "出发城市",
    "destination": "目的城市",
    "travel_date": "明确日期（YYYY-MM-DD）",
    "miles_required": "所需里程",
    "cash_price": "现金票价",
    "taxes": "税费",
}


def controlled_turn_for(state: TravelState) -> ControlledTurn | None:
    """Return a stable control-plane response when no model judgment is needed."""

    pending = state.pending_confirmation
    if pending:
        label = FIELD_LABELS[pending.field]
        current = state.value(pending.field)
        return ControlledTurn(
            message=(
                f"请确认：是否将{label}从“{current}”改为“{pending.value}”？"
                "请回答“是”或“不是”。"
            ),
            reason="confirmation",
        )

    if state.control_message:
        return ControlledTurn(state.control_message, "clarification")

    if state.current_task not in {"train", "mileage", "decision"}:
        return None

    missing = state.missing_fields()
    if missing:
        fields = "、".join(FIELD_LABELS[name] for name in missing)
        return ControlledTurn(
            f"请提供以下缺失信息：{fields}。",
            reason="clarification",
        )

    errors = state.validation_errors()
    if errors:
        return ControlledTurn(
            f"参数需要修正：{'；'.join(errors)}。",
            reason="validation",
        )

    if state.current_task == "train":
        unsupported = [
            city
            for city in (state.origin, state.destination)
            if city and city not in STATIONS
        ]
        if unsupported:
            supported = "、".join(STATIONS)
            return ControlledTurn(
                f"当前铁路数据源仅支持{supported}之间的计划时刻查询，"
                f"暂不支持“{state.origin}→{state.destination}”。"
                "请改为支持城市；系统不会猜测或替换你的路线。",
                reason="unsupported_route",
            )

    return None
