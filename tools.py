import json
from datetime import date, datetime
from typing import Any

from agents import RunContextWrapper, function_tool
from agents.tool_guardrails import (
    ToolGuardrailFunctionOutput,
    ToolInputGuardrailData,
    tool_input_guardrail,
)

from loyalty_service import get_loyalty_evidence
from decision_service import compose_decision
from state import TravelState
from transport_provider import TransportProviderError, search_train_journeys


INVALID_TEXT_VALUES = {
    "",
    "unknown",
    "未提供",
    "未提供出发城市",
    "未提供目的城市",
    "未知",
    "none",
    "null",
}


def train_tool_enabled(
    ctx: RunContextWrapper[TravelState],
    agent: Any,
) -> bool:
    """Expose train search only for a complete, confirmed train task."""

    del agent
    state = ctx.context
    if state.current_task == "train":
        return state.is_actionable("train") and "train" not in state.tool_results
    return (
        state.current_task == "decision"
        and state.is_actionable("decision")
        and "train" not in state.tool_results
    )


def mileage_tool_enabled(
    ctx: RunContextWrapper[TravelState],
    agent: Any,
) -> bool:
    """Expose calculation only after all values have user provenance."""

    del agent
    state = ctx.context
    if state.current_task == "mileage":
        return state.is_actionable("mileage") and "mileage" not in state.tool_results
    return (
        state.current_task == "decision"
        and state.is_actionable("decision")
        and "mileage" not in state.tool_results
    )


def loyalty_tool_enabled(
    ctx: RunContextWrapper[TravelState],
    agent: Any,
) -> bool:
    del agent
    state = ctx.context
    return (state.current_task == "loyalty" and "loyalty" not in state.tool_results) or (
        state.current_task == "decision"
        and state.is_actionable("decision")
        and state.loyalty_requested
        and "loyalty" not in state.tool_results
    )


def decision_tool_enabled(
    ctx: RunContextWrapper[TravelState],
    agent: Any,
) -> bool:
    del agent
    return ctx.context.decision_dependencies_ready()


def validate_train_arguments(
    state: TravelState,
    arguments: dict[str, Any],
    *,
    today: date | None = None,
) -> str | None:
    """Return a user-safe rejection reason, or None when arguments are valid."""

    if state.current_task not in {"train", "decision"}:
        return "当前不是铁路查询任务，不得调用铁路工具。"

    origin = str(arguments.get("origin", "")).strip()
    destination = str(arguments.get("destination", "")).strip()
    travel_date_text = str(arguments.get("date", "")).strip()

    if origin.casefold() in INVALID_TEXT_VALUES:
        return "缺少有效出发城市，请先向用户确认。"
    if destination.casefold() in INVALID_TEXT_VALUES:
        return "缺少有效目的城市，请先向用户确认。"

    try:
        travel_date = datetime.strptime(travel_date_text, "%Y-%m-%d").date()
    except ValueError:
        return "日期格式无效，请向用户确认明确日期。"

    if travel_date < (today or date.today()):
        return "日期不能是过去日期，请向用户确认。"

    expected = {
        "origin": state.origin,
        "destination": state.destination,
        "travel_date": state.travel_date,
    }
    actual = {
        "origin": origin,
        "destination": destination,
        "travel_date": travel_date_text,
    }
    labels = {
        "origin": "出发城市",
        "destination": "目的城市",
        "travel_date": "出发日期",
    }
    for field_name, expected_value in expected.items():
        provenance = state.provenance(field_name)
        if (
            provenance is None
            or provenance.source != "user"
            or provenance.task != state.current_task
        ):
            return f"{labels[field_name]}没有可验证的用户来源，请先确认。"
        if actual[field_name] != expected_value:
            return f"{labels[field_name]}与用户已确认值不一致，不得猜测或改写。"

    return None


def validate_mileage_arguments(
    state: TravelState,
    arguments: dict[str, Any],
) -> str | None:
    if state.current_task not in {"mileage", "decision"}:
        return "当前不是里程计算任务，不得调用计算工具。"

    expected = {
        "miles_required": state.miles_required,
        "cash_price": state.cash_price,
        "taxes": state.taxes,
    }
    labels = {
        "miles_required": "所需里程",
        "cash_price": "现金票价",
        "taxes": "税费",
    }

    for field_name, expected_value in expected.items():
        provenance = state.provenance(field_name)
        if (
            provenance is None
            or provenance.source != "user"
            or provenance.task != state.current_task
        ):
            return f"{labels[field_name]}没有可验证的用户来源，请先确认。"
        try:
            actual_value = float(arguments[field_name])
            confirmed_value = float(expected_value)
        except (KeyError, TypeError, ValueError):
            return f"{labels[field_name]}缺失或格式无效，请先确认。"
        if actual_value != confirmed_value:
            return f"{labels[field_name]}与用户已确认值不一致，不得猜测或改写。"

    if float(arguments["miles_required"]) <= 0:
        return "所需里程必须大于零。"
    if float(arguments["cash_price"]) < 0:
        return "现金票价不能为负数。"
    if float(arguments["taxes"]) < 0:
        return "税费不能为负数。"
    if float(arguments["taxes"]) > float(arguments["cash_price"]):
        return "税费不能高于现金票价，请向用户确认。"

    return None


@tool_input_guardrail
def validate_train_input(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
    try:
        arguments = json.loads(data.context.tool_arguments)
    except (TypeError, json.JSONDecodeError):
        return ToolGuardrailFunctionOutput.reject_content("铁路工具参数不是有效 JSON。")

    reason = validate_train_arguments(data.context.context, arguments)
    if reason:
        return ToolGuardrailFunctionOutput.reject_content(reason)
    return ToolGuardrailFunctionOutput.allow()


@tool_input_guardrail
def validate_mileage_input(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
    try:
        arguments = json.loads(data.context.tool_arguments)
    except (TypeError, json.JSONDecodeError):
        return ToolGuardrailFunctionOutput.reject_content("计算工具参数不是有效 JSON。")

    reason = validate_mileage_arguments(data.context.context, arguments)
    if reason:
        return ToolGuardrailFunctionOutput.reject_content(reason)
    return ToolGuardrailFunctionOutput.allow()


@function_tool(
    is_enabled=train_tool_enabled,
    tool_input_guardrails=[validate_train_input],
)
def search_train(
    ctx: RunContextWrapper[TravelState],
    origin: str,
    destination: str,
    date: str,
) -> str:
    """Search scheduled train journeys between supported cities.

    Args:
        origin: User-confirmed departure city.
        destination: User-confirmed destination city.
        date: User-confirmed travel date in YYYY-MM-DD format.
    """

    try:
        result = search_train_journeys(
            origin=origin,
            destination=destination,
            travel_date=date,
            limit=2,
        )
    except TransportProviderError as exc:
        result = {
            "status": "provider_error",
            "origin": origin,
            "destination": destination,
            "date": date,
            "message": str(exc),
            "journeys": [],
        }
        ctx.context.record_tool_result("train", result)
        return _json(result)

    if result.get("status") == "no_results" or not result.get("journeys"):
        result = {
            "status": "no_results",
            "origin": origin,
            "destination": destination,
            "date": date,
            "message": "当前数据源未找到可用铁路行程。",
            "journeys": [],
        }
        ctx.context.record_tool_result("train", result)
        return _json(result)

    # This is an assertion, not a write: confirmed provenance remains unchanged.
    ctx.context.update_train_trip(origin, destination, date)
    ctx.context.record_tool_result("train", result)
    return _json(result)


@function_tool(is_enabled=loyalty_tool_enabled)
def retrieve_loyalty_benefits(
    ctx: RunContextWrapper[TravelState],
    query: str,
) -> str:
    """Retrieve grounded loyalty-benefit evidence from the curated knowledge base.

    Args:
        query: The user's loyalty-benefit question. The original user message is
            authoritative and model-generated query expansion is discarded.
    """

    model_query = query.strip()
    user_query = (
        ctx.context.decision_loyalty_query()
        if ctx.context.current_task == "decision"
        else ctx.context.current_user_message.strip()
    )
    effective_query = user_query if user_query else model_query
    try:
        result = get_loyalty_evidence(query=effective_query, top_k=4)
    except Exception as exc:  # Provider failures are data, not invented answers.
        result = {
            "status": "provider_error",
            "query": effective_query,
            "message": f"常旅客知识检索暂不可用：{type(exc).__name__}",
            "evidence": [],
        }
    ctx.context.record_tool_result("loyalty", result)
    return _json(result)


@function_tool(
    is_enabled=mileage_tool_enabled,
    tool_input_guardrails=[validate_mileage_input],
)
def calculate_mileage_value(
    ctx: RunContextWrapper[TravelState],
    miles_required: int,
    cash_price: float,
    taxes: float,
) -> str:
    """Calculate redemption value in pence per mile from confirmed values.

    Args:
        miles_required: User-confirmed miles or Avios required.
        cash_price: User-confirmed cash price of the same ticket.
        taxes: User-confirmed taxes and fees, including an explicitly stated zero.
    """

    value = calculate_redemption_value(miles_required, cash_price, taxes)
    result = {
        "status": "success",
        "miles_required": miles_required,
        "cash_price": cash_price,
        "taxes": taxes,
        "pence_per_mile": value,
    }
    ctx.context.record_tool_result("mileage", result)
    return _json(result)


@function_tool(is_enabled=decision_tool_enabled)
def compose_travel_decision(ctx: RunContextWrapper[TravelState]) -> str:
    """Compose the final structured decision after all required tools were attempted."""

    result = compose_decision(ctx.context).model_dump(mode="json")
    ctx.context.record_tool_result("decision", result)
    return _json(result)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def calculate_redemption_value(
    miles_required: int,
    cash_price: float,
    taxes: float,
) -> float:
    """Pure calculation used by the tool and deterministic unit tests."""

    if miles_required <= 0:
        raise ValueError("miles_required must be greater than zero")
    if cash_price < 0 or taxes < 0 or taxes > cash_price:
        raise ValueError("cash_price and taxes are inconsistent")
    return round((cash_price - taxes) / miles_required * 100, 2)
