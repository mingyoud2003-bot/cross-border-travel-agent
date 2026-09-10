from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from agents import Runner, SQLiteSession
from openai import APIConnectionError, APIError, RateLimitError

from agent import run_config_for, travel_agent
from metrics import MetricsRegistry
from observability import tool_timeline, usage_snapshot
from settings import session_db_path
from state import TravelState


RunFunction = Callable[..., Awaitable[Any]]
DECISION_KEYS = {
    "request_summary",
    "transport_options",
    "redemption_value",
    "loyalty_benefits",
    "recommendation",
    "tradeoffs",
    "evidence",
    "limitations",
}


class SessionNotFoundError(KeyError):
    pass


class AgentUnavailableError(RuntimeError):
    pass


@dataclass
class SessionRecord:
    id: str
    state: TravelState
    memory: SQLiteSession
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    traces: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=30))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_accessed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionStore:
    """SQLite-backed business state paired with the SDK's SQLite memory."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        max_sessions: int = 200,
        ttl_hours: int = 6,
    ) -> None:
        self.db_path = str(db_path or session_db_path())
        self.max_sessions = max_sessions
        self.ttl = timedelta(hours=ttl_hours)
        self._sessions: dict[str, SessionRecord] = {}
        self._db_lock = threading.RLock()
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys=ON")
        if self.db_path != ":memory:":
            self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_sessions (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                trace_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES app_sessions(session_id)
                    ON DELETE CASCADE
            )
            """
        )
        self._connection.commit()

    def create(self) -> SessionRecord:
        self._prune()
        with self._db_lock:
            count = self._connection.execute(
                "SELECT COUNT(*) FROM app_sessions"
            ).fetchone()[0]
            if count >= self.max_sessions:
                oldest = self._connection.execute(
                    "SELECT session_id FROM app_sessions ORDER BY last_accessed_at ASC LIMIT 1"
                ).fetchone()
                if oldest:
                    self._delete_locked(oldest[0])
        session_id = uuid4().hex
        now = datetime.now(UTC)
        record = SessionRecord(
            id=session_id,
            state=TravelState(),
            memory=self._sdk_memory(session_id),
            created_at=now,
            last_accessed_at=now,
        )
        self._sessions[session_id] = record
        with self._db_lock:
            self._connection.execute(
                "INSERT INTO app_sessions VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    self._state_json(record.state),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._connection.commit()
        return record

    def get(self, session_id: str) -> SessionRecord:
        self._prune()
        record = self._sessions.get(session_id)
        if record is None:
            with self._db_lock:
                row = self._connection.execute(
                    """
                    SELECT state_json, created_at, last_accessed_at
                    FROM app_sessions WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()
                trace_rows = self._connection.execute(
                    """
                    SELECT trace_json FROM app_traces
                    WHERE session_id = ? ORDER BY id DESC LIMIT 30
                    """,
                    (session_id,),
                ).fetchall()
            if row is None:
                raise SessionNotFoundError(session_id)
            record = SessionRecord(
                id=session_id,
                state=TravelState.from_persistence_dict(json.loads(row[0])),
                memory=self._sdk_memory(session_id),
                traces=deque(
                    (json.loads(item[0]) for item in reversed(trace_rows)), maxlen=30
                ),
                created_at=datetime.fromisoformat(row[1]),
                last_accessed_at=datetime.fromisoformat(row[2]),
            )
            self._sessions[session_id] = record
        record.last_accessed_at = datetime.now(UTC)
        return record

    def delete(self, session_id: str) -> bool:
        with self._db_lock:
            exists = self._connection.execute(
                "SELECT 1 FROM app_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if exists:
                self._delete_locked(session_id)
                self._connection.commit()
        record = self._sessions.pop(session_id, None)
        if record:
            record.memory.close()
        return exists is not None

    def save(self, record: SessionRecord, trace: dict[str, Any]) -> None:
        with self._db_lock:
            self._connection.execute(
                """
                UPDATE app_sessions
                SET state_json = ?, last_accessed_at = ?
                WHERE session_id = ?
                """,
                (
                    self._state_json(record.state),
                    record.last_accessed_at.isoformat(),
                    record.id,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO app_traces(session_id, trace_json, created_at)
                VALUES (?, ?, ?)
                """,
                (record.id, json.dumps(trace, ensure_ascii=False), trace["created_at"]),
            )
            self._connection.execute(
                """
                DELETE FROM app_traces WHERE session_id = ? AND id NOT IN (
                    SELECT id FROM app_traces WHERE session_id = ?
                    ORDER BY id DESC LIMIT 30
                )
                """,
                (record.id, record.id),
            )
            self._connection.commit()

    def ready(self) -> bool:
        try:
            with self._db_lock:
                self._connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        for record in self._sessions.values():
            record.memory.close()
        self._sessions.clear()
        self._connection.close()

    def _prune(self) -> None:
        cutoff = (datetime.now(UTC) - self.ttl).isoformat()
        with self._db_lock:
            expired = self._connection.execute(
                "SELECT session_id FROM app_sessions WHERE last_accessed_at < ?",
                (cutoff,),
            ).fetchall()
            for (session_id,) in expired:
                self._delete_locked(session_id)
            if expired:
                self._connection.commit()

    def _delete_locked(self, session_id: str) -> None:
        record = self._sessions.pop(session_id, None)
        if record:
            record.memory.close()
        self._connection.execute(
            "DELETE FROM app_sessions WHERE session_id = ?", (session_id,)
        )
        # SDK tables may not exist until the first model turn.
        for table in ("agent_messages", "agent_sessions"):
            try:
                self._connection.execute(
                    f"DELETE FROM {table} WHERE session_id = ?",  # noqa: S608
                    (f"web-{session_id}",),
                )
            except sqlite3.OperationalError:
                pass

    def _sdk_memory(self, session_id: str) -> SQLiteSession:
        return SQLiteSession(f"web-{session_id}", self.db_path)

    @staticmethod
    def _state_json(state: TravelState) -> str:
        return json.dumps(state.to_persistence_dict(), ensure_ascii=False)


class AgentService:
    def __init__(
        self,
        *,
        store: SessionStore | None = None,
        run_agent: RunFunction | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self.store = store or SessionStore()
        self._run_agent = run_agent or Runner.run
        self.metrics = metrics

    def create_session(self) -> dict[str, Any]:
        record = self.store.create()
        return self.session_snapshot(record)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.session_snapshot(self.store.get(session_id))

    def delete_session(self, session_id: str) -> bool:
        return self.store.delete(session_id)

    def ready(self) -> bool:
        return self.store.ready()

    async def chat(
        self,
        message: str,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.store.get(session_id) if session_id else self.store.create()
        request_id = request_id or uuid4().hex

        async with record.lock:
            started = time.perf_counter()
            rollback_state = copy.deepcopy(record.state)
            state_before = rollback_state.snapshot()
            record.state.begin_turn(message)
            try:
                result = await self._run_agent(
                    travel_agent,
                    message,
                    session=record.memory,
                    context=record.state,
                    max_turns=12,
                    run_config=run_config_for(record.state),
                )
            except (APIConnectionError, RateLimitError, APIError) as exc:
                record.state = rollback_state
                self._observe_failure(record.state.current_task, started, "provider_error")
                raise AgentUnavailableError(
                    "模型服务暂时不可用，请稍后重试；本轮不会生成猜测结果。"
                ) from exc
            except Exception:
                record.state = rollback_state
                self._observe_failure(record.state.current_task, started, "internal_error")
                raise

            duration_ms = round((time.perf_counter() - started) * 1000)
            final_output = str(result.final_output or "")
            decision = _parse_decision(final_output)
            trace = {
                "request_id": request_id,
                "created_at": datetime.now(UTC).isoformat(),
                "duration_ms": duration_ms,
                "task": record.state.current_task,
                "tools": tool_timeline(result.new_items),
                "usage": usage_snapshot(result),
                "state_before": state_before,
                "state_after": record.state.snapshot(),
            }
            record.traces.append(trace)
            record.last_accessed_at = datetime.now(UTC)
            self.store.save(record, trace)
            if self.metrics:
                self.metrics.observe_agent(
                    task=record.state.current_task,
                    status="success",
                    duration_seconds=duration_ms / 1000,
                    tool_names=[item["tool"] for item in trace["tools"]],
                    usage=trace["usage"],
                )
            return {
                "request_id": request_id,
                "session_id": record.id,
                "answer_type": "decision" if decision else "text",
                "message": (
                    decision["recommendation"]["summary"] if decision else final_output
                ),
                "decision": decision,
                "state": record.state.snapshot(),
                "trace": _public_trace(trace),
            }

    def _observe_failure(self, task: str, started: float, status: str) -> None:
        if self.metrics:
            self.metrics.observe_agent(
                task=task,
                status=status,
                duration_seconds=time.perf_counter() - started,
            )

    @staticmethod
    def session_snapshot(record: SessionRecord) -> dict[str, Any]:
        return {
            "session_id": record.id,
            "created_at": record.created_at.isoformat(),
            "state": record.state.snapshot(),
            "traces": [_public_trace(item) for item in record.traces],
        }


def _parse_decision(output: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and set(parsed) == DECISION_KEYS:
        return parsed
    return None


def _public_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        key: trace[key]
        for key in ("request_id", "created_at", "duration_ms", "task", "tools", "usage")
    }
