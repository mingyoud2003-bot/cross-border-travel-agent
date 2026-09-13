from fastapi.testclient import TestClient

from app import create_app
from state import TravelState
from tripflow_service import TripFlowService


class FakeService:
    def __init__(self) -> None:
        self.session_id = "a" * 32
        self.state = TravelState()

    def create_session(self):
        return {
            "session_id": self.session_id,
            "created_at": "2026-09-09T00:00:00+00:00",
            "state": self.state.snapshot(),
            "traces": [],
        }

    def get_session(self, session_id):
        assert session_id == self.session_id
        return self.create_session()

    def delete_session(self, session_id):
        return session_id == self.session_id

    def ready(self):
        return True

    async def chat(self, message, session_id=None, request_id=None):
        self.state.begin_turn(message)
        return {
            "request_id": request_id or "b" * 32,
            "session_id": session_id or self.session_id,
            "answer_type": "text",
            "message": "测试回答",
            "decision": None,
            "state": self.state.snapshot(),
            "trace": {
                "request_id": "b" * 32,
                "created_at": "2026-09-09T00:00:01+00:00",
                "duration_ms": 10,
                "task": self.state.current_task,
                "tools": [],
                "usage": {"requests": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        }


def client() -> TestClient:
    return TestClient(create_app(FakeService()))


def test_health_endpoint_exposes_readiness_without_secret():
    response = client().get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model"] == "gpt-5.6-luna"
    assert "OPENAI_API_KEY" not in response.text


def test_session_and_chat_contract():
    api = client()
    created = api.post("/api/sessions")
    session_id = created.json()["session_id"]
    response = api.post(
        "/api/chat",
        json={"session_id": session_id, "message": "你好"},
    )

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.json()["message"] == "测试回答"
    assert response.json()["state"]["turn_id"] == 1
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_chat_rejects_blank_or_oversized_input():
    api = client()

    assert api.post("/api/chat", json={"message": "   "}).status_code == 422
    assert api.post("/api/chat", json={"message": "x" * 4001}).status_code == 422


def test_web_ui_and_static_assets_are_served():
    api = client()

    page = api.get("/")
    tripflow_script = api.get("/static/tripflow.js")

    assert page.status_code == 200
    assert "TripFlow" in page.text
    assert 'id="transport-form"' in page.text
    assert 'id="stay-form"' in page.text
    assert 'id="trip-files"' in page.text
    assert 'id="import-queue"' in page.text
    assert tripflow_script.status_code == 200
    assert "ready_for_confirmation" in tripflow_script.text
    assert "review-pending" in tripflow_script.text
    assert "/conversation" in tripflow_script.text
    assert "/imports" in tripflow_script.text
    assert "请选择时区" in tripflow_script.text
    assert '香港:"Asia/Hong_Kong"' in tripflow_script.text
    assert '洛杉矶:"America/Los_Angeles"' in tripflow_script.text
    assert '旧金山:"America/Los_Angeles"' in tripflow_script.text
    assert "跨越国际日期变更线时" in tripflow_script.text
    assert "/rules/query" in tripflow_script.text
    assert "safeExternalUrl" in tripflow_script.text
    assert 'id="rules-form"' in page.text
    assert "官方来源 · 有据才答" in page.text
    assert "escapeHtml" in tripflow_script.text

    legacy_page = api.get("/legacy")
    legacy_script = api.get("/static/app.js")
    assert legacy_page.status_code == 200
    assert "Travel Decision Agent" in legacy_page.text
    assert legacy_script.status_code == 200
    assert "renderDecision" in legacy_script.text


def test_tripflow_main_app_flow_reaches_conflict_and_calendar():
    api = TestClient(create_app(FakeService(), TripFlowService()))
    trip = api.post(
        "/api/trips", json={"title": "Browser flow", "minimum_connection_minutes": 90}
    ).json()
    first = {
        "mode": "train",
        "operator": "Deutsche Bahn",
        "service_number": "ICE 1",
        "origin": {"name": "Berlin Hbf", "city": "Berlin", "timezone": "Europe/Berlin"},
        "destination": {"name": "Frankfurt Hbf", "city": "Frankfurt", "timezone": "Europe/Berlin"},
        "departure_at": "2026-10-26T08:00",
        "arrival_at": "2026-10-26T11:00",
    }
    trip = api.post(
        f"/api/trips/{trip['id']}/transport",
        headers={"If-Match": str(trip["version"])}, json=first,
    ).json()
    second = {
        **first,
        "service_number": "ICE 2",
        "origin": {"name": "Frankfurt Airport", "city": "Frankfurt", "timezone": "Europe/Berlin"},
        "destination": {"name": "Köln Hbf", "city": "Cologne", "timezone": "Europe/Berlin"},
        "departure_at": "2026-10-26T11:30",
        "arrival_at": "2026-10-26T13:00",
    }
    trip = api.post(
        f"/api/trips/{trip['id']}/transport",
        headers={"If-Match": str(trip["version"])}, json=second,
    ).json()

    conflicts = api.get(f"/api/trips/{trip['id']}/conflicts")
    calendar = api.get(f"/api/trips/{trip['id']}/calendar.ics")

    assert conflicts.status_code == 200
    assert conflicts.json()[0]["type"] == "short_connection"
    assert conflicts.json()[0]["evidence"]["buffer_minutes"] == 30
    assert calendar.status_code == 200
    assert calendar.text.count("BEGIN:VEVENT") == 2


def test_tripflow_proposal_endpoint_is_rate_limited_before_model_call(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    api = TestClient(create_app(FakeService(), TripFlowService()))

    first = api.post("/api/trips/not-found/proposals/text", json={"text": "booking"})
    second = api.post("/api/trips/not-found/proposals/text", json={"text": "booking"})

    assert first.status_code == 404
    assert second.status_code == 429
    assert "retry-after" in second.headers


def test_readiness_checks_database_and_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    response = client().get("/ready")

    assert response.status_code == 200
    assert response.json()["database"] == "ready"


def test_readiness_fails_without_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")

    response = client().get("/ready")

    assert response.status_code == 503


def test_chat_rate_limit_returns_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    api = TestClient(create_app(FakeService()))

    first = api.post("/api/chat", json={"message": "first"})
    second = api.post("/api/chat", json={"message": "second"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1


def test_security_headers_and_request_metrics_are_exposed():
    api = client()

    health = api.get("/health")
    metrics = api.get("/metrics")

    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in health.headers["content-security-policy"]
    assert metrics.status_code == 200
    assert 'travel_agent_http_requests_total{method="GET",route="/health",status="200"} 1' in metrics.text
    assert "session_id" not in metrics.text
