from datetime import date, timedelta

from agent import run_config_for
from state import TravelState, detect_task, extract_explicit_dates


def future_date(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_complete_train_turn_records_user_provenance():
    state = TravelState()
    travel_date = future_date()

    state.begin_turn(f"请查{travel_date}从伦敦去巴黎的火车")

    assert state.current_task == "train"
    assert state.origin == "伦敦"
    assert state.destination == "巴黎"
    assert state.travel_date == travel_date
    assert state.is_complete("train")
    assert state.provenance("origin").source == "user"
    assert state.provenance("origin").turn_id == 1


def test_train_slots_accumulate_across_turns():
    state = TravelState()
    travel_date = future_date()

    state.begin_turn("我想从伦敦出发坐火车")
    state.begin_turn("去巴黎")
    state.begin_turn(travel_date)

    assert state.is_complete("train")
    assert state.origin == "伦敦"
    assert state.destination == "巴黎"
    assert state.travel_date == travel_date
    assert state.provenance("origin").turn_id == 1
    assert state.provenance("destination").turn_id == 2
    assert state.provenance("travel_date").turn_id == 3


def test_user_can_update_destination_and_provenance():
    state = TravelState()
    travel_date = future_date()
    state.begin_turn(f"查{travel_date}伦敦到巴黎的火车")

    state.begin_turn("目的地改成布鲁塞尔")

    assert state.destination == "布鲁塞尔"
    assert state.provenance("destination").turn_id == 2
    assert any(
        event.event == "slot_updated"
        and event.field == "destination"
        and event.old_value == "巴黎"
        and event.new_value == "布鲁塞尔"
        for event in state.events
    )


def test_user_can_update_date():
    state = TravelState()
    first_date = future_date(20)
    second_date = future_date(25)
    state.begin_turn(f"查{first_date}伦敦到巴黎的火车")

    state.begin_turn(f"日期改成{second_date}")

    assert state.travel_date == second_date
    assert state.provenance("travel_date").turn_id == 2


def test_task_switch_clears_stale_target_task_values():
    state = TravelState()
    state.begin_turn(f"查{future_date()}伦敦到巴黎的火车")
    state.begin_turn("20000 Avios，现金400英镑，税费50英镑，算兑换价值")

    assert state.current_task == "mileage"
    assert state.is_complete("mileage")

    state.begin_turn("再帮我查火车")

    assert state.current_task == "train"
    assert state.origin is None
    assert state.destination is None
    assert state.travel_date is None
    assert state.missing_fields() == ["origin", "destination", "travel_date"]


def test_switch_to_general_does_not_authorize_old_tool():
    state = TravelState()
    state.begin_turn(f"查{future_date()}伦敦到巴黎的火车")
    state.begin_turn("你好")

    assert state.current_task == "general"
    assert not state.is_complete()


def test_mileage_values_accumulate_across_turns():
    state = TravelState()
    state.begin_turn("兑换需要25000 Avios")
    state.begin_turn("现金票价是500英镑")
    state.begin_turn("税费60英镑")

    assert state.current_task == "mileage"
    assert state.miles_required == 25000
    assert state.cash_price == 500
    assert state.taxes == 60
    assert state.is_complete("mileage")


def test_zero_tax_requires_explicit_user_phrase():
    state = TravelState()
    state.begin_turn("18000 Avios，现金300英镑，而且没有税费")

    assert state.taxes == 0
    assert state.provenance("taxes").source_text.endswith("没有税费")


def test_missing_tax_remains_missing():
    state = TravelState()
    state.begin_turn("现金400英镑，需要20000 Avios，值不值")

    assert state.taxes is None
    assert state.missing_fields("mileage") == ["taxes"]


def test_english_city_aliases_normalize_to_provider_values():
    state = TravelState()
    state.begin_turn(f"Find a train from London to Paris on {future_date()}")

    assert state.origin == "伦敦"
    assert state.destination == "巴黎"


def test_invalid_date_is_not_confirmed():
    state = TravelState()
    state.begin_turn("查2027年2月30日伦敦到巴黎的火车")

    assert state.travel_date is None


def test_relative_date_is_not_silently_resolved():
    state = TravelState()
    state.begin_turn("下周从伦敦去巴黎坐火车")

    assert state.travel_date is None


def test_detect_loyalty_beats_general_language():
    assert detect_task("BA Silver 有什么休息室权益？") == "loyalty"


def test_date_extractor_normalizes_both_supported_formats():
    assert extract_explicit_dates("2027-01-02 和 2027年1月3日") == {
        "2027-01-02",
        "2027-01-03",
    }


def test_snapshot_exposes_provenance_without_objects():
    state = TravelState()
    state.begin_turn("从伦敦出发坐火车")

    snapshot = state.snapshot()

    assert snapshot["slots"]["origin"]["source"] == "user"
    assert snapshot["slots"]["origin"]["turn_id"] == 1


def test_general_train_poem_does_not_start_train_task():
    assert detect_task("写一首关于火车旅行的小诗") == "general"


def test_mathematical_integral_does_not_start_mileage_task():
    assert detect_task("积分在数学里是什么意思？") == "general"


def test_cash_price_correction_is_captured():
    state = TravelState()
    state.begin_turn("20000 Avios，现金400英镑，税费50英镑")
    state.begin_turn("现金票价改成500英镑")

    assert state.cash_price == 500
    assert state.provenance("cash_price").turn_id == 2


def test_decision_prompt_exposes_next_dependency_after_correction():
    state = TravelState()
    travel_date = future_date()
    state.begin_turn(
        f"{travel_date}从伦敦去巴黎，现金票400英镑，"
        "奖励票20000 Avios加50英镑税费，应该怎么选？"
    )
    state.record_tool_result("train", {"status": "success"})
    state.record_tool_result("mileage", {"status": "success"})
    state.record_tool_result("decision", {"recommendation": {}})

    state.begin_turn("现金票价改成200英镑")

    assert "必须执行的下一步：立即调用 calculate_mileage_value" in state.prompt_context()
    assert "train=success" in state.prompt_context()


def test_decision_destination_correction_requires_fresh_train_search():
    state = TravelState()
    travel_date = future_date()
    state.begin_turn(
        f"{travel_date}从伦敦去巴黎，现金票400英镑，"
        "奖励票20000 Avios加50英镑税费，应该怎么选？"
    )
    state.record_tool_result("train", {"status": "success"})
    state.record_tool_result("mileage", {"status": "success"})
    state.record_tool_result("decision", {"recommendation": {}})

    state.begin_turn("目的地改成布鲁塞尔")

    assert "必须执行的下一步：立即调用 search_train" in state.prompt_context()
    assert "mileage=success" in state.prompt_context()


def test_actionable_decision_forces_tool_choice_until_composer():
    state = TravelState()
    state.begin_turn(
        f"{future_date()}从伦敦去巴黎，现金票400英镑，"
        "奖励票20000 Avios加50英镑税费，应该怎么选？"
    )

    config = run_config_for(state)

    assert config is not None
    assert config.model_settings.tool_choice == "required"


def test_incomplete_decision_does_not_force_tool_choice():
    state = TravelState()
    state.begin_turn("从伦敦去巴黎，现金票400英镑，奖励票20000 Avios，应该怎么选？")

    assert run_config_for(state) is None


def test_actionable_train_forces_required_tool_choice():
    state = TravelState()
    state.begin_turn(f"{future_date()}从伦敦到巴黎的火车")

    config = run_config_for(state)

    assert config is not None
    assert config.model_settings.tool_choice == "required"


def test_arrow_route_does_not_capture_leading_date_as_origin():
    state = TravelState()

    state.begin_turn("2026年10月12日柏林→科隆的火车。")

    assert state.value("origin") == "柏林"
    assert state.value("destination") == "科隆"
    assert state.value("travel_date") == "2026-10-12"


def test_train_command_is_not_captured_as_standalone_city():
    state = TravelState()
    state.begin_turn("20000 Avios，现金400英镑，税费50英镑，算兑换价值。")

    state.begin_turn("再帮我查火车。")

    assert state.current_task == "train"
    assert state.missing_fields() == ["origin", "destination", "travel_date"]
    assert state.value("origin") is None
    assert state.value("destination") is None
