from datetime import UTC, date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flight_provider import (
    FlightLookupOutcome,
    ProviderCandidateError,
    ProviderFlightCandidate,
)
from tripflow_agent import (
    FlightLookupRequest,
    ItineraryProposal,
    LocationCandidate,
    StayCandidate,
    TransportCandidate,
    merge_conversation_draft,
    normalize_proposal,
    ProposalUnavailableError,
)
from tripflow_api import build_tripflow_router
from tripflow_models import Location, SourceInput, TransportInput
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


class FlightLookupProposalService:
    async def propose(self, text: str) -> ItineraryProposal:
        return ItineraryProposal(
            flight_lookups=[
                FlightLookupRequest(
                    flight_number="LH400",
                    departure_date=date(2026, 10, 26),
                    source_excerpt=text,
                )
            ]
        )


class FakeFlightProvider:
    candidate_id = "pfc_" + "f" * 32

    def lookup(self, flight_number, departure_date, *, trip_id):
        return FlightLookupOutcome(
            flight_number=flight_number,
            departure_date=departure_date,
            status="found",
            message="found",
            candidates=[
                ProviderFlightCandidate(
                    candidate_id=self.candidate_id,
                    flight_number="LH 400",
                    operator="Lufthansa",
                    flight_status="Expected",
                    origin=Location(
                        name="Frankfurt (FRA)", city="Frankfurt", timezone="Europe/Berlin"
                    ),
                    destination=Location(
                        name="John F Kennedy (JFK)", city="New York", timezone="America/New_York"
                    ),
                    departure_at="2026-10-26T10:00:00+01:00",
                    arrival_at="2026-10-26T13:30:00-04:00",
                    checked_at=datetime(2026, 10, 25, tzinfo=UTC),
                )
            ],
        )

    def confirmed_input(self, candidate_id, *, trip_id):
        if candidate_id != self.candidate_id:
            raise ProviderCandidateError(candidate_id)
        return TransportInput(
            mode="flight",
            operator="Lufthansa",
            service_number="LH 400",
            origin=Location(
                name="Frankfurt (FRA)", city="Frankfurt", timezone="Europe/Berlin"
            ),
            destination=Location(
                name="John F Kennedy (JFK)", city="New York", timezone="America/New_York"
            ),
            departure_at="2026-10-26T10:00:00+01:00",
            arrival_at="2026-10-26T13:30:00-04:00",
            source=SourceInput(
                source_type="provider",
                source_id=candidate_id,
                source_excerpt="AeroDataBox LH400",
                confirmed_by_user=True,
            ),
        )


class CrashingFlightProvider(FakeFlightProvider):
    def lookup(self, flight_number, departure_date, *, trip_id):
        raise RuntimeError("unexpected provider failure")


class FakeConversationService:
    async def respond(self, messages, draft):
        user_messages = [item.content for item in messages if item.role == "user"]
        if len(user_messages) == 1:
            return (
                "请补充日期",
                ItineraryProposal(
                    flight_lookups=[
                        FlightLookupRequest(
                            flight_number="LH400",
                            missing_fields=["departure_date"],
                            source_excerpt="LH400",
                        )
                    ]
                ),
            )
        return (
            "可以确认",
            ItineraryProposal(
                flight_lookups=[
                    FlightLookupRequest(
                        flight_number="LH400",
                        departure_date=date(2026, 10, 26),
                        source_excerpt="LH400",
                    )
                ]
            ),
        )


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


def flight_lookup_client() -> TestClient:
    app = FastAPI()
    app.include_router(
        build_tripflow_router(
            TripFlowService(), FlightLookupProposalService(), FakeFlightProvider()
        )
    )
    return TestClient(app)


def crashing_flight_lookup_client() -> TestClient:
    app = FastAPI()
    app.include_router(
        build_tripflow_router(
            TripFlowService(), FlightLookupProposalService(), CrashingFlightProvider()
        )
    )
    return TestClient(app)


def conversation_client() -> TestClient:
    app = FastAPI()
    app.include_router(
        build_tripflow_router(
            TripFlowService(),
            FlightLookupProposalService(),
            FakeFlightProvider(),
            FakeConversationService(),
        )
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


def test_agent_lookup_returns_provider_candidate_without_mutating_trip():
    api = flight_lookup_client()
    trip = api.post("/api/trips", json={"title": "Flight check"}).json()

    response = api.post(
        f"/api/trips/{trip['id']}/proposals/text",
        json={"text": "请查询 2026-10-26 的 LH400 航班状态"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider_lookups"][0]["status"] == "found"
    assert body["provider_lookups"][0]["candidates"][0]["operator"] == "Lufthansa"
    assert api.get(f"/api/trips/{trip['id']}").json()["reservations"] == []


def test_unexpected_provider_failure_degrades_without_losing_agent_proposal():
    api = crashing_flight_lookup_client()
    trip = api.post("/api/trips", json={"title": "Provider failure"}).json()

    response = api.post(
        f"/api/trips/{trip['id']}/proposals/text",
        json={"text": "请查询 2026-10-26 的 LH400 航班状态"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["flight_lookups"][0]["flight_number"] == "LH400"
    assert body["provider_lookups"][0]["status"] == "unavailable"
    assert "未经核验" in body["provider_lookups"][0]["message"]


def test_conversation_persists_missing_state_then_opens_confirmation_candidate():
    api = conversation_client()
    trip = api.post("/api/trips", json={"title": "Conversational"}).json()

    first = api.post(
        f"/api/trips/{trip['id']}/conversation",
        json={"message": "帮我查 LH400"},
    )
    second = api.post(
        f"/api/trips/{trip['id']}/conversation",
        json={"message": "2026-10-26"},
    )
    restored = api.get(f"/api/trips/{trip['id']}/conversation")

    assert first.status_code == 200
    assert first.json()["ready_for_confirmation"] is False
    assert "明确出发日期" in first.json()["messages"][-1]["content"]
    assert first.json()["draft"]["provider_lookups"] == []
    assert second.status_code == 200
    assert second.json()["ready_for_confirmation"] is True
    assert second.json()["draft"]["provider_lookups"][0]["status"] == "found"
    assert len(restored.json()["messages"]) == 4


def test_conversation_reset_clears_draft_and_messages():
    api = conversation_client()
    trip = api.post("/api/trips", json={"title": "Reset"}).json()
    api.post(
        f"/api/trips/{trip['id']}/conversation",
        json={"message": "帮我查 LH400"},
    )

    reset = api.delete(f"/api/trips/{trip['id']}/conversation")

    assert reset.status_code == 200
    assert reset.json()["messages"] == []
    assert reset.json()["draft"]["flight_lookups"] == []


def test_provider_candidate_requires_confirmation_and_gets_trusted_provenance():
    api = flight_lookup_client()
    trip = api.post("/api/trips", json={"title": "Flight check"}).json()
    lookup = api.post(
        f"/api/trips/{trip['id']}/proposals/text",
        json={"text": "请查询 2026-10-26 的 LH400 航班状态"},
    ).json()
    candidate_id = lookup["provider_lookups"][0]["candidates"][0]["candidate_id"]

    confirmed = api.post(
        f"/api/trips/{trip['id']}/providers/flights/confirm",
        headers={"If-Match": "1"},
        json={"candidate_id": candidate_id},
    )

    assert confirmed.status_code == 200
    reservation = confirmed.json()["reservations"][0]
    assert reservation["service_number"] == "LH 400"
    assert reservation["provenance"]["departure_at"]["source_type"] == "provider"
    assert reservation["provenance"]["departure_at"]["confirmed_by_user"] is True


def test_client_cannot_spoof_provider_provenance_on_normal_transport_endpoint():
    api = client()
    trip = api.post("/api/trips", json={"title": "No spoof"}).json()
    payload = train_payload()
    payload["source"]["source_type"] = "provider"

    response = api.post(
        f"/api/trips/{trip['id']}/transport",
        headers={"If-Match": "1"},
        json=payload,
    )

    assert response.status_code == 422
    assert "Provider" in response.json()["detail"]


def test_expired_provider_candidate_requires_a_fresh_lookup():
    api = flight_lookup_client()
    trip = api.post("/api/trips", json={"title": "Expired"}).json()

    response = api.post(
        f"/api/trips/{trip['id']}/providers/flights/confirm",
        headers={"If-Match": "1"},
        json={"candidate_id": "pfc_" + "0" * 32},
    )

    assert response.status_code == 410
    assert "重新查询" in response.json()["detail"]


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


def test_proposal_normalizer_canonicalizes_grounded_bilingual_city():
    text = "DB 从柏林到 Köln Hbf"
    proposal = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="DB",
                origin=LocationCandidate(name="柏林", city="Berlin"),
                destination=LocationCandidate(name="Köln Hbf", city="Cologne"),
                source_excerpt=text,
            )
        ]
    )

    item = normalize_proposal(text, proposal).transports[0]

    assert item.origin.city == "柏林"
    assert item.destination.city == "科隆"


def test_proposal_normalizer_removes_city_and_station_not_in_user_evidence():
    text = "DB 从 Berlin 到 Cologne"
    proposal = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="DB",
                origin=LocationCandidate(name="Berlin Hbf", city="Berlin"),
                destination=LocationCandidate(name="Atlantis Central", city="Atlantis"),
                source_excerpt=text,
            )
        ]
    )

    item = normalize_proposal(text, proposal).transports[0]

    assert item.origin.city == "柏林"
    assert item.origin.name is None
    assert item.destination.city is None
    assert item.destination.name is None
    assert "destination.city" in item.missing_fields


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


def test_flight_lookup_normalizer_requires_grounded_number_date_and_intent():
    proposal = ItineraryProposal(
        flight_lookups=[
            FlightLookupRequest(
                flight_number="LH400",
                departure_date=date(2026, 10, 26),
                source_excerpt="查一下 2026年10月26日 LH400",
            )
        ]
    )

    grounded = normalize_proposal("查一下 2026年10月26日 LH400", proposal)
    no_intent = normalize_proposal("2026年10月26日 LH400", proposal)
    hallucinated = normalize_proposal("查一下 2026年10月26日 LH401", proposal)

    assert grounded.flight_lookups[0].flight_number == "LH400"
    assert grounded.flight_lookups[0].departure_date == date(2026, 10, 26)
    assert grounded.flight_lookups[0].missing_fields == []
    assert no_intent.flight_lookups == []
    assert hallucinated.flight_lookups == []


def test_conversation_draft_deterministically_combines_flight_number_and_date():
    previous = ItineraryProposal(
        flight_lookups=[
            FlightLookupRequest(
                flight_number="LH400",
                missing_fields=["departure_date"],
                source_excerpt="LH400",
            )
        ]
    )

    merged = merge_conversation_draft(
        "帮我查 LH400\n出发日期是 2026-09-12",
        "出发日期是 2026-09-12",
        previous,
        ItineraryProposal(),
    )

    assert merged.flight_lookups[0].flight_number == "LH400"
    assert merged.flight_lookups[0].departure_date == date(2026, 9, 12)
    assert merged.flight_lookups[0].missing_fields == []


def test_explicit_stay_switch_does_not_carry_old_transport_draft():
    previous = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="DB",
                origin=LocationCandidate(name="Berlin", city="Berlin"),
                destination=LocationCandidate(name="Cologne", city="Cologne"),
                source_excerpt="DB Berlin Cologne",
            )
        ]
    )
    current = ItineraryProposal(
        stays=[
            StayCandidate(
                property_name="Hotel AMANO",
                city="Berlin",
                source_excerpt="改成添加 Berlin 的 Hotel AMANO 住宿",
            )
        ]
    )

    merged = merge_conversation_draft(
        "DB Berlin Cologne\n改成添加 Berlin 的 Hotel AMANO 住宿",
        "改成添加 Berlin 的 Hotel AMANO 住宿",
        previous,
        current,
    )

    assert merged.transports == []
    assert len(merged.stays) == 1


def test_explicit_same_domain_new_task_does_not_reuse_old_route():
    previous = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="DB",
                origin=LocationCandidate(name="Berlin", city="Berlin"),
                destination=LocationCandidate(name="Cologne", city="Cologne"),
                source_excerpt="DB Berlin Cologne",
            )
        ]
    )
    current = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="SNCF",
                origin=LocationCandidate(name="Paris", city="Paris"),
                destination=LocationCandidate(),
                source_excerpt="换一个新任务：SNCF 从 Paris 出发",
            )
        ]
    )

    merged = merge_conversation_draft(
        "DB Berlin Cologne\n换一个新任务：SNCF 从 Paris 出发",
        "换一个新任务：SNCF 从 Paris 出发",
        previous,
        current,
    )

    assert merged.transports[0].origin.city == "巴黎"
    assert merged.transports[0].destination.city is None


def test_conversation_derives_known_city_timezones_without_asking_user_for_iana():
    current = ItineraryProposal(
        transports=[
            TransportCandidate(
                mode="train",
                operator="DB",
                service_number="ICE 105",
                origin=LocationCandidate(name="Berlin", city="Berlin"),
                destination=LocationCandidate(name="Cologne", city="Cologne"),
                departure_at="2026-10-26T08:00:00",
                arrival_at="2026-10-26T12:00:00",
                source_excerpt="DB ICE 105 Berlin to Cologne",
            )
        ]
    )

    merged = merge_conversation_draft(
        "DB ICE 105 Berlin to Cologne 2026-10-26 08:00 12:00",
        "DB ICE 105 Berlin to Cologne 2026-10-26 08:00 12:00",
        ItineraryProposal(),
        current,
    )

    item = merged.transports[0]
    assert item.origin.timezone == "Europe/Berlin"
    assert item.destination.timezone == "Europe/Berlin"
    assert "origin.timezone" not in item.missing_fields
    assert "应用根据城市映射" in merged.warnings[-1]


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
