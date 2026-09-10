from datetime import date, timedelta
from types import SimpleNamespace

from state import TravelState
from tools import (
    calculate_redemption_value,
    loyalty_tool_enabled,
    mileage_tool_enabled,
    train_tool_enabled,
    validate_mileage_arguments,
    validate_train_arguments,
)
import pytest


def future_date() -> str:
    return (date.today() + timedelta(days=30)).isoformat()


def wrapper(state: TravelState):
    return SimpleNamespace(context=state)


def test_train_tool_hidden_until_all_slots_are_confirmed():
    state = TravelState()
    state.begin_turn("从伦敦去巴黎坐火车")

    assert not train_tool_enabled(wrapper(state), None)

    state.begin_turn(future_date())

    assert train_tool_enabled(wrapper(state), None)


def test_train_guardrail_accepts_exact_confirmed_arguments():
    state = TravelState()
    travel_date = future_date()
    state.begin_turn(f"查{travel_date}伦敦到巴黎的火车")

    reason = validate_train_arguments(
        state,
        {"origin": "伦敦", "destination": "巴黎", "date": travel_date},
    )

    assert reason is None


def test_train_guardrail_rejects_model_substituted_city():
    state = TravelState()
    travel_date = future_date()
    state.begin_turn(f"查{travel_date}伦敦到巴黎的火车")

    reason = validate_train_arguments(
        state,
        {"origin": "伦敦", "destination": "布鲁塞尔", "date": travel_date},
    )

    assert "不一致" in reason


def test_train_guardrail_rejects_past_date():
    state = TravelState()
    state.begin_turn("查2020-01-01伦敦到巴黎的火车")

    reason = validate_train_arguments(
        state,
        {"origin": "伦敦", "destination": "巴黎", "date": "2020-01-01"},
    )

    assert "过去" in reason


def test_mileage_tool_hidden_when_cash_price_is_missing():
    state = TravelState()
    state.begin_turn("25000 Avios，加60英镑税费，值不值")

    assert not mileage_tool_enabled(wrapper(state), None)


def test_mileage_guardrail_rejects_inferred_zero_tax():
    state = TravelState()
    state.begin_turn("现金400英镑，需要20000 Avios，值不值")

    reason = validate_mileage_arguments(
        state,
        {"miles_required": 20000, "cash_price": 400, "taxes": 0},
    )

    assert "税费没有可验证" in reason


def test_mileage_guardrail_rejects_changed_value():
    state = TravelState()
    state.begin_turn("20000 Avios，现金400英镑，税费50英镑")

    reason = validate_mileage_arguments(
        state,
        {"miles_required": 25000, "cash_price": 400, "taxes": 50},
    )

    assert "不一致" in reason


def test_mileage_guardrail_rejects_tax_above_cash_price():
    state = TravelState()
    state.begin_turn("20000 Avios，现金50英镑，税费60英镑")

    reason = validate_mileage_arguments(
        state,
        {"miles_required": 20000, "cash_price": 50, "taxes": 60},
    )

    assert "高于" in reason


def test_loyalty_tool_only_visible_for_loyalty_task():
    state = TravelState()
    state.begin_turn("你好")
    assert not loyalty_tool_enabled(wrapper(state), None)

    state.begin_turn("BA Silver 有什么休息室权益？")
    assert loyalty_tool_enabled(wrapper(state), None)


def test_switching_task_disables_previous_tool():
    state = TravelState()
    state.begin_turn(f"查{future_date()}伦敦到巴黎的火车")
    assert train_tool_enabled(wrapper(state), None)

    state.begin_turn("20000 Avios，现金400英镑，税费50英镑，算兑换价值")

    assert not train_tool_enabled(wrapper(state), None)
    assert mileage_tool_enabled(wrapper(state), None)


def test_redemption_calculation_is_deterministic():
    assert calculate_redemption_value(20000, 400, 50) == 1.75


@pytest.mark.parametrize(
    "miles,cash,taxes",
    [(0, 400, 50), (20000, -1, 0), (20000, 50, 60)],
)
def test_redemption_calculation_rejects_invalid_values(miles, cash, taxes):
    with pytest.raises(ValueError):
        calculate_redemption_value(miles, cash, taxes)
