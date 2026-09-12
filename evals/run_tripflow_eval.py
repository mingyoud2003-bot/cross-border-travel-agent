from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
        checks.append({"name": f"equals:{path}", "pass": actual == expected, "expected": expected, "actual": actual})
    for path in case.get("missing", []):
        collection, index, field = path.split(".", 2)
        items = output.get(collection, [])
        actual = items[int(index)].get("missing_fields", []) if len(items) > int(index) else []
        checks.append({"name": f"missing:{path}", "pass": field in actual, "expected": field, "actual": actual})
    return {"passed": all(check["pass"] for check in checks), "checks": checks}


async def run(cases: list[dict[str, Any]], category: str | None) -> dict[str, Any]:
    selected = [case for case in cases if category is None or case["category"] == category]
    service = AgentsProposalService()
    results = []
    for case in selected:
        try:
            proposal = await service.propose(case["input"])
            output = proposal.model_dump(mode="json")
            result = grade(case, output)
            results.append({"id": case["id"], "category": case["category"], **result, "output": output})
            print(f"{'PASS' if result['passed'] else 'FAIL'} {case['id']}")
        except Exception as exc:
            results.append({"id": case["id"], "category": case["category"], "passed": False, "infrastructure_error": type(exc).__name__})
            print(f"ERROR {case['id']}: {type(exc).__name__}")
    passed = sum(item["passed"] for item in results)
    by_category = Counter(item["category"] for item in results if item["passed"])
    totals = Counter(item["category"] for item in results)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": CASES_PATH.name,
        "summary": {"passed": passed, "total": len(results), "pass_rate": passed / len(results) if results else 0, "by_category": {key: {"passed": by_category[key], "total": total} for key, total in totals.items()}},
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real TripFlow extraction Eval")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--category")
    args = parser.parse_args()
    cases = load_cases()
    print(f"validated {len(cases)} TripFlow Eval cases")
    if args.validate_only:
        return
    load_local_env()
    report = asyncio.run(run(cases, args.category))
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = report["summary"]
    print(f"result: {summary['passed']}/{summary['total']} ({summary['pass_rate']:.1%})")
    raise SystemExit(0 if summary["passed"] == summary["total"] else 1)


if __name__ == "__main__":
    main()
