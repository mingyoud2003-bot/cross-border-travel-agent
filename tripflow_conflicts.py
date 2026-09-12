from __future__ import annotations

from hashlib import sha256

from tripflow_models import Conflict, TransportReservation, Trip


def detect_conflicts(trip: Trip) -> list[Conflict]:
    """Return deterministic conflicts for confirmed, non-cancelled transport."""

    transports = sorted(
        (
            item
            for item in trip.reservations
            if isinstance(item, TransportReservation) and item.status == "confirmed"
        ),
        key=lambda item: item.departure_at,
    )
    conflicts: list[Conflict] = []
    for index, current in enumerate(transports):
        for other in transports[index + 1 :]:
            if other.departure_at >= current.arrival_at:
                break
            conflicts.append(
                _conflict(
                    "schedule_overlap",
                    "blocking",
                    current.id,
                    other.id,
                    "两个交通行程在时间上重叠。",
                    {
                        "first_arrival": current.arrival_at.isoformat(),
                        "second_departure": other.departure_at.isoformat(),
                    },
                )
            )

    for current, following in zip(transports, transports[1:]):
        if following.departure_at < current.arrival_at:
            continue
        if _normalize_city(current.destination.city) != _normalize_city(
            following.origin.city
        ):
            continue
        buffer_minutes = int(
            (following.departure_at - current.arrival_at).total_seconds() // 60
        )
        if buffer_minutes >= trip.minimum_connection_minutes:
            continue
        conflicts.append(
            _conflict(
                "short_connection",
                "warning",
                current.id,
                following.id,
                (
                    f"两段交通在{current.destination.city}的衔接仅"
                    f"{buffer_minutes}分钟，低于设置的"
                    f"{trip.minimum_connection_minutes}分钟。"
                ),
                {
                    "connection_city": current.destination.city,
                    "buffer_minutes": buffer_minutes,
                    "required_minutes": trip.minimum_connection_minutes,
                },
            )
        )
    return sorted(conflicts, key=lambda item: (item.severity, item.id))


def _normalize_city(value: str) -> str:
    return "".join(value.casefold().split())


def _conflict(
    conflict_type: str,
    severity: str,
    first_id: str,
    second_id: str,
    message: str,
    evidence: dict[str, str | int],
) -> Conflict:
    digest = sha256(
        f"{conflict_type}:{first_id}:{second_id}".encode("utf-8")
    ).hexdigest()[:16]
    return Conflict(
        id=f"conf_{digest}",
        type=conflict_type,
        severity=severity,
        reservation_ids=[first_id, second_id],
        message=message,
        evidence=evidence,
    )

