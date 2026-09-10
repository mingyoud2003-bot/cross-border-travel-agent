from argparse import Namespace
from datetime import date
import json

import pytest

from evals.run_agent_eval import (
    EvalConfigError,
    grade_clarification,
    grade_behavior,
    grade_state,
    infrastructure_error_from_outputs,
    load_cases,
    normalize_for_match,
    render_query,
    select_cases,
    validate_cases,
)
from state import TravelState


def test_dataset_has_target_case_count_and_unique_ids():
    cases = load_cases()

    assert 80 <= len(cases) <= 100
    assert len({case["id"] for case in cases}) == len(cases)


def test_relative_date_rendering_is_stable():
    assert render_query("{{DATE+2}} / {{DATE+3_CN}}", today=date(2030, 1, 1)) == (
        "2030-01-03 / 2030年1月4日"
    )


def test_empty_dataset_is_rejected():
    with pytest.raises(EvalConfigError, match="non-empty"):
        validate_cases([])


def test_duplicate_case_ids_are_rejected():
    cases = [
        {
            "id": "duplicate",
            "category": "general",
            "query": "a",
            "expected_behavior": "direct_answer",
        },
        {
            "id": "duplicate",
            "category": "general",
            "query": "b",
            "expected_behavior": "direct_answer",
        },
    ]

    with pytest.raises(EvalConfigError, match="Duplicate"):
        validate_cases(cases)


def test_unknown_case_selection_is_not_false_pass():
    with pytest.raises(EvalConfigError, match="No eval cases selected"):
        select_cases(
            load_cases(),
            Namespace(case="does-not-exist", all=False),
        )


def test_default_smoke_selection_contains_seven_real_cases():
    selected = select_cases(load_cases(), Namespace(case=None, all=False))

    assert len(selected) == 7


def test_destination_clarification_accepts_natural_paraphrase():
    turn = {
        "expected_missing_fields": ["destination"],
    }

    assert grade_clarification(
        turn,
        [],
        "请问您想从巴黎乘火车前往哪个城市？",
    )


def test_state_grader_checks_provenance_turn():
    state = TravelState()
    state.begin_turn("从伦敦出发坐火车")

    assert grade_state(
        {
            "current_task": "train",
            "slots": {"origin": "伦敦"},
            "provenance_turns": {"origin": 1},
        },
        state,
    )


def test_state_grader_rejects_wrong_provenance_turn():
    state = TravelState()
    state.begin_turn("从伦敦出发坐火车")

    assert not grade_state({"provenance_turns": {"origin": 2}}, state)


def test_match_normalization_treats_chinese_and_numeric_zero_equally():
    assert normalize_for_match("必须大于 零") == normalize_for_match("必须大于0")


def test_grounded_answer_rejects_invented_citation_url():
    turn = {
        "expected_behavior": "grounded_answer",
        "expected_tool": "retrieve_loyalty_benefits",
        "required_evidence_ids": ["rule-1"],
    }
    outputs = [
        {
            "status": "success",
            "evidence": [
                {"id": "rule-1", "source_url": "https://official.example/rule"}
            ],
        }
    ]

    assert not grade_behavior(
        turn,
        ["retrieve_loyalty_benefits"],
        outputs,
        "来源：https://invented.example/rule",
    )


def test_grounded_answer_accepts_returned_citation_url():
    turn = {
        "expected_behavior": "grounded_answer",
        "expected_tool": "retrieve_loyalty_benefits",
        "required_evidence_ids": ["rule-1"],
    }
    outputs = [
        {
            "status": "success",
            "evidence": [
                {"id": "rule-1", "source_url": "https://official.example/rule"}
            ],
        }
    ]

    assert grade_behavior(
        turn,
        ["retrieve_loyalty_benefits"],
        outputs,
        "来源：https://official.example/rule",
    )


def test_train_provider_error_requires_user_visible_abstention():
    turn = {"expected_behavior": "tool_call", "expected_tool": "search_train"}

    assert not grade_behavior(
        turn,
        ["search_train"],
        [{"status": "provider_error"}],
        "伦敦到巴黎有一班车。",
    )


def test_tool_connection_failure_is_classified_as_infrastructure():
    error = infrastructure_error_from_outputs(
        [
            "An error occurred while running the tool. Please try again. "
            "Error: Connection error."
        ]
    )

    assert error is not None


def test_business_provider_error_is_not_infrastructure():
    assert infrastructure_error_from_outputs(
        [{"status": "provider_error", "message": "铁路服务暂时不可用"}]
    ) is None


def test_decision_grader_requires_structured_output_and_dependency_graph():
    turn = {
        "expected_behavior": "decision",
        "expected_tools": [
            "search_train",
            "calculate_mileage_value",
            "compose_travel_decision",
        ],
        "expected_choice": "award_ticket",
        "expected_pence_per_mile": 1.75,
        "required_evidence_ids": ["redemption_formula_v1"],
    }
    output = {
        "request_summary": {},
        "transport_options": [],
        "redemption_value": {"pence_per_mile": 1.75},
        "loyalty_benefits": [],
        "recommendation": {"choice": "award_ticket"},
        "tradeoffs": [],
        "evidence": [{"id": "redemption_formula_v1"}],
        "limitations": [],
    }

    assert grade_behavior(
        turn,
        ["calculate_mileage_value", "search_train", "compose_travel_decision"],
        [],
        json.dumps(output),
    )
    assert not grade_behavior(
        turn,
        ["compose_travel_decision"],
        [],
        json.dumps(output),
    )
