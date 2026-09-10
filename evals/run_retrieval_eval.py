import json
from pathlib import Path

from loyalty_retriever import rank_loyalty_knowledge


ROOT = Path(__file__).resolve().parent.parent

CASES_FILE = (
    ROOT
    / "evals"
    / "loyalty_retrieval_cases.json"
)


def load_cases() -> list[dict]:
    """
    Load retrieval evaluation cases.
    """
    with open(
        CASES_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def main():
    cases = load_cases()

    # --------------------------------------------------
    # Step 1:
    # 每个 Query 只生成一次 Embedding，
    # 得到完整的知识块排序。
    # --------------------------------------------------

    ranked_results = {}

    print(
        f"正在评估 {len(cases)} 个 Retrieval Cases..."
    )

    for case in cases:
        ranked_results[case["id"]] = (
            rank_loyalty_knowledge(
                query=case["query"],
            )
        )

    # --------------------------------------------------
    # Step 2:
    # 不重新调用 Embedding API，
    # 直接在本地比较不同 Top-K。
    # --------------------------------------------------

    for k in [1, 2, 3, 4, 5]:

        total_expected = 0
        total_matched = 0
        hit_count = 0

        print()
        print("=" * 70)
        print(f"Top-K = {k}")
        print("=" * 70)

        for case in cases:

            results = (
                ranked_results[case["id"]][:k]
            )

            retrieved_ids = [
                result["id"]
                for result in results
            ]

            expected_ids = (
                case["expected_ids"]
            )

            matched = (
                set(retrieved_ids)
                & set(expected_ids)
            )

            recall = (
                len(matched)
                / len(expected_ids)
            )

            hit = bool(matched)

            total_expected += len(
                expected_ids
            )

            total_matched += len(
                matched
            )

            if hit:
                hit_count += 1

            print(
                f"\nCase: {case['id']}"
            )

            print(
                f"Query: {case['query']}"
            )

            print(
                f"Expected: {expected_ids}"
            )

            print(
                f"Retrieved: {retrieved_ids}"
            )

            print(
                f"Matched: {sorted(matched)}"
            )

            print(
                f"Recall@{k}: "
                f"{recall:.2%}"
            )

        overall_recall = (
            total_matched
            / total_expected
        )

        hit_rate = (
            hit_count
            / len(cases)
        )

        print()
        print("-" * 70)

        print(
            f"Overall Recall@{k}: "
            f"{overall_recall:.2%}"
        )

        print(
            f"Hit@{k}: "
            f"{hit_rate:.2%}"
        )


if __name__ == "__main__":
    main()