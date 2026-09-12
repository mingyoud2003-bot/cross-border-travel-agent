from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from tripflow_conflicts import detect_conflicts
from tripflow_models import (
    Conflict,
    CreateTripInput,
    StayInput,
    StayReservation,
    TransportInput,
    TransportReservation,
    Trip,
    provenance_for_fields,
)


class TripNotFoundError(KeyError):
    pass


class TripVersionConflictError(RuntimeError):
    pass


class TripFlowService:
    """SQLite repository and deterministic domain service for TripFlow."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tripflow_trips (
                trip_id TEXT PRIMARY KEY,
                trip_json TEXT NOT NULL,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_trip(self, payload: CreateTripInput) -> Trip:
        trip = Trip(**payload.model_dump())
        self._insert(trip)
        return trip

    def list_trips(self) -> list[Trip]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT trip_json FROM tripflow_trips ORDER BY updated_at DESC"
            ).fetchall()
        return [Trip.model_validate_json(row[0]) for row in rows]

    def get_trip(self, trip_id: str) -> Trip:
        with self._lock:
            row = self._connection.execute(
                "SELECT trip_json FROM tripflow_trips WHERE trip_id = ?", (trip_id,)
            ).fetchone()
        if row is None:
            raise TripNotFoundError(trip_id)
        return Trip.model_validate_json(row[0])

    def delete_trip(self, trip_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM tripflow_trips WHERE trip_id = ?", (trip_id,)
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def add_transport(
        self,
        trip_id: str,
        payload: TransportInput,
        *,
        expected_version: int,
    ) -> Trip:
        provenance = provenance_for_fields(
            [
                "mode",
                "operator",
                "service_number",
                "origin.name",
                "origin.city",
                "origin.timezone",
                "destination.name",
                "destination.city",
                "destination.timezone",
                "departure_at",
                "arrival_at",
                "status",
            ],
            payload.source,
        )
        reservation = TransportReservation(
            **payload.model_dump(exclude={"source"}),
            provenance=provenance,
        )
        return self._append_reservation(
            trip_id, reservation, expected_version=expected_version
        )

    def add_stay(
        self,
        trip_id: str,
        payload: StayInput,
        *,
        expected_version: int,
    ) -> Trip:
        provenance = provenance_for_fields(
            ["property_name", "city", "address", "check_in", "check_out", "status"],
            payload.source,
        )
        reservation = StayReservation(
            **payload.model_dump(exclude={"source"}),
            provenance=provenance,
        )
        return self._append_reservation(
            trip_id, reservation, expected_version=expected_version
        )

    def update_transport(
        self,
        trip_id: str,
        reservation_id: str,
        payload: TransportInput,
        *,
        expected_version: int,
    ) -> Trip:
        current = self._reservation(trip_id, reservation_id)
        if not isinstance(current, TransportReservation):
            raise ValueError("reservation is not a transport")
        replacement = TransportReservation(
            **payload.model_dump(exclude={"source"}),
            id=current.id,
            revision=current.revision + 1,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
            provenance=provenance_for_fields(
                [
                    "mode", "operator", "service_number", "origin.name",
                    "origin.city", "origin.timezone", "destination.name",
                    "destination.city", "destination.timezone", "departure_at",
                    "arrival_at", "status",
                ],
                payload.source,
            ),
        )
        return self._replace_reservation(
            trip_id, replacement, expected_version=expected_version
        )

    def update_stay(
        self,
        trip_id: str,
        reservation_id: str,
        payload: StayInput,
        *,
        expected_version: int,
    ) -> Trip:
        current = self._reservation(trip_id, reservation_id)
        if not isinstance(current, StayReservation):
            raise ValueError("reservation is not a stay")
        replacement = StayReservation(
            **payload.model_dump(exclude={"source"}),
            id=current.id,
            revision=current.revision + 1,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
            provenance=provenance_for_fields(
                ["property_name", "city", "address", "check_in", "check_out", "status"],
                payload.source,
            ),
        )
        return self._replace_reservation(
            trip_id, replacement, expected_version=expected_version
        )

    def delete_reservation(
        self, trip_id: str, reservation_id: str, *, expected_version: int
    ) -> Trip:
        trip = self.get_trip(trip_id)
        remaining = [item for item in trip.reservations if item.id != reservation_id]
        if len(remaining) == len(trip.reservations):
            raise TripNotFoundError(reservation_id)
        trip.reservations = remaining
        return self._save_changed_trip(trip, expected_version=expected_version)

    def conflicts(self, trip_id: str) -> list[Conflict]:
        return detect_conflicts(self.get_trip(trip_id))

    def export_ics(self, trip_id: str) -> str:
        trip = self.get_trip(trip_id)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//TripFlow//Itinerary//EN",
            "CALSCALE:GREGORIAN",
        ]
        for item in trip.reservations:
            if item.status != "confirmed":
                continue
            if isinstance(item, TransportReservation):
                summary = f"{item.operator} {item.service_number or item.mode}"
                description = f"{item.origin.name} -> {item.destination.name}"
                start = _ics_datetime(item.departure_at)
                end = _ics_datetime(item.arrival_at)
            else:
                summary = f"Stay: {item.property_name}"
                description = item.address or item.city
                start = item.check_in.strftime("%Y%m%d")
                end = item.check_out.strftime("%Y%m%d")
            start_line = (
                f"DTSTART:{start}"
                if isinstance(item, TransportReservation)
                else f"DTSTART;VALUE=DATE:{start}"
            )
            end_line = (
                f"DTEND:{end}"
                if isinstance(item, TransportReservation)
                else f"DTEND;VALUE=DATE:{end}"
            )
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{item.id}@tripflow",
                    f"DTSTAMP:{_ics_datetime(trip.updated_at)}",
                    start_line,
                    end_line,
                    f"SUMMARY:{_ics_escape(summary)}",
                    f"DESCRIPTION:{_ics_escape(description)}",
                    "END:VEVENT",
                ]
            )
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"

    def _append_reservation(
        self,
        trip_id: str,
        reservation: TransportReservation | StayReservation,
        *,
        expected_version: int,
    ) -> Trip:
        trip = self.get_trip(trip_id)
        trip.reservations.append(reservation)
        return self._save_changed_trip(trip, expected_version=expected_version)

    def _reservation(
        self, trip_id: str, reservation_id: str
    ) -> TransportReservation | StayReservation:
        trip = self.get_trip(trip_id)
        for item in trip.reservations:
            if item.id == reservation_id:
                return item
        raise TripNotFoundError(reservation_id)

    def _replace_reservation(
        self,
        trip_id: str,
        replacement: TransportReservation | StayReservation,
        *,
        expected_version: int,
    ) -> Trip:
        trip = self.get_trip(trip_id)
        for index, item in enumerate(trip.reservations):
            if item.id == replacement.id:
                trip.reservations[index] = replacement
                return self._save_changed_trip(
                    trip, expected_version=expected_version
                )
        raise TripNotFoundError(replacement.id)

    def _save_changed_trip(self, trip: Trip, *, expected_version: int) -> Trip:
        with self._lock:
            if trip.version != expected_version:
                raise TripVersionConflictError(
                    f"expected trip version {expected_version}, current version is {trip.version}"
                )
            trip.version += 1
            trip.updated_at = datetime.now(UTC)
            cursor = self._connection.execute(
                """
                UPDATE tripflow_trips
                SET trip_json = ?, version = ?, updated_at = ?
                WHERE trip_id = ? AND version = ?
                """,
                (
                    trip.model_dump_json(), trip.version,
                    trip.updated_at.isoformat(), trip.id, expected_version,
                ),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                raise TripVersionConflictError("trip changed during update")
            self._connection.commit()
        return trip

    def _insert(self, trip: Trip) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO tripflow_trips VALUES (?, ?, ?, ?)",
                (
                    trip.id,
                    trip.model_dump_json(),
                    trip.version,
                    trip.updated_at.isoformat(),
                ),
            )
            self._connection.commit()


def _ics_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
