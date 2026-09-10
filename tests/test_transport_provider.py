from unittest.mock import Mock, patch

import pytest
import requests

from transport_provider import (
    TransportProviderError,
    search_train_journeys,
)


def test_unsupported_destination():
    """
    Unsupported cities should fail before making
    any external HTTP request.
    """

    with pytest.raises(
        TransportProviderError,
        match="暂不支持目的城市：罗马",
    ):
        search_train_journeys(
            origin="伦敦",
            destination="罗马",
            travel_date="2026-09-10",
        )


@patch("transport_provider.requests.get")
def test_provider_timeout(mock_get):
    """
    Transitous timeout should be converted into
    a stable business-level error.
    """

    mock_get.side_effect = requests.Timeout()

    with pytest.raises(
        TransportProviderError,
        match="铁路查询服务响应超时",
    ):
        search_train_journeys(
            origin="伦敦",
            destination="巴黎",
            travel_date="2026-09-10",
        )


@patch("transport_provider.requests.get")
def test_provider_network_error(mock_get):
    """
    Generic HTTP/network errors should not leak
    raw requests exceptions to the Agent layer.
    """

    mock_get.side_effect = requests.RequestException()

    with pytest.raises(
        TransportProviderError,
        match="铁路查询服务暂时不可用",
    ):
        search_train_journeys(
            origin="伦敦",
            destination="巴黎",
            travel_date="2026-09-10",
        )


@patch("transport_provider.requests.get")
def test_no_results(mock_get):
    """
    Empty provider results should return a
    structured no_results response.
    """

    mock_response = Mock()

    mock_response.raise_for_status.return_value = None

    mock_response.json.return_value = {
        "itineraries": []
    }

    mock_get.return_value = mock_response

    result = search_train_journeys(
        origin="伦敦",
        destination="巴黎",
        travel_date="2026-09-10",
    )

    assert result["status"] == "no_results"
    assert result["origin"] == "伦敦"
    assert result["destination"] == "巴黎"
    assert result["date"] == "2026-09-10"
    assert result["journeys"] == []


@patch("transport_provider.requests.get")
def test_provider_invalid_json_is_stable_business_error(mock_get):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.side_effect = ValueError("invalid json")
    mock_get.return_value = mock_response

    with pytest.raises(
        TransportProviderError,
        match="返回了无效数据",
    ):
        search_train_journeys(
            origin="伦敦",
            destination="巴黎",
            travel_date="2030-01-01",
        )


@patch("transport_provider.requests.get")
def test_provider_non_object_json_is_rejected(mock_get):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = []
    mock_get.return_value = mock_response

    with pytest.raises(
        TransportProviderError,
        match="返回了无效数据",
    ):
        search_train_journeys(
            origin="伦敦",
            destination="巴黎",
            travel_date="2030-01-01",
        )
