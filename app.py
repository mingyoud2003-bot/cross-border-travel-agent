from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from agent import travel_agent
from agent_service import AgentService, AgentUnavailableError, SessionNotFoundError
from api_models import (
    ChatRequest,
    ChatResponse,
    DeleteSessionResponse,
    HealthResponse,
    ReadinessResponse,
    SessionResponse,
)
from logging_config import configure_logging
from metrics import MetricsRegistry
from rate_limit import SlidingWindowRateLimiter
from settings import (
    api_key_configured,
    load_local_env,
    rate_limit_per_minute,
    session_db_path,
)
from tripflow_api import build_tripflow_router
from tripflow_service import TripFlowService


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"


def create_app(
    service: AgentService | None = None,
    tripflow_service: TripFlowService | None = None,
) -> FastAPI:
    load_local_env()
    metrics = MetricsRegistry()
    runtime = service or AgentService(metrics=metrics)
    tripflow = tripflow_service or TripFlowService(db_path=session_db_path())
    limiter = SlidingWindowRateLimiter(rate_limit_per_minute())
    logger = configure_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        close = getattr(getattr(runtime, "store", None), "close", None)
        if close:
            close()
        tripflow.close()

    api = FastAPI(
        title="TripFlow Travel Operations Agent",
        version="0.7.0",
        description="A confirmation-gated itinerary workspace with traceable AI extraction.",
        lifespan=lifespan,
    )
    api.include_router(build_tripflow_router(tripflow))

    @api.middleware("http")
    async def request_observability(request: Request, call_next):
        supplied_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_id
            if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", supplied_id)
            else uuid4().hex
        )
        request.state.request_id = request_id
        started = time.perf_counter()
        limited_response = None
        if (
            request.method == "POST"
            and request.url.path.endswith("/proposals/text")
        ):
            client_key = request.client.host if request.client else "unknown"
            allowed, retry_after = await limiter.check(client_key)
            if not allowed:
                limited_response = JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded."},
                    headers={"Retry-After": str(retry_after)},
                )
        status_code = 500
        try:
            response = limited_response or await call_next(request)
            status_code = response.status_code
        except Exception:
            route = request.scope.get("route")
            metrics.observe_http(
                request.method,
                getattr(route, "path", "__unmatched__"),
                500,
                time.perf_counter() - started,
            )
            logger.exception(
                "request_failed",
                extra={
                    "structured_fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                    }
                },
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000)
        route = request.scope.get("route")
        route_path = getattr(route, "path", "__unmatched__")
        metrics.observe_http(
            request.method,
            route_path,
            status_code,
            duration_ms / 1000,
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        logger.info(
            "request_completed",
            extra={
                "structured_fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                }
            },
        )
        return response

    @api.get("/health", response_model=HealthResponse)
    async def health() -> dict:
        return {
            "status": "ok",
            "service": "travel-decision-agent",
            "version": api.version,
            "model": str(travel_agent.model),
            "api_key_configured": api_key_configured(),
        }

    @api.get("/ready", response_model=ReadinessResponse)
    async def readiness() -> dict:
        if not runtime.ready() or not api_key_configured():
            raise HTTPException(status_code=503, detail="Service is not ready.")
        return {"status": "ready", "database": "ready", "api_key": "configured"}

    @api.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @api.post("/api/sessions", response_model=SessionResponse, status_code=201)
    async def create_session() -> dict:
        return runtime.create_session()

    @api.get("/api/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str) -> dict:
        try:
            return runtime.get_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc

    @api.delete(
        "/api/sessions/{session_id}", response_model=DeleteSessionResponse
    )
    async def delete_session(session_id: str) -> dict:
        return {"deleted": runtime.delete_session(session_id)}

    @api.post("/api/chat", response_model=ChatResponse)
    async def chat(request_body: ChatRequest, request: Request) -> dict:
        client_key = request.client.host if request.client else "unknown"
        allowed, retry_after = await limiter.check(client_key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded."},
                headers={"Retry-After": str(retry_after)},
            )
        try:
            return await runtime.chat(
                request_body.message,
                request_body.session_id,
                request_id=request.state.request_id,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc
        except AgentUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @api.get("/tripflow", include_in_schema=False)
    async def tripflow_home() -> FileResponse:
        return FileResponse(WEB_DIR / "tripflow.html")

    api.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    api.state.agent_service = runtime
    api.state.tripflow_service = tripflow
    api.state.metrics = metrics
    return api


app = create_app()
