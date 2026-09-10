from unittest.mock import patch

from loyalty_service import (
    get_loyalty_evidence,
)


@patch(
    "loyalty_service.search_loyalty_knowledge"
)
@patch(
    "loyalty_service.check_loyalty_scope"
)
def test_out_of_scope_does_not_retrieve(
    mock_scope,
    mock_search,
):
    mock_scope.return_value = {
        "supported": False,
        "matched_anchors": [],
        "reason": (
            "当前常旅客知识库未覆盖该体系。"
        ),
    }

    result = get_loyalty_evidence(
        "Lufthansa Senator 有什么权益？"
    )

    assert (
        result["status"]
        == "out_of_scope"
    )

    assert result["evidence"] == []

    mock_search.assert_not_called()


@patch(
    "loyalty_service.search_loyalty_knowledge"
)
@patch(
    "loyalty_service.check_loyalty_scope"
)
def test_supported_query_retrieves_top_4(
    mock_scope,
    mock_search,
):
    mock_scope.return_value = {
        "supported": True,
        "matched_anchors": [
            "ba_silver",
            "qatar_airways",
        ],
        "reason": None,
    }

    mock_search.return_value = [
        {
            "id": "ba_silver_tier",
            "title": "BA Silver tier",
            "content": (
                "BA Silver corresponds "
                "to oneworld Sapphire."
            ),
            "source": "British Airways",
            "source_url": "https://example.com/1",
            "score": 0.5,
        }
    ]

    result = get_loyalty_evidence(
        (
            "我是 BA Silver，"
            "坐 Qatar Airways "
            "可以进休息室吗？"
        )
    )

    assert result["status"] == "success"

    assert len(result["evidence"]) == 1

    mock_search.assert_called_once_with(
        query=(
            "我是 BA Silver，"
            "坐 Qatar Airways "
            "可以进休息室吗？"
        ),
        top_k=4,
    )


@patch(
    "loyalty_service.search_loyalty_knowledge"
)
@patch(
    "loyalty_service.check_loyalty_scope"
)
def test_no_evidence_abstains(
    mock_scope,
    mock_search,
):
    mock_scope.return_value = {
        "supported": True,
        "matched_anchors": [
            "ba_silver",
        ],
        "reason": None,
    }

    mock_search.return_value = []

    result = get_loyalty_evidence(
        "BA Silver 的某项未知权益是什么？"
    )

    assert (
        result["status"]
        == "no_evidence"
    )

    assert result["evidence"] == []