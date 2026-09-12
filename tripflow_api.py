from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field

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


class TextProposalRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


def build_tripflow_router(
    service: TripFlowService,
    proposal_service: ProposalService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/trips", tags=["TripFlow"])
    proposals = proposal_service or AgentsProposalService()

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
            return await proposals.propose(payload.text.strip())
        except ProposalUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
