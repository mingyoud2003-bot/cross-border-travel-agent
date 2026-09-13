from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, Response, UploadFile
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
from tripflow_imports import (
    AgentsDocumentExtractor,
    DocumentExtractionError,
    DocumentExtractor,
    ImportBatch,
    ImportCandidate,
    ImportConfirmationRequest,
    ImportConfirmationResult,
    MAX_BATCH_BYTES,
    MAX_FILE_BYTES,
    MAX_UPLOAD_FILES,
    SourceDocument,
    candidates_from_extraction,
    file_digest,
    mark_duplicates,
    normalize_document_extraction,
    safe_filename,
    stay_field_sources,
    stay_input_from_candidate,
    transport_field_sources,
    transport_input_from_candidate,
    validate_upload,
)
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
    document_extractor: DocumentExtractor | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/trips", tags=["TripFlow"])
    proposals = proposal_service or AgentsProposalService()
    flights = flight_provider or AeroDataBoxFlightProvider(
        os.environ.get("AERODATABOX_RAPIDAPI_KEY")
    )
    conversations = conversation_service or AgentsConversationService()
    documents = document_extractor or AgentsDocumentExtractor()

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

    @router.get("/{trip_id}/imports", response_model=list[ImportBatch])
    def list_imports(trip_id: str) -> list[ImportBatch]:
        try:
            return service.list_import_batches(trip_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc

    @router.get("/{trip_id}/imports/{batch_id}", response_model=ImportBatch)
    def get_import(trip_id: str, batch_id: str) -> ImportBatch:
        try:
            return service.get_import_batch(trip_id, batch_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="import batch not found") from exc

    @router.post("/{trip_id}/imports", response_model=ImportBatch, status_code=201)
    async def create_import(
        trip_id: str,
        files: Annotated[list[UploadFile], File()],
        instructions: Annotated[str, Form(max_length=1000)] = "",
    ) -> ImportBatch:
        try:
            service.get_trip(trip_id)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trip not found") from exc
        if not files or len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(status_code=422, detail="单次最多上传 10 个文件。")

        records: list[tuple[SourceDocument, bytes | None]] = []
        seen_hashes: set[str] = set()
        total_bytes = 0
        for upload in files:
            filename = safe_filename(upload.filename or "upload")
            data = await upload.read(MAX_FILE_BYTES + 1)
            total_bytes += len(data)
            digest = file_digest(data)
            try:
                mime = validate_upload(filename, upload.content_type or "", data)
                if total_bytes > MAX_BATCH_BYTES:
                    raise ValueError("单次上传文件总大小不能超过 30 MB。")
                duplicate = digest in seen_hashes
                document = SourceDocument(
                    filename=filename,
                    mime_type=mime,
                    sha256=digest,
                    size_bytes=len(data),
                    status="duplicate" if duplicate else "parsed",
                )
                records.append((document, None if duplicate else data))
                seen_hashes.add(digest)
            except ValueError as exc:
                records.append(
                    (
                        SourceDocument(
                            filename=filename,
                            mime_type=(upload.content_type or "application/octet-stream")[
                                :120
                            ],
                            sha256=digest,
                            size_bytes=len(data),
                            status="failed",
                            error=str(exc),
                        ),
                        None,
                    )
                )

        semaphore = asyncio.Semaphore(2)

        async def extract_one(document: SourceDocument, data: bytes | None):
            if data is None:
                return None
            try:
                async with semaphore:
                    extraction = await documents.extract(
                        filename=document.filename,
                        mime_type=document.mime_type,
                        data=data,
                        instructions=instructions.strip(),
                    )
                    return normalize_document_extraction(extraction)
            except DocumentExtractionError as exc:
                document.status = "failed"
                document.error = str(exc)
                return None
            except Exception:
                logger.exception("document_extraction_unexpected_error")
                document.status = "failed"
                document.error = "文件解析暂时失败，请稍后重试。"
                return None

        extracted = await asyncio.gather(
            *(extract_one(document, data) for document, data in records)
        )
        candidates: list[ImportCandidate] = []
        for (document, _), extraction in zip(records, extracted):
            if extraction is not None:
                candidates.extend(candidates_from_extraction(extraction, document))
        mark_duplicates(candidates)
        parsed = sum(document.status == "parsed" for document, _ in records)
        failed = sum(document.status == "failed" for document, _ in records)
        status = "failed" if not parsed else "partial_failed" if failed else "completed"
        batch = ImportBatch(
            trip_id=trip_id,
            status=status,
            instructions=instructions.strip(),
            documents=[document for document, _ in records],
            candidates=candidates,
        )
        service.save_import_batch(batch)
        return batch

    @router.post(
        "/{trip_id}/imports/{batch_id}/candidates/{candidate_id}/confirm",
        response_model=ImportConfirmationResult,
    )
    def confirm_import_candidate(
        trip_id: str,
        batch_id: str,
        candidate_id: str,
        payload: ImportConfirmationRequest,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> ImportConfirmationResult:
        try:
            batch = service.get_import_batch(trip_id, batch_id)
            candidate = _import_candidate(batch, candidate_id)
            if candidate.review_status != "pending":
                raise ValueError("该候选已经处理。")
            if candidate.duplicate_of and not payload.force_duplicate:
                raise ValueError("该候选与同批次行程重复，请确认是否仍要添加。")
            document = next(
                item for item in batch.documents if item.id == candidate.source_document_id
            )
            if candidate.kind == "transport":
                if payload.stay:
                    raise ValueError("候选类型与补全数据不一致。")
                item = transport_input_from_candidate(
                    candidate, document.mime_type, payload.transport
                )
                trip = service.add_transport(
                    trip_id,
                    item,
                    expected_version=if_match,
                    field_sources=transport_field_sources(
                        candidate, document.mime_type, item
                    ),
                )
            else:
                if payload.transport:
                    raise ValueError("候选类型与补全数据不一致。")
                item = stay_input_from_candidate(
                    candidate, document.mime_type, payload.stay
                )
                trip = service.add_stay(
                    trip_id,
                    item,
                    expected_version=if_match,
                    field_sources=stay_field_sources(
                        candidate, document.mime_type, item
                    ),
                )
            candidate.review_status = "confirmed"
            service.save_import_batch(batch)
            return ImportConfirmationResult(trip=trip, batch=batch)
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="import candidate not found") from exc
        except (ValueError, StopIteration) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TripVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post(
        "/{trip_id}/imports/{batch_id}/candidates/{candidate_id}/skip",
        response_model=ImportBatch,
    )
    def skip_import_candidate(
        trip_id: str, batch_id: str, candidate_id: str
    ) -> ImportBatch:
        try:
            batch = service.get_import_batch(trip_id, batch_id)
            candidate = _import_candidate(batch, candidate_id)
            if candidate.review_status == "confirmed":
                raise ValueError("已确认候选不能跳过。")
            candidate.review_status = "skipped"
            service.save_import_batch(batch)
            return batch
        except TripNotFoundError as exc:
            raise HTTPException(status_code=404, detail="import candidate not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/{trip_id}/transport", response_model=Trip)
    def add_transport(
        trip_id: str,
        payload: TransportInput,
        if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    ) -> Trip:
        _reject_client_trusted_source(payload)
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
        _reject_client_trusted_source(payload)
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
        _reject_client_trusted_source(payload)
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
        _reject_client_trusted_source(payload)
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


def _reject_client_trusted_source(payload: TransportInput | StayInput) -> None:
    if payload.source.source_type in {"provider", "pdf", "image", "catalog"}:
        raise HTTPException(
            status_code=422,
            detail="Provider、文件和目录来源只能由服务端可验证流程写入。",
        )


def _import_candidate(batch: ImportBatch, candidate_id: str) -> ImportCandidate:
    candidate = next(
        (item for item in batch.candidates if item.id == candidate_id), None
    )
    if candidate is None:
        raise TripNotFoundError(candidate_id)
    return candidate


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
