from datetime import datetime

import pytest
from pydantic import ValidationError

from tripflow_models import Location, TransportInput


def location(name: str, city: str, timezone: str = "Europe/Berlin") -> Location:
    return Location(name=name, city=city, timezone=timezone)


def test_transport_accepts_arbitrary_train_operator():
    item = TransportInput(
        mode="train",
        operator="Deutsche Bahn",
        service_number="ICE 105",
        origin=location("Berlin Hbf", "Berlin"),
        destination=location("Köln Hbf", "Cologne"),
        departure_at="2026-10-26T08:45:00+01:00",
        arrival_at="2026-10-26T13:12:00+01:00",
    )

    assert item.operator == "Deutsche Bahn"
    assert item.service_number == "ICE 105"


def test_location_rejects_unknown_timezone():
    with pytest.raises(ValidationError, match="IANA timezone"):
        location("Somewhere", "City", "Europe/Not-A-Real-Zone")


def test_transport_resolves_local_form_time_with_explicit_location_timezone():
    item = TransportInput(
        mode="train",
        operator="SNCF",
        origin=location("Paris Gare de Lyon", "Paris", "Europe/Paris"),
        destination=location("Lyon Part-Dieu", "Lyon", "Europe/Paris"),
        departure_at=datetime(2026, 10, 26, 8, 0),
        arrival_at=datetime(2026, 10, 26, 10, 0),
    )

    assert item.departure_at.utcoffset() is not None
    assert item.arrival_at.utcoffset() is not None


def test_transport_rejects_non_chronological_times():
    with pytest.raises(ValidationError, match="arrival_at must be later"):
        TransportInput(
            mode="flight",
            operator="Example Air",
            origin=location("London Heathrow", "London", "Europe/London"),
            destination=location("Paris CDG", "Paris", "Europe/Paris"),
            departure_at="2026-10-26T10:00:00+00:00",
            arrival_at="2026-10-26T09:00:00+01:00",
        )
