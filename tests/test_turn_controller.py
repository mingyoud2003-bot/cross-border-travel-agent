from datetime import date, timedelta

from state import TravelState, extract_explicit_route
from turn_controller import controlled_turn_for


def future_date(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_arbitrary_explicit_route_preserves_user_text_and_abstains_once():
    state = TravelState()

    state.begin_turn(f"请查{future_date()}从伦武汉到驻马店的火车")
    controlled = controlled_turn_for(state)

    assert state.origin == "伦武汉"
    assert state.destination == "驻马店"
    assert controlled is not None
    assert controlled.reason == "unsupported_route"
    assert "伦武汉→驻马店" in controlled.message
    assert "伦敦、巴黎、布鲁塞尔" in controlled.message


def test_arrow_route_correction_updates_both_slots_without_cross_assignment():
    state = TravelState()
    state.begin_turn(f"{future_date()}从伦敦到巴黎的火车")

    state.begin_turn("更改为柏林→科隆")

    assert state.origin == "柏林"
    assert state.destination == "科隆"
    assert state.provenance("origin").turn_id == 2
    assert state.provenance("destination").turn_id == 2


def test_standalone_typo_correction_uses_pending_confirmation():
    state = TravelState()
    state.begin_turn(f"请查{future_date()}从伦武汉到驻马店的火车")

    state.begin_turn("武汉")
    pending = state.pending_confirmation
    controlled = controlled_turn_for(state)

    assert state.current_task == "train"
    assert pending is not None
    assert pending.field == "origin"
    assert pending.value == "武汉"
    assert controlled is not None
    assert controlled.reason == "confirmation"

    state.begin_turn("是的")

    assert state.current_task == "train"
    assert state.origin == "武汉"
    assert state.destination == "驻马店"
    assert state.provenance("origin").turn_id == 2
    assert state.pending_confirmation is None
    assert controlled_turn_for(state).reason == "unsupported_route"


def test_bare_affirmation_without_pending_candidate_never_switches_task():
    state = TravelState()
    state.begin_turn(f"{future_date()}从伦敦到巴黎的火车")

    state.begin_turn("是")
    controlled = controlled_turn_for(state)

    assert state.current_task == "train"
    assert state.origin == "伦敦"
    assert state.destination == "巴黎"
    assert controlled is not None
    assert controlled.reason == "clarification"
    assert "没有待确认" in controlled.message


def test_labeled_unknown_destination_is_not_silently_ignored():
    state = TravelState()
    state.begin_turn(f"{future_date()}从柏林到摩纳哥的火车")

    state.begin_turn("目标城市改为科隆")

    assert state.origin == "柏林"
    assert state.destination == "科隆"
    assert controlled_turn_for(state).reason == "unsupported_route"


def test_explicit_route_extractor_does_not_need_city_allowlist():
    assert extract_explicit_route("从武汉到驻马店") == ("武汉", "驻马店")
    assert extract_explicit_route("更改为柏林→科隆") == ("柏林", "科隆")


def test_reported_thirteen_turn_sequence_never_corrupts_destination():
    travel_date = future_date()
    messages = [
        f"请查{travel_date}从伦武汉到驻马店的火车",
        "武汉",
        "是的",
        "是",
        "是",
        f"请查{travel_date}从武汉到驻马店的火车",
        f"请查{travel_date}从柏林到摩纳哥的火车",
        "是",
        f"请查{travel_date}从柏林到科隆的火车",
        "更改为柏林→科隆",
        "目标城市改为科隆",
        travel_date,
        "从柏林到科隆",
    ]
    state = TravelState()

    for message in messages:
        state.begin_turn(message)
        assert state.current_task == "train"

    assert state.origin == "柏林"
    assert state.destination == "科隆"
    assert state.travel_date == travel_date
    assert state.origin != state.destination
    controlled = controlled_turn_for(state)
    assert controlled is not None
    assert controlled.reason == "unsupported_route"
