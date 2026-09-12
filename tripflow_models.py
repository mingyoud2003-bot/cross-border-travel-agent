from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from location_catalog import resolve_city


SourceType = Literal["form", "text", "pdf", "image", "provider"]
ReservationStatus = Literal["draft", "confirmed", "cancelled"]
TransportMode = Literal["flight", "train"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(UTC)


class FieldProvenance(BaseModel):
    source_type: SourceType
    source_id: str
    source_excerpt: str = Field(default="", max_length=500)
    confirmed_by_user: bool
    recorded_at: datetime = Field(default_factory=utc_now)


class SourceInput(BaseModel):
    source_type: SourceType = "form"
    source_id: str | None = Field(default=None, max_length=128)
    source_excerpt: str = Field(default="", max_length=500)
    confirmed_by_user: bool = True


class Location(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=100)
    city_id: str | None = Field(default=None, max_length=16)
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("name", "city", "timezone")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def valid_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def canonicalize_city(self) -> "Location":
        city = resolve_city(self.city, self.timezone)
        if city:
            self.city = city.display_name
            self.city_id = city.city_id
        else:
            # Never trust a client/model supplied identity for an unresolved city.
            self.city_id = None
        return self


class CreateTripInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    home_timezone: str = Field(default="Asia/Shanghai", max_length=64)
    minimum_connection_minutes: int = Field(default=90, ge=0, le=1440)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return value.strip()

    @field_validator("home_timezone")
    @classmethod
    def valid_home_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("home_timezone must be a valid IANA timezone") from exc
        return value


class TransportInput(BaseModel):
    mode: TransportMode
    operator: str = Field(min_length=1, max_length=120)
    service_number: str | None = Field(default=None, max_length=80)
    origin: Location
    destination: Location
    departure_at: datetime
    arrival_at: datetime
    status: ReservationStatus = "confirmed"
    source: SourceInput = Field(default_factory=SourceInput)

    @field_validator("operator")
    @classmethod
    def strip_operator(cls, value: str) -> str:
        return value.strip()

    @field_validator("service_number")
    @classmethod
    def strip_service_number(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None

    @model_validator(mode="after")
    def chronological(self) -> "TransportInput":
        if self.departure_at.tzinfo is None or self.departure_at.utcoffset() is None:
            self.departure_at = self.departure_at.replace(
                tzinfo=ZoneInfo(self.origin.timezone)
            )
        if self.arrival_at.tzinfo is None or self.arrival_at.utcoffset() is None:
            self.arrival_at = self.arrival_at.replace(
                tzinfo=ZoneInfo(self.destination.timezone)
            )
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be later than departure_at")
        return self


class StayInput(BaseModel):
    property_name: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=100)
    city_id: str | None = Field(default=None, max_length=16)
    address: str | None = Field(default=None, max_length=300)
    check_in: date
    check_out: date
    status: ReservationStatus = "confirmed"
    source: SourceInput = Field(default_factory=SourceInput)

    @field_validator("property_name", "city")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def chronological(self) -> "StayInput":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be later than check_in")
        return self

    @model_validator(mode="after")
    def canonicalize_city(self) -> "StayInput":
        city = resolve_city(self.city)
        if city:
            self.city = city.display_name
            self.city_id = city.city_id
        else:
            self.city_id = None
        return self


class TransportReservation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("res"))
    kind: Literal["transport"] = "transport"
    mode: TransportMode
    operator: str
    service_number: str | None
    origin: Location
    destination: Location
    departure_at: datetime
    arrival_at: datetime
    status: ReservationStatus
    revision: int = Field(default=1, ge=1)
    provenance: dict[str, FieldProvenance]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class StayReservation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("res"))
    kind: Literal["stay"] = "stay"
    property_name: str
    city: str
    city_id: str | None = None
    address: str | None
    check_in: date
    check_out: date
    status: ReservationStatus
    revision: int = Field(default=1, ge=1)
    provenance: dict[str, FieldProvenance]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def canonicalize_city(self) -> "StayReservation":
        city = resolve_city(self.city)
        if city:
            self.city = city.display_name
            self.city_id = city.city_id
        else:
            self.city_id = None
        return self


Reservation = Annotated[
    TransportReservation | StayReservation,
    Field(discriminator="kind"),
]


class Trip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("trip"))
    title: str
    home_timezone: str
    minimum_connection_minutes: int
    version: int = 1
    reservations: list[Reservation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Conflict(BaseModel):
    id: str
    type: Literal["schedule_overlap", "short_connection"]
    severity: Literal["blocking", "warning"]
    reservation_ids: list[str]
    message: str
    evidence: dict[str, str | int]


def provenance_for_fields(
    field_names: list[str], source: SourceInput
) -> dict[str, FieldProvenance]:
    source_id = source.source_id or new_id("src")
    evidence = FieldProvenance(
        source_type=source.source_type,
        source_id=source_id,
        source_excerpt=source.source_excerpt,
        confirmed_by_user=source.confirmed_by_user,
    )
    return {field_name: evidence.model_copy(deep=True) for field_name in field_names}
