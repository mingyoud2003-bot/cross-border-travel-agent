from loyalty_retriever import (
    search_loyalty_knowledge,
)
from loyalty_scope import (
    check_loyalty_scope,
)


DEFAULT_TOP_K = 4


def get_loyalty_evidence(
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> dict:
    """
    Retrieve grounded loyalty evidence
    for a user query.

    The service first checks whether the
    query is covered by the current
    knowledge-base scope. Only supported
    queries proceed to vector retrieval.
    """

    scope_result = check_loyalty_scope(
        query=query,
    )

    if not scope_result["supported"]:
        return {
            "status": "out_of_scope",
            "query": query,
            "matched_anchors": [],
            "message": scope_result["reason"],
            "evidence": [],
        }

    results = search_loyalty_knowledge(
        query=query,
        top_k=top_k,
    )

    if not results:
        return {
            "status": "no_evidence",
            "query": query,
            "matched_anchors": (
                scope_result["matched_anchors"]
            ),
            "message": (
                "当前知识库未检索到足够的"
                "常旅客权益证据。"
            ),
            "evidence": [],
        }

    evidence = []

    for item in results:
        evidence.append(
            {
                "id": item["id"],
                "title": item["title"],
                "content": item["content"],
                "source": item["source"],
                "source_url": (
                    item["source_url"]
                ),
            }
        )

    return {
        "status": "success",
        "query": query,
        "matched_anchors": (
            scope_result["matched_anchors"]
        ),
        "evidence": evidence,
    }