from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from settings import load_local_env
from travel_rules import RuleQuery, TravelRulesService
from tripflow_models import Location, TransportReservation, Trip


CASES_FILE = ROOT / "evals" / "travel_rules_cases.jsonl"
RESULT_FILE = ROOT / "evals" / "results" / "travel_rules_latest.json"
REQUIRED_CATEGORIES = {
    "grounded_answer",
    "context_binding",
    "prompt_injection",
    "needs_clarification",
    "out_of_scope",
    "unsupported_provider",
}


def load_cases() -> list[dict]:
    with CASES_FILE.open(encoding="utf-8") as file:
        cases = [json.loads(line) for line in file if line.strip()]
    ids = [case["id"] for case in cases]
    if len(cases) != 50 or len(ids) != len(set(ids)):
        raise ValueError("travel rules eval requires 50 unique cases")
    categories = {case["category"] for case in cases}
    if categories != REQUIRED_CATEGORIES:
        raise ValueError(f"unexpected categories: {sorted(categories)}")
    for case in cases:
        if case["expected_status"] not in {
            "answered", "needs_clarification", "out_of_scope"
        }:
            raise ValueError(f"invalid status in {case['id']}")
        if case["expected_status"] == "answered" and not case["required_evidence_ids"]:
            raise ValueError(f"answered case lacks evidence contract: {case['id']}")
    return cases


def make_trip(operator: str) -> Trip:
    item = TransportReservation(
        mode="flight" if operator not in {"Deutsche Bahn", "Eurostar"} else "train",
        operator=operator,
        service_number="TF100",
        origin=Location(name="London Heathrow", city="London", timezone="Europe/London"),
        destination=Location(
            name="Los Angeles International",
            city="Los Angeles",
            timezone="America/Los_Angeles",
        ),
        departure_at="2026-10-26T10:00:00+00:00",
        arrival_at="2026-10-26T14:00:00-07:00",
        status="confirmed",
        provenance={},
    )
    return Trip(
        title="Travel rules eval",
        home_timezone="Asia/Shanghai",
        minimum_connection_minutes=90,
        reservations=[item],
    )


async def run(cases: list[dict], *, infrastructure_retries: int = 3) -> dict:
    service = TravelRulesService()
    results = []
    dependency_circuit_open = False
    for case in cases:
        trip = make_trip(case["operator"])
        item = trip.reservations[0]
        attempts = 0
        while True:
            attempts += 1
            answer = await service.ask(
                trip,
                RuleQuery(question=case["question"], reservation_id=item.id),
            )
            allowed_retries = 0 if dependency_circuit_open else infrastructure_retries
            if answer.status != "unavailable" or attempts > allowed_retries:
                break
            # Eval infrastructure failures are not product behavior failures. A
            # bounded backoff absorbs transient model/embedding rate limits while
            # preserving a final unavailable result when the dependency stays down.
            delay = (5, 15, 30)[min(attempts - 1, 2)]
            print(f"RETRY {case['id']} after unavailable ({delay}s)", flush=True)
            await asyncio.sleep(delay)
        if answer.status == "unavailable" and attempts > 1:
            dependency_circuit_open = True
        citation_ids = {item.evidence_id for item in answer.citations}
        missing = set(case["required_evidence_ids"]) - citation_ids
        passed = answer.status == case["expected_status"] and not missing
        results.append({
            "id": case["id"],
            "category": case["category"],
            "passed": passed,
            "expected_status": case["expected_status"],
            "actual_status": answer.status,
            "required_evidence_ids": case["required_evidence_ids"],
            "citation_ids": sorted(citation_ids),
            "retrieved_evidence_ids": answer.retrieved_evidence_ids,
            "latency_ms": answer.latency_ms,
            "answer": answer.answer,
            "attempts": attempts,
        })
        print(
            f"{'PASS' if passed else 'FAIL'} {case['id']} -> {answer.status}",
            flush=True,
        )
    passed = sum(item["passed"] for item in results)
    latencies = [item["latency_ms"] for item in results]
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    category_totals = Counter(item["category"] for item in results)
    category_passed = Counter(item["category"] for item in results if item["passed"])
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "summary": {
            "passed": passed,
            "total": len(results),
            "pass_rate": passed / len(results),
            "mean_latency_ms": round(statistics.mean(latencies), 1),
            "p95_latency_ms": p95,
            "infrastructure_unavailable": sum(
                item["actual_status"] == "unavailable" for item in results
            ),
            "categories": {
                key: {"passed": category_passed[key], "total": value}
                for key, value in sorted(category_totals.items())
            },
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--infrastructure-retries", type=int, default=3)
    args = parser.parse_args()
    cases = load_cases()
    print(f"validated {len(cases)} travel-rule eval cases")
    if args.validate_only:
        return
    load_local_env()
    selected = cases[: args.limit] if args.limit else cases
    report = asyncio.run(
        run(selected, infrastructure_retries=max(0, args.infrastructure_retries))
    )
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = report["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["passed"] != summary["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
