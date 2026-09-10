from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from state import TravelState


class EvidenceItem(BaseModel):
    type: Literal["transport_provider", "calculation", "knowledge"]
    id: str
    claim: str
    source: str
    source_url: str | None = None


class Recommendation(BaseModel):
    choice: Literal["award_ticket", "cash_ticket", "conditional"]
    summary: str
    confidence: Literal["high", "medium", "low"]
    reason_codes: list[str] = Field(default_factory=list)
    policy: str = "redemption-value-v1"


class TravelDecisionOutput(BaseModel):
    request_summary: dict[str, Any]
    transport_options: list[dict[str, Any]]
    redemption_value: dict[str, Any]
    loyalty_benefits: list[dict[str, Any]]
    recommendation: Recommendation
    tradeoffs: list[str]
    evidence: list[EvidenceItem]
    limitations: list[str]


def compose_decision(state: TravelState) -> TravelDecisionOutput:
    """Build a deterministic, evidence-linked decision from recorded tool results."""

    if not state.decision_dependencies_ready():
        raise ValueError("Decision dependencies are incomplete.")

    train = state.tool_results["train"]
    mileage = state.tool_results["mileage"]
    loyalty = state.tool_results.get(
        "loyalty",
        {"status": "not_requested", "evidence": []},
    )

    transport_options = list(train.get("journeys") or [])
    loyalty_evidence = list(loyalty.get("evidence") or [])
    loyalty_benefits = [
        {
            "evidence_id": item["id"],
            "title": item["title"],
            "benefit": item["content"],
            "source": item["source"],
            "source_url": item["source_url"],
        }
        for item in loyalty_evidence
    ]

    pence_per_mile = float(mileage["pence_per_mile"])
    if pence_per_mile >= 1.5:
        choice = "award_ticket"
        summary = "按当前输入，奖励票的单位里程价值较高，优先考虑奖励票。"
        reason_codes = ["redemption_value_at_least_1_5_ppm"]
    elif pence_per_mile < 1.0:
        choice = "cash_ticket"
        summary = "按当前输入，奖励票的单位里程价值偏低，优先考虑现金票。"
        reason_codes = ["redemption_value_below_1_0_ppm"]
    else:
        choice = "conditional"
        summary = "当前单位里程价值处于中间区间，应结合里程余额与改退需求选择。"
        reason_codes = ["redemption_value_between_1_0_and_1_5_ppm"]

    successful_dependencies = sum(
        result.get("status") == "success" for result in (train, mileage, loyalty)
    )
    expected_dependencies = 3 if state.loyalty_requested else 2
    confidence: Literal["high", "medium", "low"] = (
        "high" if successful_dependencies == expected_dependencies else "medium"
    )

    limitations: list[str] = []
    if train.get("status") == "provider_error":
        limitations.append("铁路数据源暂不可用，推荐未纳入具体铁路班次。")
    elif train.get("status") == "no_results":
        limitations.append("铁路数据源未返回班次，这不代表客观上没有可售行程。")
    else:
        limitations.extend(
            [
                "铁路结果是计划时刻；realtime=false 不代表实时运行状态。",
                "铁路数据源不提供票价，未进行铁路票价与机票价格比较。",
            ]
        )
    if loyalty.get("status") in {"provider_error", "no_evidence", "out_of_scope"}:
        limitations.append("常旅客权益证据不足或不可用，推荐不依赖未证实权益。")
    if loyalty_benefits:
        limitations.append("常旅客权益受承运航司、营销航司、舱等及现场规则限制。")

    evidence: list[EvidenceItem] = [
        EvidenceItem(
            type="calculation",
            id="redemption_formula_v1",
            claim=(
                f"({state.cash_price} - {state.taxes}) / {state.miles_required} × 100 "
                f"= {pence_per_mile} pence_per_mile"
            ),
            source="deterministic calculator",
        )
    ]
    if train.get("status") == "success":
        evidence.append(
            EvidenceItem(
                type="transport_provider",
                id="transitous_schedule",
                claim=f"返回 {len(transport_options)} 个计划铁路行程。",
                source=str(train.get("source", "Transitous")),
                source_url="https://transitous.org/",
            )
        )
    evidence.extend(
        EvidenceItem(
            type="knowledge",
            id=item["id"],
            claim=item["content"],
            source=item["source"],
            source_url=item["source_url"],
        )
        for item in loyalty_evidence
    )

    return TravelDecisionOutput(
        request_summary={
            "origin": state.origin,
            "destination": state.destination,
            "travel_date": state.travel_date,
            "cash_price": state.cash_price,
            "miles_required": state.miles_required,
            "taxes": state.taxes,
            "loyalty_context_requested": state.loyalty_requested,
        },
        transport_options=transport_options,
        redemption_value=mileage,
        loyalty_benefits=loyalty_benefits,
        recommendation=Recommendation(
            choice=choice,
            summary=summary,
            confidence=confidence,
            reason_codes=reason_codes,
        ),
        tradeoffs=[
            f"奖励票可节省的现金净额为 {state.cash_price - state.taxes:.2f}。",
            f"奖励票消耗 {state.miles_required} 里程并仍需支付 {state.taxes:.2f} 税费。",
            "现金票保留里程；奖励票的改退与余票规则需另行核实。",
        ],
        evidence=evidence,
        limitations=limitations,
    )
