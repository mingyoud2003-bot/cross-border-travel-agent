from datetime import date, timedelta
from types import SimpleNamespace

from decision_service import compose_decision
from agent import travel_agent
from state import TravelState, detect_task
from tools import (
    decision_tool_enabled,
    loyalty_tool_enabled,
    mileage_tool_enabled,
    train_tool_enabled,
    validate_mileage_arguments,
    validate_train_arguments,
)


def future_date() -> str:
    return (date.today() + timedelta(days=45)).isoformat()


def decision_message() -> str:
    return (
        f"{future_date()}从伦敦去巴黎，现金票400英镑，奖励票20000 Avios加"
        "50英镑税费，我是BA Silver，应该怎么选？"
    )


def wrapper(state: TravelState):
    return SimpleNamespace(context=state)


def complete_state(*, loyalty: bool = True) -> TravelState:
    state = TravelState()
    message = decision_message() if loyalty else decision_message().replace("，我是BA Silver", "")
    state.begin_turn(message)
    state.record_tool_result(
        "train",
        {
            "status": "success",
            "source": "Transitous",
            "journeys": [
                {
                    "departure_local": f"{future_date()}T08:00+01:00",
                    "arrival_local": f"{future_date()}T11:20+02:00",
                    "duration_minutes": 140,
                    "realtime": False,
                }
            ],
        },
    )
    state.record_tool_result(
        "mileage",
        {
            "status": "success",
            "miles_required": 20000,
            "cash_price": 400,
            "taxes": 50,
            "pence_per_mile": 1.75,
        },
    )
    if loyalty:
        state.record_tool_result(
            "loyalty",
            {
                "status": "success",
                "evidence": [
                    {
                        "id": "ba_silver_tier",
                        "title": "British Airways Silver tier mapping",
                        "content": "British Airways Club Silver corresponds to oneworld Sapphire status.",
                        "source": "British Airways",
                        "source_url": "https://example.test/ba",
                    }
                ],
            },
        )
    return state


def test_multi_intent_request_is_classified_as_decision():
    assert detect_task(decision_message()) == "decision"


def test_agent_disables_parallel_calls_for_stateful_dependency_tools():
    assert travel_agent.model_settings.parallel_tool_calls is False


def test_decision_captures_all_action_fields_with_user_provenance():
    state = TravelState()
    state.begin_turn(decision_message())

    assert state.current_task == "decision"
    assert state.is_actionable("decision")
    assert state.loyalty_requested
    assert all(
        state.provenance(name).task == "decision"
        for name in state.DECISION_FIELDS
    )


def test_decision_tools_are_exposed_but_composer_waits_for_dependencies():
    state = TravelState()
    state.begin_turn(decision_message())
    ctx = wrapper(state)

    assert train_tool_enabled(ctx, None)
    assert mileage_tool_enabled(ctx, None)
    assert loyalty_tool_enabled(ctx, None)
    assert not decision_tool_enabled(ctx, None)


def test_decision_guardrails_accept_exact_user_values():
    state = TravelState()
    state.begin_turn(decision_message())

    assert validate_train_arguments(
        state,
        {"origin": "伦敦", "destination": "巴黎", "date": future_date()},
    ) is None
    assert validate_mileage_arguments(
        state,
        {"miles_required": 20000, "cash_price": 400, "taxes": 50},
    ) is None


def test_decision_correction_invalidates_stale_calculation_and_composition():
    state = complete_state()
    state.record_tool_result("decision", {"status": "complete"})

    state.begin_turn("现金票价改成500英镑")

    assert state.current_task == "decision"
    assert state.cash_price == 500
    assert "train" in state.tool_results
    assert "mileage" not in state.tool_results
    assert "decision" not in state.tool_results


def test_decision_retrieval_query_is_composed_only_from_user_turns():
    state = TravelState()
    state.begin_turn(decision_message().replace("，我是BA Silver", ""))
    state.begin_turn("补充：我是BA Silver，请考虑会员权益")

    query = state.decision_loyalty_query()
    assert "BA Silver" in query
    assert query.count("\n") == 1
    assert state.loyalty_requested


def test_composer_becomes_enabled_after_required_attempts():
    state = complete_state()
    assert state.decision_dependencies_ready()
    assert decision_tool_enabled(wrapper(state), None)


def test_composed_output_has_stable_schema_and_evidence_links():
    output = compose_decision(complete_state()).model_dump(mode="json")

    assert set(output) == {
        "request_summary",
        "transport_options",
        "redemption_value",
        "loyalty_benefits",
        "recommendation",
        "tradeoffs",
        "evidence",
        "limitations",
    }
    assert output["recommendation"]["choice"] == "award_ticket"
    assert output["redemption_value"]["pence_per_mile"] == 1.75
    assert output["loyalty_benefits"][0]["evidence_id"] == "ba_silver_tier"
    assert any(item["id"] == "ba_silver_tier" for item in output["evidence"])


def test_partial_provider_failure_still_produces_degraded_decision():
    state = complete_state()
    state.record_tool_result(
        "train",
        {"status": "provider_error", "message": "timeout", "journeys": []},
    )

    output = compose_decision(state)

    assert output.transport_options == []
    assert output.recommendation.choice == "award_ticket"
    assert output.recommendation.confidence == "medium"
    assert any("铁路数据源暂不可用" in item for item in output.limitations)


def test_loyalty_dependency_is_optional_when_user_did_not_request_it():
    state = complete_state(loyalty=False)

    assert not state.loyalty_requested
    assert not loyalty_tool_enabled(wrapper(state), None)
    assert state.decision_dependencies_ready()
    assert compose_decision(state).loyalty_benefits == []
