import asyncio
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from travel_rules import (
    GroundedRuleDraft,
    RuleAnswer,
    RuleQuery,
    TravelRulesService,
    detect_rule_anchors,
    detect_rule_topic,
    rank_travel_rules,
    required_context,
)
from tripflow_api import build_tripflow_router
from tripflow_models import (
    CreateTripInput,
    Location,
    TransportReservation,
    Trip,
)
from tripflow_service import TripFlowService


def flight(operator: str = "British Airways", number: str = "BA281") -> TransportReservation:
    return TransportReservation(
        mode="flight",
        operator=operator,
        service_number=number,
        origin=Location(
            name="London Heathrow", city="London", timezone="Europe/London"
        ),
        destination=Location(
            name="Los Angeles International",
            city="Los Angeles",
            timezone="America/Los_Angeles",
        ),
        departure_at="2026-10-26T10:00:00+00:00",
        arrival_at="2026-10-26T14:00:00-07:00",
        status="confirmed",
        provenance={},
    )


def trip_with(*items: TransportReservation) -> Trip:
    return Trip(
        title="Rules",
        home_timezone="Asia/Shanghai",
        minimum_connection_minutes=90,
        reservations=list(items),
    )


def evidence(**overrides) -> dict:
    value = {
        "id": "ba_hand_baggage",
        "title": "BA hand baggage",
        "content": "One cabin bag is allowed within the published dimensions.",
        "source_name": "British Airways",
        "source_url": "https://www.britishairways.com/content/information/baggage-essentials",
        "anchors": ["british_airways"],
        "topics": ["baggage"],
        "keywords": ["carry-on", "随身行李"],
        "retrieved_at": "2026-09-14",
        "review_after": "2026-12-14",
        "embedding": [1.0, 0.0],
        "score": 0.9,
    }
    value.update(overrides)
    return value


class FakeRetriever:
    def __init__(self, results=None, error=None):
        self.results = [evidence()] if results is None else results
        self.error = error
        self.calls = []

    def retrieve(self, query, *, anchors, topic, top_k=4):
        self.calls.append((query, anchors, topic, top_k))
        if self.error:
            raise self.error
        return self.results


class FakeAnswerer:
    def __init__(self, draft=None):
        self.draft = draft or GroundedRuleDraft(
            status="answered",
            answer="根据英航发布规则，这段行程适用一件客舱行李的一般规则。",
            used_evidence_ids=["ba_hand_baggage"],
            limitations=["这不是对个人票面额度的确认。"],
        )
        self.context = None

    async def answer(self, *, question, itinerary_context, evidence):
        self.context = itinerary_context
        return self.draft


def test_topic_and_operator_alias_routing_is_deterministic():
    assert detect_rule_topic("这趟航班的随身行李限制？") == "baggage"
    assert detect_rule_topic("什么时候开放 online check-in？") == "check_in"
    assert detect_rule_topic("BA Silver 能进休息室吗？") == "loyalty"
    assert detect_rule_topic("Economy cabin bag size?") == "baggage"
    assert detect_rule_anchors("我的行李限制", "英国航空") == {"british_airways"}
    assert detect_rule_anchors("Economy cabin bag size?", "BA") == {"british_airways"}
    assert "oneworld" in detect_rule_anchors("我是英航银卡", "Qatar Airways")


def test_ranker_filters_metadata_before_semantic_ranking():
    index = [
        evidence(),
        evidence(
            id="lh_rule",
            anchors=["lufthansa"],
            embedding=[1.0, 0.0],
        ),
        evidence(
            id="ba_checkin",
            topics=["check_in"],
            embedding=[1.0, 0.0],
        ),
    ]

    result = rank_travel_rules(
        "BA carry-on",
        anchors={"british_airways"},
        topic="baggage",
        index=index,
        query_embedding=[1.0, 0.0],
    )

    assert [item["id"] for item in result] == ["ba_hand_baggage"]


def test_rule_answer_is_bound_to_itinerary_and_server_owned_citations():
    item = flight()
    retriever = FakeRetriever()
    answerer = FakeAnswerer()
    service = TravelRulesService(
        retriever, answerer, today=date(2026, 9, 14)
    )

    result = asyncio.run(
        service.ask(
            trip_with(item),
            RuleQuery(
                reservation_id=item.id,
                question="这趟经济舱的随身行李限制是什么？",
            ),
        )
    )

    assert result.status == "answered"
    assert result.applies_to_reservation_ids == [item.id]
    assert result.citations[0].evidence_id == "ba_hand_baggage"
    assert result.citations[0].source_url.startswith("https://www.britishairways.com/")
    assert answerer.context["operator"] == "British Airways"
    assert item.id in answerer.context["reservation_id"]


def test_checked_baggage_requires_cabin_before_retrieval():
    retriever = FakeRetriever()
    service = TravelRulesService(retriever, FakeAnswerer())

    result = asyncio.run(
        service.ask(
            trip_with(flight()), RuleQuery(question="这趟可以免费托运几件行李？")
        )
    )

    assert result.status == "needs_clarification"
    assert result.missing_context == ["cabin_or_fare_type"]
    assert retriever.calls == []


def test_connection_baggage_question_does_not_require_cabin():
    assert required_context("不同预订号转机需要重新托运行李吗？", "baggage") == []


def test_checked_baggage_count_alias_requires_cabin():
    assert required_context("英航托运行李能带几个箱？", "baggage") == [
        "cabin_or_fare_type"
    ]


def test_multiple_itinerary_legs_require_explicit_selection():
    retriever = FakeRetriever()
    service = TravelRulesService(retriever, FakeAnswerer())

    result = asyncio.run(
        service.ask(
            trip_with(flight(), flight("Lufthansa", "LH400")),
            RuleQuery(question="随身行李怎么带？"),
        )
    )

    assert result.status == "needs_clarification"
    assert result.missing_context == ["reservation_id"]
    assert retriever.calls == []


def test_unknown_rule_domain_abstains_without_retrieval():
    retriever = FakeRetriever()
    service = TravelRulesService(retriever, FakeAnswerer())

    result = asyncio.run(
        service.ask(trip_with(flight()), RuleQuery(question="伦敦明天会下雨吗？"))
    )

    assert result.status == "out_of_scope"
    assert retriever.calls == []


def test_unsupported_operator_abstains_before_requesting_ticket_context():
    retriever = FakeRetriever()
    service = TravelRulesService(retriever, FakeAnswerer())

    result = asyncio.run(
        service.ask(
            trip_with(flight("Deutsche Bahn", "ICE100")),
            RuleQuery(question="这趟火车能带多少行李？"),
        )
    )

    assert result.status == "out_of_scope"
    assert retriever.calls == []


def test_hallucinated_evidence_id_is_rejected():
    answerer = FakeAnswerer(
        GroundedRuleDraft(
            status="answered",
            answer="虚构回答",
            used_evidence_ids=["not_retrieved"],
        )
    )
    service = TravelRulesService(FakeRetriever(), answerer)

    result = asyncio.run(
        service.ask(
            trip_with(flight()), RuleQuery(question="经济舱随身行李是什么规则？")
        )
    )

    assert result.status == "insufficient_evidence"
    assert result.citations == []


def test_model_cannot_overrule_deterministic_missing_context_gate():
    answerer = FakeAnswerer(
        GroundedRuleDraft(
            status="needs_clarification",
            answer="通用随身行李规则已有充分证据，但请以票面为准。",
            used_evidence_ids=["ba_hand_baggage"],
            missing_context=["fare_type"],
        )
    )
    service = TravelRulesService(FakeRetriever(), answerer)

    result = asyncio.run(
        service.ask(
            trip_with(flight()), RuleQuery(question="经济舱随身行李尺寸是什么？")
        )
    )

    assert result.status == "answered"
    assert result.missing_context == []


def test_stale_only_evidence_is_not_used_for_an_answer():
    service = TravelRulesService(
        FakeRetriever([evidence(review_after="2026-09-01")]),
        FakeAnswerer(),
        today=date(2026, 9, 14),
    )

    result = asyncio.run(
        service.ask(
            trip_with(flight()), RuleQuery(question="经济舱随身行李是什么规则？")
        )
    )

    assert result.status == "stale_evidence"
    assert result.citations[0].evidence_id == "ba_hand_baggage"


def test_retrieval_failure_degrades_without_model_memory():
    service = TravelRulesService(
        FakeRetriever(error=RuntimeError("embedding unavailable")), FakeAnswerer()
    )

    result = asyncio.run(
        service.ask(
            trip_with(flight()), RuleQuery(question="经济舱随身行李是什么规则？")
        )
    )

    assert result.status == "unavailable"
    assert "模型记忆" in result.answer


def test_rules_endpoint_returns_answer_without_mutating_trip():
    repository = TripFlowService()
    trip = repository.create_trip(CreateTripInput(title="Rule API"))
    trip.reservations = [flight()]
    repository._save_changed_trip(trip, expected_version=trip.version)
    rules = TravelRulesService(FakeRetriever(), FakeAnswerer())
    app = FastAPI()
    app.include_router(build_tripflow_router(repository, rules_service=rules))
    api = TestClient(app)

    response = api.post(
        f"/api/trips/{trip.id}/rules/query",
        json={
            "reservation_id": trip.reservations[0].id,
            "question": "经济舱随身行李有什么限制？",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert repository.get_trip(trip.id).version == 2
