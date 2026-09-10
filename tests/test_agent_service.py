import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from metrics import MetricsRegistry
from agent_service import (
    AgentService,
    AgentUnavailableError,
    SessionNotFoundError,
    SessionStore,
    _parse_decision,
)


def fake_result(output: str):
    usage = SimpleNamespace(
        requests=1,
        input_tokens=20,
        output_tokens=10,
        total_tokens=30,
    )
    return SimpleNamespace(
        final_output=output,
        new_items=[],
        context_wrapper=SimpleNamespace(usage=usage),
    )


async def text_runner(*args, **kwargs):
    del args, kwargs
    return fake_result("你好，我可以帮你规划跨境出行。")


def memory_service(run_agent=text_runner) -> AgentService:
    return AgentService(store=SessionStore(db_path=":memory:"), run_agent=run_agent)


def test_service_creates_isolated_sessions():
    service = memory_service()
    first = service.create_session()
    second = service.create_session()

    assert first["session_id"] != second["session_id"]
    assert first["state"]["turn_id"] == 0


def test_chat_returns_state_usage_and_public_trace():
    service = memory_service()
    session_id = service.create_session()["session_id"]

    response = asyncio.run(service.chat("你好", session_id))

    assert response["answer_type"] == "text"
    assert response["state"]["turn_id"] == 1
    assert response["trace"]["usage"]["total_tokens"] == 30
    assert "state_before" not in response["trace"]
    assert service.get_session(session_id)["traces"][0]["request_id"] == response["request_id"]


def test_same_session_requests_are_serialized():
    active = 0
    peak = 0

    async def measured_runner(*args, **kwargs):
        nonlocal active, peak
        del args, kwargs
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return fake_result("ok")

    async def exercise():
        service = memory_service(measured_runner)
        session_id = service.create_session()["session_id"]
        await asyncio.gather(
            service.chat("你好", session_id),
            service.chat("继续", session_id),
        )
        return service.get_session(session_id)["state"]

    state = asyncio.run(exercise())

    assert peak == 1
    assert state["turn_id"] == 2


def test_different_sessions_can_run_concurrently():
    active = 0
    peak = 0

    async def measured_runner(*args, **kwargs):
        nonlocal active, peak
        del args, kwargs
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return fake_result("ok")

    async def exercise():
        service = memory_service(measured_runner)
        first = service.create_session()["session_id"]
        second = service.create_session()["session_id"]
        await asyncio.gather(
            service.chat("你好", first),
            service.chat("你好", second),
        )

    asyncio.run(exercise())

    assert peak == 2


def test_unknown_session_is_rejected():
    service = memory_service()

    with pytest.raises(SessionNotFoundError):
        service.get_session("0" * 32)


def test_api_failure_rolls_back_business_state():
    async def failing_runner(*args, **kwargs):
        del args, kwargs
        raise APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))

    service = memory_service(failing_runner)
    session_id = service.create_session()["session_id"]

    with pytest.raises(AgentUnavailableError):
        asyncio.run(service.chat("从伦敦去巴黎坐火车", session_id))

    state = service.get_session(session_id)["state"]
    assert state["turn_id"] == 0
    assert state["slots"] == {}


def test_session_store_evicts_oldest_record_at_capacity():
    store = SessionStore(db_path=":memory:", max_sessions=1)
    first = store.create().id
    second = store.create().id

    with pytest.raises(SessionNotFoundError):
        store.get(first)
    assert store.get(second).id == second


def test_decision_parser_requires_exact_contract():
    payload = {
        "request_summary": {},
        "transport_options": [],
        "redemption_value": {},
        "loyalty_benefits": [],
        "recommendation": {"summary": "test"},
        "tradeoffs": [],
        "evidence": [],
        "limitations": [],
    }

    assert _parse_decision(json.dumps(payload)) == payload
    payload["unexpected"] = True
    assert _parse_decision(json.dumps(payload)) is None


def test_state_and_trace_survive_store_restart(tmp_path):
    database = tmp_path / "sessions.db"
    first_store = SessionStore(db_path=database)
    first_service = AgentService(store=first_store, run_agent=text_runner)
    session_id = first_service.create_session()["session_id"]
    response = asyncio.run(first_service.chat("从伦敦出发坐火车", session_id))
    assert response["state"]["slots"]["origin"]["value"] == "伦敦"
    first_store.close()

    second_store = SessionStore(db_path=database)
    restored = second_store.get(session_id)

    assert restored.state.origin == "伦敦"
    assert restored.state.turn_id == 1
    assert len(restored.traces) == 1
    assert restored.traces[0]["request_id"] == response["request_id"]
    second_store.close()


def test_service_records_agent_latency_and_token_metrics():
    metrics = MetricsRegistry()
    service = AgentService(
        store=SessionStore(db_path=":memory:"),
        run_agent=text_runner,
        metrics=metrics,
    )

    asyncio.run(service.chat("你好"))
    rendered = metrics.render()

    assert 'travel_agent_runs_total{task="general",status="success"} 1' in rendered
    assert 'travel_agent_tokens_total{task="general",type="total_tokens"} 30' in rendered
