from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from flight_provider import (
    AeroDataBoxFlightProvider,
    FlightLookupOutcome,
    FlightProvider,
    ProviderCandidateError,
)
from tripflow_agent import (
    AgentsProposalService,
    ItineraryProposal,
    ProposalService,
    ProposalUnavailableError,
)
from tripflow_models import CreateTripInput, StayInput, TransportInput, Trip
from tripflow_service import (
    TripFlowService,
    TripNotFoundError,
    TripVersionConflictError,
)


logger = logging.getLogger(__name__)


class TextProposalRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ProviderConfirmationRequest(BaseModel):
    candidate_id: str = Field(
        min_length=36, max_length=36, pattern=r"^pfc_[0-9a-f]{32}$"
    )


def build_tripflow_router(
    service: TripFlowService,
    proposal_service: ProposalService | None = None,
    flight_provider: FlightProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/trips", tags=["TripFlow"])
    proposals = proposal_service or AgentsProposalService()
    flights = flight_provider or AeroDataBoxFlightProvider(
        os.environ.get("AERODATABOX_RAPIDAPI_KEY")
    )

    @router.post("", response_model=Trip, status_code=201)
    def create_trip(payload: CreateTripInput) -> Trip:
        return service.create_trip(payload)

    @router.get("/{trip_id}", response_model=Trip)
    def get_trip(trip_id: str) -> Trip:
        try:
            return service.get_trip(trip_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc

    @router.delete("/{trip_id}", status_code=204)
    def delete_trip(trip_id: str) -> Response:
        if not service.delete_trip(trip_id):
            raise HTTPException(status_code=404, detail="trip not found")
        return Response(status_code=204)

    @router.post("/{trip_id}/transport", response_model=Trip)
    def add_transport(
        trip_id: str,
        payload: TransportInput,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> Trip:
        _reject_client_provider_source(payload)
        try:
            return service.add_transport(
                trip_id, payload, expected_version=if_match
            )
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        except TripVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/{trip_id}/stays", response_model=Trip)
    def add_stay(
        trip_id: str,
        payload: StayInput,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> Trip:
        _reject_client_provider_source(payload)
        try:
            return service.add_stay(trip_id, payload, expected_version=if_match)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        except TripVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.put("/{trip_id}/transport/{reservation_id}", response_model=Trip)
    def update_transport(
        trip_id: str,
        reservation_id: str,
        payload: TransportInput,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> Trip:
        _reject_client_provider_source(payload)
        try:
            return service.update_transport(
                trip_id, reservation_id, payload, expected_version=if_match
            )
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="reservation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TripVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.put("/{trip_id}/stays/{reservation_id}", response_model=Trip)
    def update_stay(
        trip_id: str,
        reservation_id: str,
        payload: StayInput,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> Trip:
        _reject_client_provider_source(payload)
        try:
            return service.update_stay(
                trip_id, reservation_id, payload, expected_version=if_match
            )
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="reservation not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TripVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/{trip_id}/reservations/{reservation_id}", response_model=Trip)
    def delete_reservation(
        trip_id: str,
        reservation_id: str,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> Trip:
        try:
            return service.delete_reservation(
                trip_id, reservation_id, expected_version=if_match
            )
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="reservation not found") from exc
        except TripVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/{trip_id}/conflicts")
    def list_conflicts(trip_id: str):
        try:
            return service.conflicts(trip_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc

    @router.get("/{trip_id}/calendar.ics")
    def export_calendar(trip_id: str) -> Response:
        try:
            calendar = service.export_ics(trip_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        return Response(
            content=calendar,
            media_type="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{trip_id}.ics"'
            },
        )

    @router.post("/{trip_id}/proposals/text", response_model=ItineraryProposal)
    async def propose_from_text(
        trip_id: str, payload: TextProposalRequest
    ) -> ItineraryProposal:
        try:
            service.get_trip(trip_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        try:
            proposal = await proposals.propose(payload.text.strip())
        except ProposalUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        for lookup in proposal.flight_lookups:
            if not lookup.flight_number or not lookup.departure_date:
                continue
            try:
                outcome = await run_in_threadpool(
                    flights.lookup,
                    lookup.flight_number,
                    lookup.departure_date,
                    trip_id=trip_id,
                )
            except Exception:
                # A provider adapter is an external failure boundary. Do not let
                # an unexpected SDK/payload error turn a usable itinerary draft
                # into a 500, and never ask the model to fill the missing data.
                logger.exception(
                    "flight_provider_unexpected_error",
                    extra={"provider": "aerodatabox", "flight_number": lookup.flight_number},
                )
                outcome = FlightLookupOutcome(
                    flight_number=lookup.flight_number,
                    departure_date=lookup.departure_date,
                    status="unavailable",
                    message="航班数据源暂时不可用，未使用未经核验的数据。",
                )
            proposal.provider_lookups.append(outcome)
            if outcome.status != "found":
                proposal.warnings.append(outcome.message)
        proposal.warnings = list(dict.fromkeys(proposal.warnings))
        return proposal

    @router.post(
        "/{trip_id}/providers/flights/confirm",
        response_model=Trip,
    )
    def confirm_provider_flight(
        trip_id: str,
        payload: ProviderConfirmationRequest,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> Trip:
        try:
            current = service.get_trip(trip_id)
            if current.version != if_match:
                raise TripVersionConflictError(
                    f"expected trip version {if_match}, current version is {current.version}"
                )
            transport = flights.confirmed_input(payload.candidate_id, trip_id=trip_id)
            return service.add_transport(
                trip_id, transport, expected_version=if_match
            )
        except ProviderCandidateError as exc:
            raise HTTPException(
                status_code=410,
                detail="航班候选已过期，请重新查询后确认。",
            ) from exc
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        except TripVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router


def _reject_client_provider_source(payload: TransportInput | StayInput) -> None:
    if payload.source.source_type == "provider":
        raise HTTPException(
            status_code=422,
            detail="Provider 来源只能通过服务端航班候选确认端点写入。",
        )
