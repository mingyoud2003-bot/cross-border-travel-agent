from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal


TaskType = Literal["train", "mileage", "loyalty", "decision", "general"]
ConfirmationField = Literal["origin", "destination"]


CITY_ALIASES = {
    "伦敦": ("伦敦", "london"),
    "巴黎": ("巴黎", "paris"),
    "布鲁塞尔": ("布鲁塞尔", "brussels", "bruxelles"),
    "罗马": ("罗马", "rome"),
    "柏林": ("柏林", "berlin"),
    "马德里": ("马德里", "madrid"),
    "米兰": ("米兰", "milan"),
}

TRAIN_TERMS = ("火车", "铁路", "列车", "train", "eurostar")
MILEAGE_TERMS = (
    "avios",
    "里程",
    "积分",
    "兑换价值",
    "每点价值",
    "划算",
    "miles",
)
LOYALTY_TERMS = (
    "会员",
    "银卡",
    "金卡",
    "休息室",
    "宾客",
    "权益",
    "联盟",
    "oneworld",
    "star alliance",
    "skyteam",
    "qatar airways",
    "british airways",
    "lufthansa",
    "skywards",
    "skymiles",
    "flying blue",
    "ba silver",
    "ba gold",
)

AFFIRMATIVE_TERMS = {"是", "是的", "对", "对的", "确认", "没错", "正确", "yes", "y"}
NEGATIVE_TERMS = {"不是", "不对", "否", "取消", "no", "n"}
GENERAL_CHAT_TERMS = {"你好", "您好", "谢谢", "再见", "帮助", "help", "hello", "hi"}


@dataclass(frozen=True)
class SlotProvenance:
    """A confirmed value and the exact user turn that supplied it."""

    value: Any
    source: Literal["user"]
    turn_id: int
    source_text: str
    task: TaskType


@dataclass(frozen=True)
class StateEvent:
    event: Literal["task_started", "task_switched", "slot_set", "slot_updated"]
    turn_id: int
    task: TaskType
    field: str | None = None
    old_value: Any = None
    new_value: Any = None


@dataclass(frozen=True)
class PendingConfirmation:
    """A value-bearing user turn awaiting an explicit yes/no answer."""

    field: ConfirmationField
    value: str
    source_turn_id: int
    source_text: str


@dataclass
class TravelState:
    """User-grounded state for one conversation.

    Conversation history and business state deliberately remain separate. Every
    value that can authorize a tool call is stored with user provenance.
    """

    current_task: TaskType = "general"
    previous_task: TaskType | None = None
    current_user_message: str = ""
    turn_id: int = 0
    slots: dict[str, SlotProvenance] = field(default_factory=dict)
    events: list[StateEvent] = field(default_factory=list)
    tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_user_messages: list[str] = field(default_factory=list)
    loyalty_requested: bool = False
    pending_confirmation: PendingConfirmation | None = None
    control_message: str | None = None

    TRAIN_FIELDS = ("origin", "destination", "travel_date")
    MILEAGE_FIELDS = ("miles_required", "cash_price", "taxes")
    DECISION_FIELDS = TRAIN_FIELDS + MILEAGE_FIELDS

    @property
    def origin(self) -> str | None:
        return self.value("origin")

    @property
    def destination(self) -> str | None:
        return self.value("destination")

    @property
    def travel_date(self) -> str | None:
        return self.value("travel_date")

    @property
    def miles_required(self) -> int | None:
        return self.value("miles_required")

    @property
    def cash_price(self) -> float | None:
        return self.value("cash_price")

    @property
    def taxes(self) -> float | None:
        return self.value("taxes")

    def value(self, field_name: str) -> Any:
        item = self.slots.get(field_name)
        return item.value if item else None

    def provenance(self, field_name: str) -> SlotProvenance | None:
        return self.slots.get(field_name)

    def set_current_user_message(self, message: str) -> None:
        """Compatibility wrapper used by older callers."""

        self.begin_turn(message)

    def begin_turn(self, message: str) -> None:
        self.turn_id += 1
        self.current_user_message = message.strip()
        self.control_message = None

        detected_task = detect_task(self.current_user_message, self.current_task)
        self._transition_to(detected_task)

        if self._resolve_pending_confirmation(self.current_user_message):
            return

        if self.current_task == "train":
            self._capture_train_slots(self.current_user_message)
        elif self.current_task == "mileage":
            self._capture_mileage_slots(self.current_user_message)
        elif self.current_task == "decision":
            self.decision_user_messages.append(self.current_user_message)
            previously_requested = self.loyalty_requested
            self.loyalty_requested = self.loyalty_requested or any(
                term in self.current_user_message.casefold() for term in LOYALTY_TERMS
            )
            if self.loyalty_requested and not previously_requested:
                self.tool_results.pop("decision", None)
            self._capture_train_slots(self.current_user_message)
            self._capture_mileage_slots(self.current_user_message)

    def _transition_to(self, new_task: TaskType) -> None:
        if self.turn_id == 1:
            self.current_task = new_task
            self.events.append(StateEvent("task_started", self.turn_id, new_task))
            return

        if new_task == self.current_task:
            return

        self.previous_task = self.current_task
        self.current_task = new_task
        self.pending_confirmation = None

        # Returning to a task begins a fresh task instance. This prevents values
        # from an older request from silently authorizing a later tool call.
        self._clear_fields(self.fields_for_task(new_task))
        self.tool_results.clear()
        if new_task == "decision":
            self.decision_user_messages.clear()
            self.loyalty_requested = False
        self.events.append(StateEvent("task_switched", self.turn_id, new_task))

    def _clear_fields(self, fields: tuple[str, ...]) -> None:
        for field_name in fields:
            self.slots.pop(field_name, None)

    @classmethod
    def fields_for_task(cls, task: TaskType) -> tuple[str, ...]:
        if task == "train":
            return cls.TRAIN_FIELDS
        if task == "mileage":
            return cls.MILEAGE_FIELDS
        if task == "decision":
            return cls.DECISION_FIELDS
        return ()

    def set_confirmed(
        self,
        field_name: str,
        value: Any,
        *,
        source_turn_id: int | None = None,
        source_text: str | None = None,
    ) -> None:
        previous = self.slots.get(field_name)
        event = "slot_updated" if previous and previous.value != value else "slot_set"
        if previous is None or previous.value != value:
            self._invalidate_results_for(field_name)
        self.slots[field_name] = SlotProvenance(
            value=value,
            source="user",
            turn_id=source_turn_id or self.turn_id,
            source_text=source_text or self.current_user_message,
            task=self.current_task,
        )
        self.events.append(
            StateEvent(
                event=event,
                turn_id=self.turn_id,
                task=self.current_task,
                field=field_name,
                old_value=previous.value if previous else None,
                new_value=value,
            )
        )

    def _invalidate_results_for(self, field_name: str) -> None:
        if field_name in self.TRAIN_FIELDS:
            self.tool_results.pop("train", None)
        if field_name in self.MILEAGE_FIELDS:
            self.tool_results.pop("mileage", None)
        self.tool_results.pop("decision", None)

    def record_tool_result(self, name: str, result: dict[str, Any]) -> None:
        """Record an attempted dependency so partial failures can be composed."""

        self.tool_results[name] = result
        if name != "decision":
            self.tool_results.pop("decision", None)

    def decision_loyalty_query(self) -> str:
        """Return only user-authored text for retrieval; never model expansion."""

        return "\n".join(self.decision_user_messages).strip()

    def decision_dependencies_ready(self) -> bool:
        required = {"train", "mileage"}
        if self.loyalty_requested:
            required.add("loyalty")
        return self.current_task == "decision" and required.issubset(self.tool_results)

    def _capture_train_slots(self, message: str) -> None:
        explicit_route = extract_explicit_route(message)
        if explicit_route:
            origin, destination = explicit_route
            self.set_confirmed("origin", origin)
            self.set_confirmed("destination", destination)
            self.pending_confirmation = None

        labeled_origin = extract_labeled_city(message, "origin")
        labeled_destination = extract_labeled_city(message, "destination")
        if labeled_origin:
            self.set_confirmed("origin", labeled_origin)
            self.pending_confirmation = None
        if labeled_destination:
            self.set_confirmed("destination", labeled_destination)
            self.pending_confirmation = None

        city_mentions = extract_city_mentions(message)

        if explicit_route or labeled_origin or labeled_destination:
            pass
        elif len(city_mentions) >= 2:
            self.set_confirmed("origin", city_mentions[0][1])
            self.set_confirmed("destination", city_mentions[1][1])
        elif len(city_mentions) == 1:
            _, city, start, end = city_mentions[0]
            prefix = message[:start].casefold()
            suffix = message[end:].casefold()
            if any(marker in prefix for marker in ("目的地", "终点", "改去", "改到")):
                self.set_confirmed("destination", city)
            elif any(marker in prefix for marker in ("出发地", "起点")):
                self.set_confirmed("origin", city)
            elif re.search(r"(?:从|由)\s*$", prefix) or re.match(
                r"\s*(?:出发|启程)", suffix
            ):
                self.set_confirmed("origin", city)
            elif re.search(r"(?:去|到|前往|回)\s*$", prefix):
                self.set_confirmed("destination", city)
            elif self.origin is None and self.destination is not None:
                self.set_confirmed("origin", city)
            elif self.destination is None:
                self.set_confirmed("destination", city)

        if not explicit_route and not labeled_origin and not labeled_destination and not city_mentions:
            standalone = extract_standalone_city(message)
            if standalone:
                self._capture_standalone_city(standalone)

        explicit_dates = extract_explicit_dates(message)
        if len(explicit_dates) == 1:
            self.set_confirmed("travel_date", next(iter(explicit_dates)))

    def _capture_standalone_city(self, city: str) -> None:
        missing = [name for name in ("origin", "destination") if self.value(name) is None]
        if len(missing) == 1:
            self.set_confirmed(missing[0], city)
            return
        if len(missing) == 2:
            self.control_message = (
                f"请明确“{city}”是出发城市还是目的城市，例如“出发地是{city}”。"
            )
            return

        matches_origin = _city_values_overlap(city, self.origin)
        matches_destination = _city_values_overlap(city, self.destination)
        if matches_origin != matches_destination:
            target: ConfirmationField = "origin" if matches_origin else "destination"
            self.pending_confirmation = PendingConfirmation(
                field=target,
                value=city,
                source_turn_id=self.turn_id,
                source_text=self.current_user_message,
            )
            return
        self.control_message = (
            f"请明确要把出发城市还是目的城市改为“{city}”，例如“目的地改为{city}”。"
        )

    def _resolve_pending_confirmation(self, message: str) -> bool:
        normalized = _normalize_short_answer(message)
        affirmative = normalized in AFFIRMATIVE_TERMS
        negative = normalized in NEGATIVE_TERMS
        if not affirmative and not negative:
            return False

        pending = self.pending_confirmation
        if pending is None:
            if self.current_task in {"train", "mileage", "decision"}:
                self.control_message = (
                    "当前没有待确认的候选值，请直接提供要修改的字段和值。"
                )
                return True
            return False

        self.pending_confirmation = None
        label = "出发城市" if pending.field == "origin" else "目的城市"
        if negative:
            self.control_message = f"已取消将{label}改为“{pending.value}”，请直接提供正确值。"
            return True

        self.set_confirmed(
            pending.field,
            pending.value,
            source_turn_id=pending.source_turn_id,
            source_text=pending.source_text,
        )
        return True

    def _capture_mileage_slots(self, message: str) -> None:
        normalized = message.casefold().replace(",", "")

        miles = _first_number(
            normalized,
            (
                r"(\d+(?:\.\d+)?)\s*(?:avios|miles|里程|积分)",
                r"(?:需要|要|兑换要|兑换需要)\s*(\d+(?:\.\d+)?)\s*(?:avios|miles|里程|积分)",
            ),
        )
        if miles is not None and miles.is_integer():
            self.set_confirmed("miles_required", int(miles))

        taxes = _first_number(
            normalized,
            (
                r"(?:税费|税|taxes?|附加费|surcharge)\s*(?:改成|改为|为|是|要|需|需要|[:：])?\s*[£￥$]?\s*(\d+(?:\.\d+)?)",
                r"[£￥$]?\s*(\d+(?:\.\d+)?)\s*(?:英镑|gbp|元|美元)?\s*(?:税费|税|taxes?|附加费|surcharge)",
            ),
        )
        if taxes is None and re.search(r"(?:没有|无|不收|免)\s*(?:税费|税|附加费)", normalized):
            taxes = 0.0
        if taxes is not None:
            self.set_confirmed("taxes", taxes)

        cash = _first_number(
            normalized,
            (
                r"(?:现金票价|现金价|现金票|现金|cash price|cash|票价|价格)\s*(?:改成|改为|为|是|要|需|需要|[:：])?\s*[£￥$]?\s*(\d+(?:\.\d+)?)",
                r"[£￥$]\s*(\d+(?:\.\d+)?)\s*(?:现金|cash)?",
                r"(\d+(?:\.\d+)?)\s*(?:英镑|gbp|元|美元)\s*(?:现金票|现金价|现金)",
            ),
        )
        if cash is not None:
            self.set_confirmed("cash_price", cash)

    def missing_fields(self, task: TaskType | None = None) -> list[str]:
        active_task = task or self.current_task
        return [
            field_name
            for field_name in self.fields_for_task(active_task)
            if self.value(field_name) is None
        ]

    def is_complete(self, task: TaskType | None = None) -> bool:
        fields = self.fields_for_task(task or self.current_task)
        return bool(fields) and not self.missing_fields(task)

    def validation_errors(self, task: TaskType | None = None) -> list[str]:
        active_task = task or self.current_task
        if not self.is_complete(active_task):
            return []
        errors: list[str] = []
        if active_task in {"train", "decision"}:
            if self.origin == self.destination:
                errors.append("出发城市和目的城市不能相同")
            try:
                parsed_date = date.fromisoformat(str(self.travel_date))
            except ValueError:
                return ["出发日期格式无效"]
            if parsed_date < date.today():
                errors.append("出发日期不能是过去日期")
        if active_task in {"mileage", "decision"}:
            if self.miles_required is not None and self.miles_required <= 0:
                errors.append("所需里程必须大于零")
            if self.cash_price is not None and self.cash_price < 0:
                errors.append("现金票价不能为负数")
            if self.taxes is not None and self.taxes < 0:
                errors.append("税费不能为负数")
            if (
                self.cash_price is not None
                and self.taxes is not None
                and self.taxes > self.cash_price
            ):
                errors.append("税费不能高于现金票价")
        return errors

    def is_actionable(self, task: TaskType | None = None) -> bool:
        active_task = task or self.current_task
        return self.is_complete(active_task) and not self.validation_errors(active_task)

    def update_train_trip(self, origin: str, destination: str, travel_date: str) -> None:
        """Keep compatibility without replacing the original provenance."""

        if not self.is_complete("train"):
            raise ValueError("Train state must be confirmed before execution.")
        if (origin, destination, travel_date) != (
            self.origin,
            self.destination,
            self.travel_date,
        ):
            raise ValueError("Tool arguments do not match confirmed train state.")

    def prompt_context(self) -> str:
        labels = {
            "origin": "出发城市",
            "destination": "目的城市",
            "travel_date": "出发日期",
            "miles_required": "所需里程",
            "cash_price": "现金票价",
            "taxes": "税费",
        }
        relevant = self.fields_for_task(self.current_task)
        confirmed = [
            f"{labels[name]}={self.value(name)}（用户第 {self.provenance(name).turn_id} 轮明确提供）"
            for name in relevant
            if self.provenance(name)
        ]
        missing = [labels[name] for name in self.missing_fields()]
        errors = self.validation_errors()
        base = (
            f"当前任务：{self.current_task}\n"
            f"已确认参数：{'; '.join(confirmed) if confirmed else '无'}\n"
            f"缺失参数：{'; '.join(missing) if missing else '无'}\n"
            f"参数错误：{'; '.join(errors) if errors else '无'}"
        )
        if self.current_task == "decision":
            statuses = "; ".join(
                f"{name}={result.get('status', 'recorded')}"
                for name, result in self.tool_results.items()
            )
            pending = self._decision_pending_action(missing, errors)
            return (
                f"{base}\n"
                f"需要常旅客证据：{'是' if self.loyalty_requested else '否'}\n"
                f"依赖工具结果：{statuses or '无'}\n"
                f"必须执行的下一步：{pending}"
            )
        return base

    def _decision_pending_action(
        self, missing_labels: list[str], errors: list[str]
    ) -> str:
        """Expose the deterministic workflow edge to the model each step."""

        if missing_labels:
            return f"仅追问缺失参数：{'; '.join(missing_labels)}"
        if errors:
            return f"仅要求用户修正参数：{'; '.join(errors)}"
        if "train" not in self.tool_results:
            return "立即调用 search_train；禁止复用历史回答中的旧查询结果"
        if "mileage" not in self.tool_results:
            return "立即调用 calculate_mileage_value；禁止自行复算或复用旧数值"
        if self.loyalty_requested and "loyalty" not in self.tool_results:
            return "立即调用 retrieve_loyalty_benefits；禁止用模型记忆回答"
        if "decision" not in self.tool_results:
            return "立即调用 compose_travel_decision；不得自行撰写推荐"
        return "返回 compose_travel_decision 的原始 JSON"

    def summary(self) -> str:
        return self.prompt_context().replace("\n", ", ")

    def snapshot(self) -> dict[str, Any]:
        return {
            "current_task": self.current_task,
            "previous_task": self.previous_task,
            "turn_id": self.turn_id,
            "slots": {name: asdict(value) for name, value in self.slots.items()},
            "missing_fields": self.missing_fields(),
            "validation_errors": self.validation_errors(),
            "events": [asdict(event) for event in self.events],
            "tool_results": self.tool_results,
            "loyalty_requested": self.loyalty_requested,
            "pending_confirmation": (
                asdict(self.pending_confirmation) if self.pending_confirmation else None
            ),
        }

    def to_persistence_dict(self) -> dict[str, Any]:
        """Serialize all state needed to continue a conversation after restart."""

        return {
            "current_task": self.current_task,
            "previous_task": self.previous_task,
            "current_user_message": self.current_user_message,
            "turn_id": self.turn_id,
            "slots": {name: asdict(value) for name, value in self.slots.items()},
            "events": [asdict(event) for event in self.events],
            "tool_results": self.tool_results,
            "decision_user_messages": self.decision_user_messages,
            "loyalty_requested": self.loyalty_requested,
            "pending_confirmation": (
                asdict(self.pending_confirmation) if self.pending_confirmation else None
            ),
        }

    @classmethod
    def from_persistence_dict(cls, payload: dict[str, Any]) -> "TravelState":
        """Restore trusted application-owned state from SQLite."""

        pending = payload.get("pending_confirmation")
        return cls(
            current_task=payload.get("current_task", "general"),
            previous_task=payload.get("previous_task"),
            current_user_message=payload.get("current_user_message", ""),
            turn_id=int(payload.get("turn_id", 0)),
            slots={
                name: SlotProvenance(**item)
                for name, item in payload.get("slots", {}).items()
            },
            events=[StateEvent(**item) for item in payload.get("events", [])],
            tool_results=payload.get("tool_results", {}),
            decision_user_messages=payload.get("decision_user_messages", []),
            loyalty_requested=bool(payload.get("loyalty_requested", False)),
            pending_confirmation=(PendingConfirmation(**pending) if pending else None),
        )


def detect_task(message: str, current_task: TaskType = "general") -> TaskType:
    normalized = message.casefold()
    if _normalize_short_answer(message) in GENERAL_CHAT_TERMS:
        return "general"
    if any(term in normalized for term in ("写一首", "小诗", "写诗", "故事")):
        return "general"
    if "积分" in normalized and any(
        term in normalized for term in ("数学", "微积分", "是什么意思")
    ):
        return "general"
    scores = {
        "train": sum(term in normalized for term in TRAIN_TERMS),
        "mileage": sum(term in normalized for term in MILEAGE_TERMS),
        "loyalty": sum(term in normalized for term in LOYALTY_TERMS),
    }
    has_route = bool(extract_explicit_route(message)) or len(extract_city_mentions(message)) >= 2
    comparison_intent = any(
        term in normalized
        for term in ("怎么选", "选哪个", "应该选", "推荐", "比较", "对比", "现金还是")
    )
    active_domains = sum(score > 0 for score in scores.values())
    if (has_route and scores["mileage"] > 0 and comparison_intent) or (
        active_domains >= 2 and comparison_intent
    ):
        return "decision"

    # A short completion or correction remains part of an active decision.
    if current_task == "decision" and not any(
        marker in normalized for marker in ("另一个问题", "另外帮我", "改问", "只查")
    ):
        if any(score > 0 for score in scores.values()) or re.search(r"\d", normalized):
            return "decision"
    best_task = max(scores, key=scores.get)
    if scores[best_task] > 0:
        return best_task  # type: ignore[return-value]

    # Short answers and corrections belong to the active structured task.
    if current_task in {"train", "mileage", "decision"}:
        if _normalize_short_answer(message) in AFFIRMATIVE_TERMS | NEGATIVE_TERMS:
            return current_task
        if current_task in {"train", "decision"} and (
            extract_explicit_route(message)
            or extract_labeled_city(message, "origin")
            or extract_labeled_city(message, "destination")
            or extract_standalone_city(message)
        ):
            return current_task
        if extract_city_mentions(message) or extract_explicit_dates(message):
            return current_task
        if re.search(r"\d", normalized) or any(
            marker in normalized for marker in ("改成", "改为", "不是", "更正", "税")
        ):
            return current_task

    return "general"


def extract_explicit_dates(message: str) -> set[str]:
    dates: set[str] = set()
    patterns = (
        r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)",
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
    )
    for pattern in patterns:
        for year, month, day in re.findall(pattern, message):
            try:
                dates.add(date(int(year), int(month), int(day)).isoformat())
            except ValueError:
                continue
    return dates


def extract_city_mentions(message: str) -> list[tuple[str, str, int, int]]:
    normalized = message.casefold()
    mentions: list[tuple[str, str, int, int]] = []
    occupied: list[tuple[int, int]] = []
    aliases = sorted(
        (
            (alias.casefold(), canonical)
            for canonical, values in CITY_ALIASES.items()
            for alias in values
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, canonical in aliases:
        for match in re.finditer(re.escape(alias), normalized):
            span = match.span()
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            mentions.append((message[span[0] : span[1]], canonical, span[0], span[1]))
            occupied.append(span)
    return sorted(mentions, key=lambda item: item[2])


def extract_explicit_route(message: str) -> tuple[str, str] | None:
    """Extract user-authored city text only when a route relation is explicit."""

    patterns = (
        r"(?:从|由)\s*([^，。！？,!?]{1,40}?)\s*(?:到|去|前往)\s*([^，。！？,!?]{1,40}?)(?=(?:有|坐|乘坐|搭乘)?(?:的)?(?:火车|铁路|列车)|[，。！？,.!?]|$)",
        r"([^，。！？,!?]{1,40}?)\s*(?:→|->|⇒)\s*([^，。！？,!?]{1,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if not match:
            continue
        origin = _clean_city_candidate(match.group(1))
        destination = _clean_city_candidate(match.group(2))
        if origin and destination:
            return canonicalize_city(origin), canonicalize_city(destination)
    return None


def extract_labeled_city(message: str, field_name: ConfirmationField) -> str | None:
    labels = (
        r"(?:出发城市|出发地|起点)"
        if field_name == "origin"
        else r"(?:目的城市|目标城市|目的地|终点)"
    )
    pattern = rf"{labels}\s*(?:改成|改为|是|为|[:：])?\s*([\u4e00-\u9fff]{{2,12}}|[A-Za-z][A-Za-z .'-]{{1,30}})"
    match = re.search(pattern, message, flags=re.IGNORECASE)
    if not match:
        return None
    candidate = _clean_city_candidate(match.group(1))
    return canonicalize_city(candidate) if candidate else None


def extract_standalone_city(message: str) -> str | None:
    candidate = message.strip().strip("'\"“”‘’。，,!?！？ ")
    # A short command can look like an unknown Chinese city to the generic
    # fallback. Keep action/query phrases out of slot memory.
    if any(
        term in candidate.casefold()
        for term in (
            "火车",
            "铁路",
            "列车",
            "查询",
            "帮我查",
            "再帮我",
            "train",
            "rail",
            "search",
        )
    ):
        return None
    if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", candidate) or re.fullmatch(
        r"[A-Za-z][A-Za-z .'-]{1,30}", candidate
    ):
        if _normalize_short_answer(candidate) not in AFFIRMATIVE_TERMS | NEGATIVE_TERMS:
            return canonicalize_city(candidate)
    return None


def canonicalize_city(value: str) -> str:
    normalized = value.casefold().strip()
    for canonical, aliases in CITY_ALIASES.items():
        if normalized in {alias.casefold() for alias in aliases}:
            return canonical
    return value.strip()


def _clean_city_candidate(value: str) -> str:
    cleaned = value.strip().strip("'\"“”‘’ ")
    cleaned = re.sub(
        r"^(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?|\d{1,2}月\d{1,2}日)[\s，,]*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"^(?:更改为|改为|改成|路线|行程)\s*", "", cleaned)
    cleaned = re.sub(r"(?:有)?(?:的)?(?:火车|铁路|列车|路线|行程)$", "", cleaned)
    return cleaned.strip()


def _city_values_overlap(candidate: str, existing: str | None) -> bool:
    if not existing:
        return False
    left = candidate.casefold().replace(" ", "")
    right = existing.casefold().replace(" ", "")
    return left in right or right in left


def _normalize_short_answer(message: str) -> str:
    return message.casefold().strip().strip("。.!！?？ ")


def _first_number(message: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None
