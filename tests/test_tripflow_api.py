from fastapi import FastAPI
from fastapi.testclient import TestClient

from tripflow_agent import (
    ItineraryProposal,
    LocationCandidate,
    TransportCandidate,
    normalize_proposal,
    ProposalUnavailableError,
)
from tripflow_api import build_tripflow_router
from tripflow_service import TripFlowService


class FakeProposalService:
    async def propose(self, text: str) -> ItineraryProposal:
        assert "ICE 105" in text
        return ItineraryProposal(
            transports=[
                TransportCandidate(
                    mode="train",
                    operator="Deutsche Bahn",
                    service_number="ICE 105",
                    origin=LocationCandidate(name="Berlin Hbf", city="Berlin"),
                    destination=LocationCandidate(name="Köln Hbf", city="Cologne"),
                    departure_at="2026-10-26T08:45:00+01:00",
                    arrival_at="2026-10-26T13:12:00+01:00",
                    missing_fields=[],
                    source_excerpt="ICE 105 Berlin Hbf 08:45 to Köln Hbf 13:12",
                )
            ]
        )


class UnavailableProposalService:
    async def propose(self, text: str) -> ItineraryProposal:
        raise ProposalUnavailableError("itinerary extraction is temporarily unavailable")


def client() -> TestClient:
    app = FastAPI()
    app.include_router(
        build_tripflow_router(TripFlowService(), FakeProposalService())
    )
    return TestClient(app)


def unavailable_client() -> TestClient:
    app = FastAPI()
    app.include_router(
        build_tripflow_router(TripFlowService(), UnavailableProposalService())
    )
    return TestClient(app)


def train_payload(operator: str = "Deutsche Bahn") -> dict:
    return {
        "mode": "train",
        "operator": operator,
        "service_number": "ICE 105",
        "origin": {
            "name": "Berlin Hbf",
            "city": "Berlin",
            "timezone": "Europe/Berlin",
        },
        "destination": {
            "name": "Köln Hbf",
            "city": "Cologne",
            "timezone": "Europe/Berlin",
        },
        "departure_at": "2026-10-26T08:45:00+01:00",
        "arrival_at": "2026-10-26T13:12:00+01:00",
        "source": {
            "source_type": "form",
            "source_excerpt": "user-confirmed form",
            "confirmed_by_user": True,
        },
    }


def test_trip_crud_and_arbitrary_operator_http_contract():
    api = client()
    created = api.post("/api/trips", json={"title": "Europe 2026"})
    trip_id = created.json()["id"]

    updated = api.post(
        f"/api/trips/{trip_id}/transport",
        headers={"If-Match": "1"},
        json=train_payload("Deutsche Bahn"),
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["reservations"][0]["operator"] == "Deutsche Bahn"
    assert api.get(f"/api/trips/{trip_id}").status_code == 200


def test_global_trip_listing_is_not_publicly_exposed():
    api = client()
    api.post("/api/trips", json={"title": "Private by default"})

    response = api.get("/api/trips")

    assert response.status_code == 405


def test_stale_http_write_returns_conflict():
    api = client()
    trip_id = api.post("/api/trips", json={"title": "Europe"}).json()["id"]
    assert api.post(
        f"/api/trips/{trip_id}/transport",
        headers={"If-Match": "1"},
        json=train_payload(),
    ).status_code == 200

    stale = api.post(
        f"/api/trips/{trip_id}/transport",
        headers={"If-Match": "1"},
        json=train_payload("SNCF"),
    )

    assert stale.status_code == 409
    assert "current version is 2" in stale.json()["detail"]


def test_text_proposal_never_mutates_confirmed_trip():
    api = client()
    created = api.post("/api/trips", json={"title": "Germany"}).json()

    proposed = api.post(
        f"/api/trips/{created['id']}/proposals/text",
        json={
            "text": "ICE 105 Berlin Hbf 08:45 to Köln Hbf 13:12 on 2026-10-26"
        },
    )
    unchanged = api.get(f"/api/trips/{created['id']}").json()

    assert proposed.status_code == 200
    assert proposed.json()["requires_confirmation"] is True
    assert proposed.json()["transports"][0]["operator"] == "Deutsche Bahn"
    assert unchanged["version"] == 1
    assert unchanged["reservations"] == []


def test_proposal_provider_failure_is_a_retryable_503():
    api = unavailable_client()
    trip_id = api.post("/api/trips", json={"title": "Failure"}).json()["id"]

    response = api.post(
        f"/api/trips/{trip_id}/proposals/text", json={"text": "some booking"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "itinerary extraction is temporarily unavailable"


def test_ics_endpoint_returns_calendar_content_type():
    api = client()
    trip_id = api.post("/api/trips", json={"title": "Calendar"}).json()["id"]

    response = api.get(f"/api/trips/{trip_id}/calendar.ics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.text.startswith("BEGIN:VCALENDAR")


def test_proposal_normalizer_recomputes_missing_timezones():
    proposal = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="DB",
                origin=LocationCandidate(name="Berlin Hbf", city="Berlin"),
                destination=LocationCandidate(name="Köln Hbf", city="Cologne"),
                departure_at="2026-10-26T08:45",
                arrival_at="2026-10-26T13:12",
                missing_fields=[],
                source_excerpt="DB Berlin Hbf to Köln Hbf",
            )
        ]
    )

    normalized = normalize_proposal("DB Berlin Hbf to Köln Hbf", proposal)

    assert normalized.transports[0].missing_fields == [
        "destination.timezone",
        "origin.timezone",
    ]


def test_proposal_normalizer_removes_inferred_operator_and_partial_datetimes():
    text = "flight MU5100 from Beijing Capital at 07:00 to Shanghai at 09:15"
    proposal = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="flight",
                operator="MU",
                service_number="MU5100",
                origin=LocationCandidate(
                    name="Beijing Capital", city="Beijing", timezone="Asia/Shanghai"
                ),
                destination=LocationCandidate(
                    name="Shanghai", city="Shanghai", timezone="Asia/Shanghai"
                ),
                departure_at="07:00",
                arrival_at="09:15",
                source_excerpt=text,
            )
        ]
    )

    item = normalize_proposal(text, proposal).transports[0]

    assert item.operator is None
    assert item.origin.timezone is None
    assert item.destination.timezone is None
    assert item.departure_at is None
    assert item.arrival_at is None
    assert item.missing_fields == [
        "arrival_at",
        "departure_at",
        "destination.timezone",
        "operator",
        "origin.timezone",
    ]


def test_proposal_normalizer_rejects_explicitly_unbooked_journey():
    text = "I considered SNCF TGV 6201 but decided not to book it."
    proposal = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="SNCF",
                service_number="TGV 6201",
                origin=LocationCandidate(name="Paris", city="Paris"),
                destination=LocationCandidate(name="Avignon", city="Avignon"),
                source_excerpt="SNCF TGV 6201",
            )
        ]
    )

    normalized = normalize_proposal(text, proposal)

    assert normalized.transports == []
    assert normalized.stays == []
    assert normalized.clarification_questions == []
    assert "不是要加入" in normalized.warnings[-1]


def test_update_and_delete_reservation_http_contract():
    api = client()
    trip = api.post("/api/trips", json={"title": "Editable"}).json()
    trip = api.post(
        f"/api/trips/{trip['id']}/transport",
        headers={"If-Match": str(trip["version"])},
        json=train_payload(),
    ).json()
    reservation_id = trip["reservations"][0]["id"]
    changed = train_payload()
    changed["departure_at"] = "2026-10-26T09:45:00+01:00"
    changed["arrival_at"] = "2026-10-26T14:12:00+01:00"

    revised = api.put(
        f"/api/trips/{trip['id']}/transport/{reservation_id}",
        headers={"If-Match": str(trip["version"])},
        json=changed,
    )

    assert revised.status_code == 200
    assert revised.json()["reservations"][0]["revision"] == 2
    deleted = api.delete(
        f"/api/trips/{trip['id']}/reservations/{reservation_id}",
        headers={"If-Match": str(revised.json()["version"])},
    )
    assert deleted.status_code == 200
    assert deleted.json()["reservations"] == []
