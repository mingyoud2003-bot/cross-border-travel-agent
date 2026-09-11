from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents import Runner, SQLiteSession, set_tracing_disabled
from agents.items import ToolCallItem, ToolCallOutputItem

from agent import BASE_INSTRUCTIONS, agent_for_state, run_config_for, travel_agent
from state import TravelState
from turn_controller import controlled_turn_for


CASES_FILE = ROOT / "evals" / "agent_cases.json"
RESULTS_DIR = ROOT / "evals" / "results"
RESULT_FILE = RESULTS_DIR / "latest.json"

CORE_CASE_IDS = {
    "train_complete_london_paris",
    "train_missing_date_london_paris",
    "mileage_complete_400_50_20000",
    "mileage_missing_tax",
    "loyalty_supported_lounge",
    "loyalty_out_lufthansa_status",
    "decision_complete_ba_silver",
}

CLARIFICATION_TERMS = {
    "origin": ("出发", "起点", "从哪里", "从哪"),
    "destination": ("目的", "终点", "去哪", "到哪里", "前往", "哪个城市"),
    "travel_date": ("日期", "哪天", "什么时候", "出发时间"),
    "miles_required": ("里程", "avios", "积分"),
    "cash_price": ("现金", "票价", "价格"),
    "taxes": ("税", "税费", "附加费"),
}


class EvalConfigError(ValueError):
    pass


class EvalInfrastructureError(RuntimeError):
    pass


def load_local_env() -> None:
    """Load the local API key without logging or overwriting process config."""

    env_file = ROOT / ".env.local"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def load_cases() -> list[dict[str, Any]]:
    with CASES_FILE.open(encoding="utf-8") as file:
        cases = json.load(file)
    validate_cases(cases)
    return cases


def validate_cases(cases: Any) -> None:
    if not isinstance(cases, list) or not cases:
        raise EvalConfigError("Eval dataset must be a non-empty JSON list.")

    ids: list[str] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvalConfigError(f"Case {index} must be an object.")
        for key in ("id", "category"):
            if not case.get(key):
                raise EvalConfigError(f"Case {index} is missing {key}.")
        ids.append(case["id"])
        turns = normalized_turns(case)
        if not turns:
            raise EvalConfigError(f"Case {case['id']} has no turns.")
        for turn_index, turn in enumerate(turns, start=1):
            if not turn.get("query"):
                raise EvalConfigError(
                    f"Case {case['id']} turn {turn_index} has no query."
                )
            if turn.get("expected_behavior") not in {
                "tool_call",
                "clarify",
                "provider_abstain",
                "grounded_answer",
                "knowledge_abstain",
                "direct_answer",
                "decision",
            }:
                raise EvalConfigError(
                    f"Case {case['id']} turn {turn_index} has invalid behavior."
                )

    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise EvalConfigError(f"Duplicate case IDs: {', '.join(duplicates)}")


def normalized_turns(case: dict[str, Any]) -> list[dict[str, Any]]:
    if "turns" in case:
        return case["turns"]
    return [
        {
            key: value
            for key, value in case.items()
            if key not in {"id", "category", "description"}
        }
    ]


def render_query(template: str, *, today: date | None = None) -> str:
    base = today or date.today()

    def replace(match: re.Match[str]) -> str:
        offset = int(match.group(1))
        output_format = match.group(2)
        rendered = base + timedelta(days=offset)
        if output_format == "CN":
            return f"{rendered.year}年{rendered.month}月{rendered.day}日"
        return rendered.isoformat()

    return re.sub(r"\{\{DATE\+(\d+)(?:_(CN))?\}\}", replace, template)


def parse_tool_output(output: Any) -> Any:
    if not isinstance(output, str):
        return output
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def get_tool_name(item: ToolCallItem) -> str | None:
    name = getattr(item, "tool_name", None)
    if name:
        return name
    raw = getattr(item, "raw_item", None)
    if isinstance(raw, dict):
        return raw.get("name")
    return getattr(raw, "name", None)


def get_tool_arguments(item: ToolCallItem) -> Any:
    raw = getattr(item, "raw_item", None)
    arguments = raw.get("arguments") if isinstance(raw, dict) else getattr(
        raw, "arguments", None
    )
    return parse_tool_output(arguments)


def collect_run_data(result: Any) -> tuple[list[dict[str, Any]], list[Any]]:
    calls: list[dict[str, Any]] = []
    outputs: list[Any] = []
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            calls.append(
                {"name": get_tool_name(item), "arguments": get_tool_arguments(item)}
            )
        elif isinstance(item, ToolCallOutputItem):
            outputs.append(parse_tool_output(item.output))
    return calls, outputs


def infrastructure_error_from_outputs(outputs: list[Any]) -> str | None:
    markers = (
        "connection error",
        "request timed out",
        "rate limit",
        "service unavailable",
    )
    for output in outputs:
        if not isinstance(output, str):
            continue
        normalized = output.casefold()
        if "error occurred while running the tool" in normalized and any(
            marker in normalized for marker in markers
        ):
            return output
    return None


def collect_statuses(outputs: list[Any]) -> list[str]:
    return [
        str(output["status"])
        for output in outputs
        if isinstance(output, dict) and output.get("status")
    ]


def collect_evidence_ids(outputs: list[Any]) -> set[str]:
    ids: set[str] = set()
    for output in outputs:
        if not isinstance(output, dict):
            continue
        for item in output.get("evidence", []):
            if isinstance(item, dict) and item.get("id"):
                ids.add(item["id"])
    return ids


def collect_evidence_urls(outputs: list[Any]) -> set[str]:
    urls: set[str] = set()
    for output in outputs:
        if not isinstance(output, dict):
            continue
        for item in output.get("evidence", []):
            if isinstance(item, dict) and item.get("source_url"):
                urls.add(item["source_url"].rstrip(".,，。)）"))
    return urls


def grade_routing(expected_tool: str | list[str] | None, actual_tools: list[str]) -> bool:
    if expected_tool is None:
        return not actual_tools
    if isinstance(expected_tool, list):
        if len(actual_tools) != len(expected_tool):
            return False
        if expected_tool[-1:] == ["compose_travel_decision"]:
            return (
                actual_tools[-1:] == ["compose_travel_decision"]
                and sorted(actual_tools[:-1]) == sorted(expected_tool[:-1])
            )
        return actual_tools == expected_tool
    return actual_tools == [expected_tool]


def grade_clarification(
    turn: dict[str, Any], actual_tools: list[str], final_output: str
) -> bool:
    if actual_tools or not final_output.strip():
        return False

    normalized_output = normalize_for_match(final_output)

    missing_fields = turn.get("expected_missing_fields", [])
    if missing_fields:
        for field_name in missing_fields:
            terms = CLARIFICATION_TERMS.get(field_name, ())
            if not terms or not any(
                normalize_for_match(term) in normalized_output for term in terms
            ):
                return False
        return True

    terms = turn.get("expected_clarification_terms", [])
    return not terms or any(normalize_for_match(term) in normalized_output for term in terms)


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text.casefold()).replace("零", "0")


def grade_state(expectation: dict[str, Any] | None, state: TravelState) -> bool:
    if not expectation:
        return True
    if "current_task" in expectation and state.current_task != expectation["current_task"]:
        return False
    if "previous_task" in expectation and state.previous_task != expectation["previous_task"]:
        return False
    if "missing_fields" in expectation and state.missing_fields() != expectation["missing_fields"]:
        return False
    for field_name, expected_value in expectation.get("slots", {}).items():
        if isinstance(expected_value, str):
            expected_value = render_query(expected_value)
        if state.value(field_name) != expected_value:
            return False
    for field_name, expected_turn in expectation.get("provenance_turns", {}).items():
        provenance = state.provenance(field_name)
        if provenance is None or provenance.turn_id != expected_turn:
            return False
    expected_pending = expectation.get("pending_confirmation")
    if expected_pending is not None:
        pending = state.pending_confirmation
        if pending is None or any(
            getattr(pending, key) != value for key, value in expected_pending.items()
        ):
            return False
    return True


def grade_output_contract(turn: dict[str, Any], final_output: str) -> bool:
    normalized = final_output.casefold()
    required_all = turn.get("required_output_terms", [])
    required_any = turn.get("required_output_any", [])
    forbidden = turn.get("forbidden_output_terms", [])
    return (
        all(term.casefold() in normalized for term in required_all)
        and (not required_any or any(term.casefold() in normalized for term in required_any))
        and not any(term.casefold() in normalized for term in forbidden)
    )


def grade_behavior(
    turn: dict[str, Any],
    actual_tools: list[str],
    outputs: list[Any],
    final_output: str,
) -> bool:
    behavior = turn["expected_behavior"]
    expected_tool = turn.get("expected_tool")
    statuses = collect_statuses(outputs)
    has_output = bool(final_output.strip())

    if behavior == "clarify":
        return grade_clarification(turn, actual_tools, final_output)
    if behavior == "tool_call":
        base = actual_tools == [expected_tool] and has_output
        expected_status = turn.get("expected_status")
        expected_value = turn.get("expected_pence_per_mile")
        if expected_status and expected_status not in statuses:
            base = False
        if expected_value is not None:
            base = base and any(
                isinstance(output, dict)
                and output.get("pence_per_mile") == expected_value
                for output in outputs
            )
        if expected_tool == "calculate_mileage_value":
            base = base and "success" in statuses
        if expected_tool == "search_train":
            allowed_statuses = {"success", "no_results", "provider_error"}
            base = base and bool(allowed_statuses.intersection(statuses))
            normalized = normalize_for_match(final_output)
            if "provider_error" in statuses:
                base = base and any(
                    term in normalized for term in ("不可用", "失败", "稍后", "暂时")
                )
            if "no_results" in statuses:
                base = base and any(
                    term in normalized for term in ("未找到", "没有", "无可用")
                )
        return base
    if behavior == "provider_abstain":
        if expected_tool is None:
            normalized = normalize_for_match(final_output)
            return (
                not actual_tools
                and has_output
                and any(term in normalized for term in ("不支持", "仅支持", "无法查询"))
            )
        return actual_tools == [expected_tool] and "provider_error" in statuses and has_output
    if behavior == "grounded_answer":
        required = set(turn.get("required_evidence_ids", []))
        evidence_urls = collect_evidence_urls(outputs)
        # Full-width punctuation surrounding a URL is prose, not part of the
        # citation. Excluding it avoids false negatives in Chinese answers.
        cited_urls = set(
            re.findall(r"https?://[^\s\]\[<>，。；、（）()]+", final_output)
        )
        return (
            actual_tools == [expected_tool]
            and "success" in statuses
            and required.issubset(collect_evidence_ids(outputs))
            and bool(cited_urls)
            and cited_urls.issubset(evidence_urls)
            and has_output
        )
    if behavior == "knowledge_abstain":
        return (
            actual_tools == [expected_tool]
            and any(status in statuses for status in ("out_of_scope", "no_evidence"))
            and has_output
        )
    if behavior == "direct_answer":
        return not actual_tools and has_output
    if behavior == "decision":
        expected_tools = turn.get("expected_tools", [])
        if not grade_routing(expected_tools, actual_tools):
            return False
        try:
            structured = json.loads(final_output)
        except (TypeError, json.JSONDecodeError):
            return False
        required_keys = {
            "request_summary",
            "transport_options",
            "redemption_value",
            "loyalty_benefits",
            "recommendation",
            "tradeoffs",
            "evidence",
            "limitations",
        }
        if set(structured) != required_keys:
            return False
        expected_choice = turn.get("expected_choice")
        if expected_choice and structured.get("recommendation", {}).get("choice") != expected_choice:
            return False
        expected_value = turn.get("expected_pence_per_mile")
        if expected_value is not None and structured.get("redemption_value", {}).get(
            "pence_per_mile"
        ) != expected_value:
            return False
        required_evidence = set(turn.get("required_evidence_ids", []))
        actual_evidence = {
            item.get("id") for item in structured.get("evidence", []) if isinstance(item, dict)
        }
        return required_evidence.issubset(actual_evidence)
    return False


def usage_dict(result: Any) -> dict[str, int]:
    usage = result.context_wrapper.usage
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


def run_turn(
    turn: dict[str, Any], state: TravelState, session: SQLiteSession
) -> dict[str, Any]:
    query = render_query(turn["query"])
    state.begin_turn(query)
    controlled = controlled_turn_for(state)
    if controlled:
        final_output = controlled.message
        calls: list[dict[str, Any]] = []
        outputs: list[Any] = []
        usage = {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
    else:
        result = Runner.run_sync(
            agent_for_state(state),
            query,
            session=session,
            context=state,
            run_config=run_config_for(state),
        )
        final_output = str(result.final_output or "")
        calls, outputs = collect_run_data(result)
        usage = usage_dict(result)
    infrastructure_error = infrastructure_error_from_outputs(outputs)
    if infrastructure_error:
        raise EvalInfrastructureError(infrastructure_error)
    actual_tools = [call["name"] for call in calls if call["name"]]
    expected_route = turn.get("expected_tools", turn.get("expected_tool"))
    route_pass = grade_routing(expected_route, actual_tools)
    behavior_pass = grade_behavior(turn, actual_tools, outputs, final_output)
    state_pass = grade_state(turn.get("expected_state"), state)
    output_pass = grade_output_contract(turn, final_output)
    return {
        "query": query,
        "expected_tool": turn.get("expected_tool"),
        "expected_tools": turn.get("expected_tools"),
        "expected_behavior": turn["expected_behavior"],
        "tool_calls": calls,
        "tool_outputs": outputs,
        "tool_statuses": collect_statuses(outputs),
        "evidence_ids": sorted(collect_evidence_ids(outputs)),
        "final_output": final_output,
        "control_reason": controlled.reason if controlled else None,
        "state": state.snapshot(),
        "route_pass": route_pass,
        "behavior_pass": behavior_pass,
        "state_pass": state_pass,
        "output_pass": output_pass,
        "pass": route_pass and behavior_pass and state_pass and output_pass,
        "usage": usage,
    }


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    state = TravelState()
    session = SQLiteSession(f"eval-{uuid4().hex}", ":memory:")
    turns = []
    error = None
    for turn in normalized_turns(case):
        try:
            turns.append(run_turn(turn, state, session))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
    result = {
        "id": case["id"],
        "category": case["category"],
        "turns": turns,
        "pass": error is None and all(turn["pass"] for turn in turns),
        "usage": {
            key: sum(turn["usage"][key] for turn in turns)
            for key in ("requests", "input_tokens", "output_tokens", "total_tokens")
        },
    }
    if error:
        result["error"] = error
    return result


def failed_case(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "id": case["id"],
        "category": case["category"],
        "turns": [],
        "pass": False,
        "usage": {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "error": f"{type(exc).__name__}: {exc}",
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_metadata(selected_cases: list[dict[str, Any]]) -> dict[str, Any]:
    knowledge_files = [
        ROOT / "knowledge" / "loyalty_rules.jsonl",
        ROOT / "knowledge" / "loyalty_index.json",
    ]
    application_files = [ROOT / name for name in (
        "agent.py",
        "state.py",
        "tools.py",
        "loyalty_service.py",
        "loyalty_retriever.py",
        "loyalty_scope.py",
        "transport_provider.py",
        "turn_controller.py",
        "decision_service.py",
    )]
    return {
        "run_at": datetime.now(UTC).isoformat(),
        "model": str(travel_agent.model),
        "python": platform.python_version(),
        "openai_agents": importlib.metadata.version("openai-agents"),
        "openai": importlib.metadata.version("openai"),
        "prompt_sha256": hashlib.sha256(BASE_INSTRUCTIONS.encode()).hexdigest(),
        "dataset_sha256": sha256_file(CASES_FILE),
        "knowledge_sha256": hashlib.sha256(
            b"".join(path.read_bytes() for path in knowledge_files)
        ).hexdigest(),
        "application_sha256": hashlib.sha256(
            b"".join(path.read_bytes() for path in application_files)
        ).hexdigest(),
        "harness_sha256": sha256_file(Path(__file__)),
        "selected_case_ids": [case["id"] for case in selected_cases],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    turns = [
        turn
        for result in results
        if "error" not in result
        for turn in result["turns"]
    ]
    category_data: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        category_data[result["category"]].append(result)

    def rate(key: str) -> float:
        return sum(turn[key] for turn in turns) / len(turns) if turns else 0.0

    completed = [result for result in results if "error" not in result]
    passed = sum(result["pass"] for result in completed)
    return {
        "cases": len(results),
        "turns": len(turns),
        "execution_completed": len(completed),
        "infrastructure_errors": len(results) - len(completed),
        "case_passed": passed,
        "case_pass_rate": passed / len(completed) if completed else 0.0,
        "routing_accuracy": rate("route_pass"),
        "behavior_accuracy": rate("behavior_pass"),
        "state_accuracy": rate("state_pass"),
        "output_contract_accuracy": rate("output_pass"),
        "total_tokens": sum(result["usage"]["total_tokens"] for result in results),
        "by_category": {
            category: _summarize_category(values)
            for category, values in sorted(category_data.items())
        },
    }


def _summarize_category(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [result for result in results if "error" not in result]
    passed = sum(result["pass"] for result in completed)
    return {
        "cases": len(results),
        "execution_completed": len(completed),
        "infrastructure_errors": len(results) - len(completed),
        "passed": passed,
        "pass_rate": passed / len(completed) if completed else 0.0,
    }


def regrade_payload(
    payload: dict[str, Any],
    all_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    cases_by_id = {case["id"]: case for case in all_cases}
    for result in payload["results"]:
        if result.get("error"):
            continue
        expected_turns = normalized_turns(cases_by_id[result["id"]])
        for index, turn_result in enumerate(result["turns"]):
            outputs = turn_result.get("tool_outputs", [])
            infrastructure_error = infrastructure_error_from_outputs(outputs)
            if infrastructure_error:
                result["error"] = f"EvalInfrastructureError: {infrastructure_error}"
                result["pass"] = False
                break
            expected = expected_turns[index]
            actual_tools = [
                call["name"]
                for call in turn_result.get("tool_calls", [])
                if call.get("name")
            ]
            expected_route = expected.get("expected_tools", expected.get("expected_tool"))
            route_pass = grade_routing(expected_route, actual_tools)
            behavior_pass = grade_behavior(
                expected,
                actual_tools,
                outputs,
                turn_result.get("final_output", ""),
            )
            output_pass = grade_output_contract(
                expected,
                turn_result.get("final_output", ""),
            )
            turn_result.update(
                {
                    "route_pass": route_pass,
                    "behavior_pass": behavior_pass,
                    "output_pass": output_pass,
                    "pass": (
                    route_pass
                    and behavior_pass
                    and turn_result.get("state_pass", False)
                    and output_pass
                    ),
                }
            )
        if "error" not in result:
            result["pass"] = all(turn["pass"] for turn in result["turns"])

    payload["metadata"] = build_metadata(all_cases)
    payload["metadata"]["regraded_at"] = datetime.now(UTC).isoformat()
    payload["summary"] = summarize(payload["results"])
    return payload


def select_cases(all_cases: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.case:
        selected = [case for case in all_cases if case["id"] == args.case]
    elif getattr(args, "category", None):
        selected = [case for case in all_cases if case["category"] == args.category]
    elif args.all:
        selected = all_cases
    else:
        selected = [case for case in all_cases if case["id"] in CORE_CASE_IDS]
    if not selected:
        raise EvalConfigError("No eval cases selected; refusing to report a false pass.")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--case")
    parser.add_argument("--category")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--retry-infra",
        action="store_true",
        help="Retry only infrastructure-error cases from the latest full run.",
    )
    parser.add_argument(
        "--regrade-latest",
        action="store_true",
        help="Recompute graders from saved tool calls/outputs without API calls.",
    )
    args = parser.parse_args()

    all_cases = load_cases()
    if args.regrade_latest:
        if not RESULT_FILE.exists():
            raise EvalConfigError("latest.json does not exist; nothing to regrade.")
        payload = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        payload = regrade_payload(payload, all_cases)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        run_file = RESULTS_DIR / f"run-{run_stamp}-regraded.json"
        run_file.write_text(serialized, encoding="utf-8")
        RESULT_FILE.write_text(serialized, encoding="utf-8")
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
        print(f"Regraded run saved to: {run_file}")
        return

    resume_payload = None
    if args.retry_infra:
        if not RESULT_FILE.exists():
            raise EvalConfigError("latest.json does not exist; nothing to retry.")
        resume_payload = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        retry_ids = {
            result["id"] for result in resume_payload["results"] if result.get("error")
        }
        selected = [case for case in all_cases if case["id"] in retry_ids]
        if not selected:
            raise EvalConfigError("The latest run has no infrastructure errors.")
        current_metadata = build_metadata(all_cases)
        for key in (
            "prompt_sha256",
            "dataset_sha256",
            "knowledge_sha256",
            "application_sha256",
            "harness_sha256",
        ):
            if resume_payload["metadata"].get(key) != current_metadata.get(key):
                raise EvalConfigError(
                    "Code, prompt, dataset, or knowledge changed; run --all instead of resuming."
                )
    else:
        selected = select_cases(all_cases, args)
    if args.validate_only:
        print(f"Validated {len(all_cases)} cases; selected {len(selected)} cases.")
        return

    load_local_env()
    if not os.environ.get("OPENAI_API_KEY"):
        raise EvalConfigError("OPENAI_API_KEY is required for Agent evals.")

    # Local regression output is the source of truth here. Disabling remote
    # trace export avoids background retries masking the actual model failure.
    set_tracing_disabled(True)

    results: list[dict[str, Any]] = []
    for index, case in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] {case['id']}", flush=True)
        try:
            result = run_case(case)
        except Exception as exc:
            result = failed_case(case, exc)
        results.append(result)
        print(
            "PASS" if result["pass"] else f"FAIL: {result.get('error', 'contract mismatch')}",
            flush=True,
        )

    if resume_payload is not None:
        replacements = {result["id"]: result for result in results}
        results = [
            replacements.get(result["id"], result)
            for result in resume_payload["results"]
        ]

    summary = summarize(results)
    metadata_cases = all_cases if args.all or args.retry_infra else selected
    payload = {
        "metadata": build_metadata(metadata_cases),
        "summary": summary,
        "results": results,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_file = RESULTS_DIR / f"run-{run_stamp}.json"
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    run_file.write_text(serialized, encoding="utf-8")
    completed_turns = summary["turns"]
    should_update_latest = completed_turns and (args.all or args.retry_infra)
    if should_update_latest:
        RESULT_FILE.write_text(serialized, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Run saved to: {run_file}")
    if should_update_latest:
        print(f"Latest successful execution saved to: {RESULT_FILE}")
    elif not completed_turns:
        print("No turns completed; latest.json was not replaced.")
    else:
        print("Focused/smoke run did not replace the full latest.json baseline.")
    if (
        summary["infrastructure_errors"]
        or summary["case_passed"] != summary["execution_completed"]
    ):
        sys.exit(1)


if __name__ == "__main__":
    main()
