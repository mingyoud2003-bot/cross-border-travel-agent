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
    AgentsConversationService,
    AgentsProposalService,
    ConversationMessage,
    ConversationService,
    ConversationState,
    ItineraryProposal,
    ProposalService,
    ProposalUnavailableError,
    proposal_ready,
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


class ConversationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def build_tripflow_router(
    service: TripFlowService,
    proposal_service: ProposalService | None = None,
    flight_provider: FlightProvider | None = None,
    conversation_service: ConversationService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/trips", tags=["TripFlow"])
    proposals = proposal_service or AgentsProposalService()
    flights = flight_provider or AeroDataBoxFlightProvider(
        os.environ.get("AERODATABOX_RAPIDAPI_KEY")
    )
    conversations = conversation_service or AgentsConversationService()

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
        await _populate_provider_lookups(proposal, flights, trip_id)
        return proposal

    @router.get("/{trip_id}/conversation", response_model=ConversationState)
    def get_conversation(trip_id: str) -> ConversationState:
        try:
            return ConversationState.model_validate(service.get_conversation(trip_id))
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc

    @router.post("/{trip_id}/conversation", response_model=ConversationState)
    async def continue_conversation(
        trip_id: str, payload: ConversationRequest
    ) -> ConversationState:
        try:
            state = ConversationState.model_validate(service.get_conversation(trip_id))
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        user_message = payload.message.strip()
        if not user_message:
            raise HTTPException(status_code=422, detail="message must not be blank")
        pending_messages = [
            *state.messages,
            ConversationMessage(role="user", content=user_message),
        ][-30:]
        try:
            assistant_message, proposal = await conversations.respond(
                pending_messages, state.draft
            )
        except ProposalUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        await _populate_provider_lookups(proposal, flights, trip_id)
        ready = proposal_ready(proposal)
        state = ConversationState(
            messages=[
                *pending_messages,
                ConversationMessage(
                    role="assistant",
                    content=_conversation_reply(assistant_message, proposal, ready),
                ),
            ][-30:],
            draft=proposal,
            ready_for_confirmation=ready,
        )
        service.save_conversation(trip_id, state.model_dump(mode="json"))
        return state

    @router.delete("/{trip_id}/conversation", response_model=ConversationState)
    def reset_conversation(trip_id: str) -> ConversationState:
        try:
            service.clear_conversation(trip_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        return ConversationState()

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


async def _populate_provider_lookups(
    proposal: ItineraryProposal,
    flights: FlightProvider,
    trip_id: str,
) -> None:
    proposal.provider_lookups = []
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


def _conversation_reply(
    model_reply: str,
    proposal: ItineraryProposal,
    ready: bool,
) -> str:
    if ready:
        return (
            "候选信息已经完整，请核对待确认信息。"
            "只有你点击确认后，它才会加入右侧行程。"
        )
    if proposal.provider_lookups:
        return proposal.provider_lookups[0].message
    missing_lookup = [
        field
        for lookup in proposal.flight_lookups
        for field in lookup.missing_fields
    ]
    if "flight_number" in missing_lookup:
        return "可以，请告诉我航班号，例如 LH400。"
    if "departure_date" in missing_lookup:
        return "还需要这趟航班的明确出发日期，例如 2026-10-26。"
    incomplete = [
        field
        for item in [*proposal.transports, *proposal.stays]
        for field in item.missing_fields
    ]
    if incomplete:
        return _missing_field_question(incomplete[0])
    # The model's prose is intentionally not returned here. It can describe a
    # successful state that deterministic normalization did not produce.
    return "我还没有识别出可核对的行程信息。请告诉我航班、火车或住宿信息。"


def _missing_field_question(field: str) -> str:
    questions = {
        "operator": "还需要运营商或承运方，例如 Deutsche Bahn、SNCF 或 Lufthansa。",
        "origin.name": "还需要出发车站或机场；如果不确定，可以直接回复出发城市。",
        "origin.city": "还需要明确的出发城市。",
        "origin.timezone": "出发城市的时区还无法确定，请换用城市的常用中英文名称。",
        "destination.name": "还需要到达车站或机场；如果不确定，可以直接回复到达城市。",
        "destination.city": "还需要明确的到达城市。",
        "destination.timezone": "到达城市的时区还无法确定，请换用城市的常用中英文名称。",
        "departure_at": "还需要包含日期的明确出发时间，例如 2026-10-26 08:30。",
        "arrival_at": "还需要包含日期的明确到达时间，例如 2026-10-26 12:30。",
        "property_name": "还需要酒店或住宿名称。",
        "city": "还需要住宿所在城市。",
        "check_in": "还需要明确的入住日期，例如 2026-10-26。",
        "check_out": "还需要明确的退房日期，例如 2026-10-28。",
    }
    return questions.get(field, "还缺少一项关键信息，请继续补充。")
