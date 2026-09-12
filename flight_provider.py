from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from tripflow_models import Location, SourceInput, TransportInput


AERODATABOX_ATTRIBUTION_URL = "https://aerodatabox.com/"
_FLIGHT_NUMBER = re.compile(r"^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$")


class ProviderFlightCandidate(BaseModel):
    candidate_id: str
    provider: Literal["aerodatabox"] = "aerodatabox"
    attribution_url: str = AERODATABOX_ATTRIBUTION_URL
    flight_number: str
    operator: str
    flight_status: str
    origin: Location
    destination: Location
    departure_at: datetime
    arrival_at: datetime
    departure_terminal: str | None = None
    departure_gate: str | None = None
    arrival_terminal: str | None = None
    arrival_gate: str | None = None
    data_quality: list[str] = Field(default_factory=list)
    checked_at: datetime


class FlightLookupOutcome(BaseModel):
    provider: Literal["aerodatabox"] = "aerodatabox"
    attribution_url: str = AERODATABOX_ATTRIBUTION_URL
    flight_number: str
    departure_date: date
    status: Literal[
        "found", "not_found", "not_configured", "out_of_scope",
        "quota_exceeded", "unavailable"
    ]
    message: str
    cached: bool = False
    candidates: list[ProviderFlightCandidate] = Field(default_factory=list)


class ProviderCandidateError(LookupError):
    pass


class FlightProvider(Protocol):
    def lookup(
        self, flight_number: str, departure_date: date, *, trip_id: str
    ) -> FlightLookupOutcome: ...

    def confirmed_input(self, candidate_id: str, *, trip_id: str) -> TransportInput: ...


@dataclass
class _StoredCandidate:
    trip_id: str
    payload: TransportInput
    expires_at: float


class AeroDataBoxFlightProvider:
    """Small, bounded AeroDataBox adapter for user-triggered flight verification."""

    base_url = "https://aerodatabox.p.rapidapi.com"
    host = "aerodatabox.p.rapidapi.com"

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout_seconds: float = 8.0,
        cache_ttl_seconds: int = 600,
        candidate_ttl_seconds: int = 900,
        session: httpx.Client | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.candidate_ttl_seconds = candidate_ttl_seconds
        self._session = session or httpx.Client()
        self._cache: dict[tuple[str, date], tuple[float, list[dict]]] = {}
        self._candidates: dict[str, _StoredCandidate] = {}
        self._lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def close(self) -> None:
        self._session.close()

    def lookup(
        self, flight_number: str, departure_date: date, *, trip_id: str
    ) -> FlightLookupOutcome:
        number = normalize_flight_number(flight_number)
        if abs((departure_date - date.today()).days) > 365:
            return self._outcome(
                number,
                departure_date,
                "out_of_scope",
                "免费数据源只支持当前日期前后 365 天内的航班查询。",
            )
        if not self.configured:
            return self._outcome(
                number,
                departure_date,
                "not_configured",
                "航班数据源尚未配置，已保留为待人工确认。",
            )

        key = (number, departure_date)
        cached = False
        with self._lock:
            self._prune_locked()
            entry = self._cache.get(key)
            if entry and entry[0] > time.monotonic():
                raw_flights = entry[1]
                cached = True
            else:
                raw_flights = []

        if not cached:
            try:
                raw_flights = self._fetch(number, departure_date)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code == 429:
                    return self._outcome(
                        number,
                        departure_date,
                        "quota_exceeded",
                        "免费航班查询额度或速率已达到上限，请稍后重试。",
                    )
                return self._outcome(
                    number,
                    departure_date,
                    "unavailable",
                    "航班数据源暂时不可用，未使用未经核验的数据。",
                )
            except (httpx.HTTPError, ValueError):
                return self._outcome(
                    number,
                    departure_date,
                    "unavailable",
                    "航班数据源暂时不可用，未使用未经核验的数据。",
                )
            with self._lock:
                self._cache[key] = (
                    time.monotonic() + self.cache_ttl_seconds,
                    raw_flights,
                )

        candidates: list[ProviderFlightCandidate] = []
        for raw in raw_flights[:8]:
            try:
                parsed = self._parse_candidate(raw, number, trip_id)
            except (KeyError, TypeError, ValueError):
                # Third-party payload drift must degrade to "not found" rather
                # than turn a user-triggered lookup into an application 500.
                parsed = None
            if parsed is not None:
                candidates.append(parsed)
        if not candidates:
            return self._outcome(
                number,
                departure_date,
                "not_found",
                "数据源未找到该日期的可确认航班，请检查航班号和出发日期。",
                cached=cached,
            )
        return FlightLookupOutcome(
            flight_number=number,
            departure_date=departure_date,
            status="found",
            message=f"找到 {len(candidates)} 个航班候选，请核对后确认。",
            cached=cached,
            candidates=candidates,
        )

    def confirmed_input(self, candidate_id: str, *, trip_id: str) -> TransportInput:
        with self._lock:
            self._prune_locked()
            stored = self._candidates.get(candidate_id)
            if stored is None or stored.trip_id != trip_id:
                raise ProviderCandidateError("provider candidate is missing or expired")
            self._candidates.pop(candidate_id, None)
            return stored.payload.model_copy(deep=True)

    def _fetch(self, number: str, departure_date: date) -> list[dict]:
        url = (
            f"{self.base_url}/flights/number/{quote(number, safe='')}/"
            f"{departure_date.isoformat()}"
        )
        with self._request_lock:
            wait_seconds = 1.05 - (time.monotonic() - self._last_request_at)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            response = self._session.get(
                url,
                params={
                    "dateLocalRole": "Departure",
                    "withAircraftImage": "false",
                    "withLocation": "false",
                    "withFlightPlan": "false",
                },
                headers={
                    "X-RapidAPI-Key": self._api_key,
                    "X-RapidAPI-Host": self.host,
                    "Accept": "application/json",
                    "User-Agent": "TripFlow/0.8 (+https://github.com/mingyoud2003-bot/cross-border-travel-agent)",
                },
                timeout=self.timeout_seconds,
            )
            self._last_request_at = time.monotonic()
        if response.status_code == 204:
            return []
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("provider response is too large")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("provider returned an unexpected response")
        return [item for item in payload if isinstance(item, dict)]

    def _parse_candidate(
        self, raw: dict, requested_number: str, trip_id: str
    ) -> ProviderFlightCandidate | None:
        try:
            returned_number = normalize_flight_number(str(raw["number"]))
            if returned_number != requested_number:
                return None
            departure = raw["departure"]
            arrival = raw["arrival"]
            dep_airport = departure["airport"]
            arr_airport = arrival["airport"]
            dep_time = departure.get("scheduledTime") or departure.get("revisedTime")
            arr_time = arrival.get("scheduledTime") or arrival.get("revisedTime")
            if not dep_time or not arr_time:
                return None
            origin = _location(dep_airport)
            destination = _location(arr_airport)
            departure_at = _provider_datetime(dep_time["local"])
            arrival_at = _provider_datetime(arr_time["local"])
            airline = raw.get("airline") or {}
            operator = str(airline.get("name") or requested_number[:2]).strip()
            if arrival_at <= departure_at:
                return None
        except (KeyError, TypeError, ValueError):
            return None

        checked_at = datetime.now(UTC)
        candidate_id = f"pfc_{uuid4().hex}"
        status = str(raw.get("status") or "Unknown")
        quality = sorted(
            {
                str(item)
                for movement in (departure, arrival)
                for item in movement.get("quality", [])
                if item
            }
        )
        source = SourceInput(
            source_type="provider",
            source_id=candidate_id,
            source_excerpt=(
                f"AeroDataBox {returned_number}; status={status}; "
                f"checked_at={checked_at.isoformat()}"
            ),
            confirmed_by_user=True,
        )
        payload = TransportInput(
            mode="flight",
            operator=operator,
            service_number=_display_flight_number(str(raw["number"])),
            origin=origin,
            destination=destination,
            departure_at=departure_at,
            arrival_at=arrival_at,
            status="cancelled" if status in {"Canceled", "CanceledUncertain"} else "confirmed",
            source=source,
        )
        with self._lock:
            self._candidates[candidate_id] = _StoredCandidate(
                trip_id=trip_id,
                payload=payload,
                expires_at=time.monotonic() + self.candidate_ttl_seconds,
            )
        return ProviderFlightCandidate(
            candidate_id=candidate_id,
            flight_number=payload.service_number or requested_number,
            operator=operator,
            flight_status=status,
            origin=origin,
            destination=destination,
            departure_at=departure_at,
            arrival_at=arrival_at,
            departure_terminal=_optional_text(departure.get("terminal")),
            departure_gate=_optional_text(departure.get("gate")),
            arrival_terminal=_optional_text(arrival.get("terminal")),
            arrival_gate=_optional_text(arrival.get("gate")),
            data_quality=quality,
            checked_at=checked_at,
        )

    def _prune_locked(self) -> None:
        now = time.monotonic()
        self._cache = {
            key: value for key, value in self._cache.items() if value[0] > now
        }
        self._candidates = {
            key: value
            for key, value in self._candidates.items()
            if value.expires_at > now
        }

    @staticmethod
    def _outcome(
        number: str,
        departure_date: date,
        status: Literal[
            "found", "not_found", "not_configured", "out_of_scope",
            "quota_exceeded", "unavailable"
        ],
        message: str,
        *,
        cached: bool = False,
    ) -> FlightLookupOutcome:
        return FlightLookupOutcome(
            flight_number=number,
            departure_date=departure_date,
            status=status,
            message=message,
            cached=cached,
        )


def normalize_flight_number(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "", value).upper()
    if not _FLIGHT_NUMBER.fullmatch(normalized):
        raise ValueError("flight number must contain an airline code and number")
    return normalized


def _display_flight_number(value: str) -> str:
    return " ".join(value.upper().split())


def _location(raw: dict) -> Location:
    name = str(raw["name"]).strip()
    city = str(raw.get("municipalityName") or name).strip()
    timezone = str(raw["timeZone"]).strip()
    code = str(raw.get("iata") or raw.get("icao") or "").strip()
    display_name = f"{name} ({code})" if code else name
    return Location(name=display_name, city=city, timezone=timezone)


def _provider_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provider datetime must contain an offset")
    return parsed


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
