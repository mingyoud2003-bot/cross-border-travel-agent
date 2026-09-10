from unittest.mock import patch

from loyalty_retriever import search_loyalty_knowledge


def ranked_fixture():
    ids = [
        "ba_silver_benefits",
        "ba_silver_tier",
        "sapphire_guest",
        "sapphire_lounge",
        "qatar_membership",
    ]
    return [{"id": item_id, "score": 1 - index / 10} for index, item_id in enumerate(ids)]


@patch("loyalty_retriever.rank_loyalty_knowledge")
def test_hybrid_retrieval_promotes_chinese_entity_and_lounge_evidence(mock_rank):
    mock_rank.return_value = ranked_fixture()

    results = search_loyalty_knowledge(
        "我是英航银卡，坐卡塔尔航空经济舱能进休息室吗？",
        top_k=4,
    )

    assert [item["id"] for item in results] == [
        "ba_silver_tier",
        "qatar_membership",
        "sapphire_lounge",
        "ba_silver_benefits",
    ]


@patch("loyalty_retriever.rank_loyalty_knowledge")
def test_hybrid_retrieval_does_not_add_unasked_guest_evidence(mock_rank):
    mock_rank.return_value = ranked_fixture()

    results = search_loyalty_knowledge("oneworld Sapphire 有什么休息室权益？", top_k=2)

    assert "sapphire_guest" not in [item["id"] for item in results]
