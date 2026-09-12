from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from settings import load_local_env
from tripflow_agent import AgentsProposalService


EVALS_ROOT = PROJECT_ROOT / "evals"
CASES_PATH = EVALS_ROOT / "tripflow_cases.json"
RESULTS_PATH = EVALS_ROOT / "results" / "tripflow-latest.json"


def load_cases() -> list[dict[str, Any]]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("TripFlow Eval dataset must be a non-empty JSON array")
    seen: set[str] = set()
    for case in cases:
        required = {"id", "category", "input", "counts", "equals"}
        if not required.issubset(case):
            raise ValueError(f"{case.get('id', '<unknown>')}: missing required keys")
        if case["id"] in seen:
            raise ValueError(f"duplicate case id: {case['id']}")
        seen.add(case["id"])
        if set(case["counts"]) != {"transports", "stays"}:
            raise ValueError(f"{case['id']}: counts must cover transports and stays")
    return cases


def value_at(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current.get(part)
        if current is None:
            return None
    return current


def grade(case: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for collection, expected in case["counts"].items():
        actual = len(output.get(collection, []))
        checks.append({"name": f"count:{collection}", "pass": actual == expected, "expected": expected, "actual": actual})
    for path, expected in case["equals"].items():
        actual = value_at(output, path)
        passed = actual in expected if isinstance(expected, list) else actual == expected
        checks.append({"name": f"equals:{path}", "pass": passed, "expected": expected, "actual": actual})
    for path in case.get("missing", []):
        collection, index, field = path.split(".", 2)
        items = output.get(collection, [])
        actual = items[int(index)].get("missing_fields", []) if len(items) > int(index) else []
        checks.append({"name": f"missing:{path}", "pass": field in actual, "expected": field, "actual": actual})
    return {"passed": all(check["pass"] for check in checks), "checks": checks}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [item for item in results if "infrastructure_error" not in item]
    passed = sum(item["passed"] for item in scored)
    infrastructure_errors = len(results) - len(scored)
    by_category = Counter(item["category"] for item in scored if item["passed"])
    totals = Counter(item["category"] for item in scored)
    return {
        "passed": passed,
        "scored": len(scored),
        "total_cases": len(results),
        "infrastructure_errors": infrastructure_errors,
        "product_pass_rate": passed / len(scored) if scored else 0,
        "by_category": {
            key: {"passed": by_category[key], "total": total}
            for key, total in totals.items()
        },
    }


def regrade_report(
    cases: list[dict[str, Any]], report: dict[str, Any]
) -> dict[str, Any]:
    cases_by_id = {case["id"]: case for case in cases}
    regraded = []
    for previous in report.get("results", []):
        case = cases_by_id.get(previous["id"])
        if case is None:
            raise ValueError(f"result references unknown case: {previous['id']}")
        if "infrastructure_error" in previous:
            regraded.append(previous)
            continue
        result = grade(case, previous["output"])
        regraded.append({**previous, **result})
    if len(regraded) != len(cases):
        raise ValueError("regrade requires a full result set matching the dataset")
    return {
        **report,
        "regraded_at": datetime.now(UTC).isoformat(),
        "summary": summarize(regraded),
        "results": regraded,
    }


async def run(
    cases: list[dict[str, Any]],
    category: str | None,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    requested = set(case_ids or [])
    selected = [
        case
        for case in cases
        if (category is None or case["category"] == category)
        and (not requested or case["id"] in requested)
    ]
    if not selected:
        raise ValueError("no TripFlow Eval cases matched the selection")
    unknown = requested - {case["id"] for case in selected}
    if unknown:
        raise ValueError(f"unknown or category-mismatched case ids: {sorted(unknown)}")
    service = AgentsProposalService()
    results = []
    for case in selected:
        started = time.perf_counter()
        try:
            proposal = await service.propose(case["input"])
            output = proposal.model_dump(mode="json")
            result = grade(case, output)
            results.append({"id": case["id"], "category": case["category"], **result, "latency_ms": round((time.perf_counter() - started) * 1000), "output": output})
            print(f"{'PASS' if result['passed'] else 'FAIL'} {case['id']}")
        except Exception as exc:
            results.append({"id": case["id"], "category": case["category"], "passed": False, "latency_ms": round((time.perf_counter() - started) * 1000), "infrastructure_error": type(exc).__name__})
            print(f"ERROR {case['id']}: {type(exc).__name__}")
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": CASES_PATH.name,
        "summary": summarize(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real TripFlow extraction Eval")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--category")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--regrade", action="store_true")
    args = parser.parse_args()
    cases = load_cases()
    print(f"validated {len(cases)} TripFlow Eval cases")
    if args.validate_only:
        return
    load_local_env()
    if args.regrade:
        if args.category or args.case_ids:
            parser.error("--regrade cannot be combined with --category or --case")
        previous = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        report = regrade_report(cases, previous)
    else:
        report = asyncio.run(run(cases, args.category, args.case_ids))
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = report["summary"]
    print(f"product result: {summary['passed']}/{summary['scored']} ({summary['product_pass_rate']:.1%}); infrastructure errors: {summary['infrastructure_errors']}")
    clean = summary["passed"] == summary["scored"] and summary["infrastructure_errors"] == 0
    raise SystemExit(0 if clean else 1)


if __name__ == "__main__":
    main()
