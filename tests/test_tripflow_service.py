import re

import pytest

from tripflow_models import (
    CreateTripInput,
    Location,
    SourceInput,
    StayInput,
    TransportInput,
)
from tripflow_service import TripFlowService, TripVersionConflictError


def transport(
    operator: str,
    service_number: str,
    origin_name: str,
    origin_city: str,
    destination_name: str,
    destination_city: str,
    departure_at: str,
    arrival_at: str,
) -> TransportInput:
    return TransportInput(
        mode="train",
        operator=operator,
        service_number=service_number,
        origin=Location(
            name=origin_name, city=origin_city, timezone="Europe/Berlin"
        ),
        destination=Location(
            name=destination_name,
            city=destination_city,
            timezone="Europe/Berlin",
        ),
        departure_at=departure_at,
        arrival_at=arrival_at,
        source=SourceInput(
            source_type="text",
            source_id="user-message-1",
            source_excerpt="ICE 105 Berlin to Cologne",
            confirmed_by_user=True,
        ),
    )


def test_service_persists_arbitrary_operator_and_field_provenance():
    service = TripFlowService()
    trip = service.create_trip(CreateTripInput(title="Germany"))

    updated = service.add_transport(
        trip.id,
        transport(
            "Deutsche Bahn",
            "ICE 105",
            "Berlin Hbf",
            "Berlin",
            "Köln Hbf",
            "Cologne",
            "2026-10-26T08:45:00+01:00",
            "2026-10-26T13:12:00+01:00",
        ),
        expected_version=1,
    )

    item = updated.reservations[0]
    assert item.operator == "Deutsche Bahn"
    assert item.provenance["operator"].source_id == "user-message-1"
    assert item.provenance["departure_at"].confirmed_by_user is True
    assert service.get_trip(trip.id).version == 2


def test_stale_trip_version_is_rejected():
    service = TripFlowService()
    trip = service.create_trip(CreateTripInput(title="Versioning"))
    payload = transport(
        "SNCF",
        "TGV 6603",
        "Paris Gare de Lyon",
        "Paris",
        "Lyon Part-Dieu",
        "Lyon",
        "2026-10-26T08:00:00+01:00",
        "2026-10-26T10:00:00+01:00",
    )
    service.add_transport(trip.id, payload, expected_version=1)

    with pytest.raises(TripVersionConflictError, match="current version is 2"):
        service.add_transport(trip.id, payload, expected_version=1)


def test_short_connection_is_detected_deterministically():
    service = TripFlowService()
    trip = service.create_trip(
        CreateTripInput(title="Connection", minimum_connection_minutes=90)
    )
    trip = service.add_transport(
        trip.id,
        transport(
            "DB",
            "ICE 1",
            "Berlin Hbf",
            "Berlin",
            "Frankfurt Hbf",
            "Frankfurt",
            "2026-10-26T08:00:00+01:00",
            "2026-10-26T11:00:00+01:00",
        ),
        expected_version=trip.version,
    )
    service.add_transport(
        trip.id,
        transport(
            "DB",
            "ICE 2",
            "Frankfurt Airport",
            "Frankfurt",
            "Paris Est",
            "Paris",
            "2026-10-26T11:45:00+01:00",
            "2026-10-26T15:30:00+01:00",
        ),
        expected_version=trip.version,
    )

    conflicts = service.conflicts(trip.id)

    assert len(conflicts) == 1
    assert conflicts[0].type == "short_connection"
    assert conflicts[0].evidence["buffer_minutes"] == 45


def test_short_connection_matches_chinese_and_english_city_aliases():
    service = TripFlowService()
    trip = service.create_trip(
        CreateTripInput(title="Bilingual connection", minimum_connection_minutes=90)
    )
    trip = service.add_transport(
        trip.id,
        transport(
            "SNCF", "TGV 1", "Paris Est", "Paris", "Frankfurt Hbf",
            "法兰克福", "2026-09-16T07:00:00+02:00",
            "2026-09-16T10:00:00+02:00",
        ),
        expected_version=trip.version,
    )
    service.add_transport(
        trip.id,
        transport(
            "Lufthansa", "LH400", "Frankfurt Airport", "Frankfurt",
            "JFK", "New York", "2026-09-16T10:55:00+02:00",
            "2026-09-16T13:35:00-04:00",
        ),
        expected_version=trip.version,
    )

    conflicts = service.conflicts(trip.id)

    assert len(conflicts) == 1
    assert conflicts[0].type == "short_connection"
    assert conflicts[0].evidence == {
        "connection_city": "法兰克福",
        "buffer_minutes": 55,
        "required_minutes": 90,
    }


def test_ics_export_has_stable_uids_and_utc_times():
    service = TripFlowService()
    trip = service.create_trip(CreateTripInput(title="Calendar"))
    trip = service.add_transport(
        trip.id,
        transport(
            "Deutsche Bahn",
            "ICE 105",
            "Berlin Hbf",
            "Berlin",
            "Köln Hbf",
            "Cologne",
            "2026-10-26T08:45:00+01:00",
            "2026-10-26T13:12:00+01:00",
        ),
        expected_version=trip.version,
    )

    first = service.export_ics(trip.id)
    second = service.export_ics(trip.id)

    assert first == second
    assert "SUMMARY:Deutsche Bahn ICE 105" in first
    assert "DTSTART:20261026T074500Z" in first
    assert re.search(r"UID:res_[0-9a-f]{32}@tripflow", first)


def test_confirmed_transport_can_be_revised_without_changing_identity():
    service = TripFlowService()
    trip = service.create_trip(CreateTripInput(title="Revision"))
    trip = service.add_transport(
        trip.id,
        transport(
            "DB", "ICE 1", "Berlin Hbf", "Berlin", "Köln Hbf", "Cologne",
            "2026-10-26T08:00:00+01:00", "2026-10-26T12:00:00+01:00",
        ),
        expected_version=trip.version,
    )
    original = trip.reservations[0]
    changed = transport(
        "DB", "ICE 1", "Berlin Hbf", "Berlin", "Köln Hbf", "Cologne",
        "2026-10-26T09:00:00+01:00", "2026-10-26T13:00:00+01:00",
    )

    trip = service.update_transport(
        trip.id, original.id, changed, expected_version=trip.version
    )

    revised = trip.reservations[0]
    assert revised.id == original.id
    assert revised.revision == 2
    assert revised.departure_at.hour == 9
    assert trip.version == 3


def test_stay_exports_as_all_day_event_and_can_be_deleted():
    service = TripFlowService()
    trip = service.create_trip(CreateTripInput(title="Stay"))
    trip = service.add_stay(
        trip.id,
        StayInput(
            property_name="Hotel AMANO",
            city="Berlin",
            check_in="2026-10-26",
            check_out="2026-10-28",
        ),
        expected_version=trip.version,
    )
    reservation_id = trip.reservations[0].id

    calendar = service.export_ics(trip.id)
    assert "DTSTART;VALUE=DATE:20261026" in calendar
    assert "DTEND;VALUE=DATE:20261028" in calendar

    trip = service.delete_reservation(
        trip.id, reservation_id, expected_version=trip.version
    )
    assert trip.reservations == []
    assert trip.version == 3


def test_conversation_state_persists_and_is_deleted_with_trip(tmp_path):
    db_path = tmp_path / "conversation.db"
    first = TripFlowService(db_path)
    trip = first.create_trip(CreateTripInput(title="Conversation"))
    first.save_conversation(
        trip.id,
        {"messages": [{"role": "user", "content": "查 LH400"}], "draft": {}},
    )
    first.close()

    restored = TripFlowService(db_path)
    assert restored.get_conversation(trip.id)["messages"][0]["content"] == "查 LH400"
    assert restored.delete_trip(trip.id) is True
    remaining = restored._connection.execute(
        "SELECT COUNT(*) FROM tripflow_conversations"
    ).fetchone()[0]
    assert remaining == 0
    restored.close()
