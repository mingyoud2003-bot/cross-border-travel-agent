import json
from datetime import date, timedelta

import httpx
import pytest

from flight_provider import (
    AeroDataBoxFlightProvider,
    ProviderCandidateError,
    normalize_flight_number,
)


def flight_payload(number: str = "LH 400") -> dict:
    return {
        "number": number,
        "status": "Expected",
        "codeshareStatus": "IsOperator",
        "airline": {"name": "Lufthansa", "iata": "LH", "icao": "DLH"},
        "departure": {
            "airport": {
                "iata": "FRA",
                "name": "Frankfurt-am-Main",
                "municipalityName": "Frankfurt",
                "timeZone": "Europe/Berlin",
            },
            "scheduledTime": {
                "utc": "2026-10-26 09:00Z",
                "local": "2026-10-26 10:00+01:00",
            },
            "terminal": "1",
            "gate": "Z50",
            "quality": ["Basic"],
        },
        "arrival": {
            "airport": {
                "iata": "JFK",
                "name": "New York John F Kennedy",
                "municipalityName": "New York",
                "timeZone": "America/New_York",
            },
            "scheduledTime": {
                "utc": "2026-10-26 17:30Z",
                "local": "2026-10-26 13:30-04:00",
            },
            "terminal": "1",
            "quality": ["Basic", "Live"],
        },
    }


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://provider.test/flights")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "provider error", request=request, response=response
            )


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_provider_maps_response_caches_and_preserves_server_provenance():
    session = FakeSession(FakeResponse(200, [flight_payload()]))
    provider = AeroDataBoxFlightProvider("test-key", session=session)

    first = provider.lookup("LH 400", date(2026, 10, 26), trip_id="trip_a")
    second = provider.lookup("lh400", date(2026, 10, 26), trip_id="trip_a")

    assert first.status == "found"
    assert first.cached is False
    assert second.cached is True
    assert len(session.calls) == 1
    candidate = first.candidates[0]
    assert candidate.origin.city == "法兰克福"
    assert candidate.origin.city_id == "DEFRA"
    assert candidate.destination.city == "纽约"
    assert candidate.destination.city_id == "USNYC"
    assert candidate.origin.timezone == "Europe/Berlin"
    assert candidate.destination.timezone == "America/New_York"
    assert candidate.data_quality == ["Basic", "Live"]
    confirmed = provider.confirmed_input(candidate.candidate_id, trip_id="trip_a")
    assert confirmed.source.source_type == "provider"
    assert confirmed.source.source_id == candidate.candidate_id
    assert confirmed.source.confirmed_by_user is True
    with pytest.raises(ProviderCandidateError):
        provider.confirmed_input(candidate.candidate_id, trip_id="trip_a")


def test_provider_candidate_is_bound_to_the_trip_that_requested_it():
    provider = AeroDataBoxFlightProvider(
        "test-key", session=FakeSession(FakeResponse(200, [flight_payload()]))
    )
    outcome = provider.lookup("LH400", date(2026, 10, 26), trip_id="trip_a")

    with pytest.raises(ProviderCandidateError):
        provider.confirmed_input(outcome.candidates[0].candidate_id, trip_id="trip_b")


def test_provider_degrades_without_configuration_or_on_quota_limit():
    missing = AeroDataBoxFlightProvider(None, session=FakeSession())
    limited = AeroDataBoxFlightProvider(
        "test-key", session=FakeSession(FakeResponse(429, {"message": "limit"}))
    )

    assert missing.lookup("LH400", date(2026, 10, 26), trip_id="t").status == "not_configured"
    assert limited.lookup("LH400", date(2026, 10, 26), trip_id="t").status == "quota_exceeded"


def test_provider_rejects_dates_outside_free_plan_window_without_a_request():
    session = FakeSession()
    provider = AeroDataBoxFlightProvider("test-key", session=session)

    result = provider.lookup("LH400", date.today() + timedelta(days=366), trip_id="t")

    assert result.status == "out_of_scope"
    assert session.calls == []


def test_provider_rejects_invalid_number_before_network_call():
    with pytest.raises(ValueError, match="flight number"):
        normalize_flight_number("../../secret")


def test_provider_discards_unexpected_or_incomplete_flights():
    provider = AeroDataBoxFlightProvider(
        "test-key",
        session=FakeSession(
            FakeResponse(200, [flight_payload("XX 999"), {"number": "LH 400"}])
        ),
    )

    result = provider.lookup("LH400", date(2026, 10, 26), trip_id="t")

    assert result.status == "not_found"
    assert result.candidates == []
