import json
from pathlib import Path

from loyalty_scope import (
    check_loyalty_scope,
)


ROOT = Path(__file__).resolve().parent.parent

CASES_FILE = (
    ROOT
    / "evals"
    / "loyalty_retrieval_cases.json"
)


def load_cases() -> list[dict]:
    with open(
        CASES_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def main():
    cases = load_cases()

    correct = 0

    print(
        f"正在评估 {len(cases)} "
        f"个 Scope Cases..."
    )

    for case in cases:

        result = check_loyalty_scope(
            query=case["query"]
        )

        predicted = result["supported"]

        expected = case["should_answer"]

        is_correct = (
            predicted == expected
        )

        if is_correct:
            correct += 1

        print()
        print("=" * 70)

        print(
            f"Case: {case['id']}"
        )

        print(
            f"Query: {case['query']}"
        )

        print(
            f"Expected answerable: "
            f"{expected}"
        )

        print(
            f"Predicted answerable: "
            f"{predicted}"
        )

        print(
            f"Matched anchors: "
            f"{result['matched_anchors']}"
        )

        print(
            f"Result: "
            f"{'PASS' if is_correct else 'FAIL'}"
        )

    accuracy = (
        correct / len(cases)
    )

    print()
    print("=" * 70)

    print(
        f"Scope Accuracy: "
        f"{accuracy:.2%}"
    )

    print(
        f"Passed: "
        f"{correct}/{len(cases)}"
    )


if __name__ == "__main__":
    main()