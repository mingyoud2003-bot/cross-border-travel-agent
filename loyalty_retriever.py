import json
import math
from pathlib import Path

from openai import OpenAI


ROOT = Path(__file__).resolve().parent

INDEX_FILE = (
    ROOT
    / "knowledge"
    / "loyalty_index.json"
)

EMBEDDING_MODEL = "text-embedding-3-small"

HYBRID_EVIDENCE_RULES = (
    (("ba silver", "british airways silver", "英航银卡", "英国航空银卡"), "ba_silver_tier"),
    (("qatar airways", "卡塔尔航空"), "qatar_membership"),
    (("guest", "宾客", "朋友", "同行", "带一"), "sapphire_guest"),
    (("lounge", "休息室"), "sapphire_lounge"),
)


def load_index() -> list[dict]:
    """
    Load the pre-built loyalty knowledge index.
    """

    with open(
        INDEX_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def cosine_similarity(
    vector_a: list[float],
    vector_b: list[float],
) -> float:
    """
    Calculate cosine similarity between two vectors.
    """

    dot_product = sum(
        a * b
        for a, b in zip(
            vector_a,
            vector_b,
        )
    )

    magnitude_a = math.sqrt(
        sum(
            value * value
            for value in vector_a
        )
    )

    magnitude_b = math.sqrt(
        sum(
            value * value
            for value in vector_b
        )
    )

    if (
        magnitude_a == 0
        or magnitude_b == 0
    ):
        return 0.0

    return (
        dot_product
        / (
            magnitude_a
            * magnitude_b
        )
    )


def rank_loyalty_knowledge(
    query: str,
) -> list[dict]:
    """
    Rank all loyalty knowledge chunks
    by semantic similarity.
    """

    client = OpenAI()

    index = load_index()

    # Only the query needs a new embedding.
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )

    query_embedding = (
        response.data[0].embedding
    )

    results = []

    for document in index:

        score = cosine_similarity(
            query_embedding,
            document["embedding"],
        )

        results.append(
            {
                "id": document["id"],
                "title": document["title"],
                "content": document["content"],
                "source": document["source"],
                "source_url": document[
                    "source_url"
                ],
                "score": score,
            }
        )

    results.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return results


def search_loyalty_knowledge(
    query: str,
    top_k: int = 3,
) -> list[dict]:
    """
    Retrieve the Top-K most relevant
    loyalty knowledge chunks.
    """

    results = rank_loyalty_knowledge(
        query=query,
    )

    # Semantic retrieval alone can miss Chinese aliases when the authoritative
    # chunk is English. Deterministic entity/intent matches are promoted, while
    # the remaining positions retain embedding rank order.
    normalized_query = query.casefold()
    required_ids = []
    for aliases, document_id in HYBRID_EVIDENCE_RULES:
        if any(alias.casefold() in normalized_query for alias in aliases):
            required_ids.append(document_id)

    by_id = {item["id"]: item for item in results}
    promoted = [by_id[document_id] for document_id in required_ids if document_id in by_id]
    promoted_ids = {item["id"] for item in promoted}
    return (promoted + [item for item in results if item["id"] not in promoted_ids])[:top_k]
