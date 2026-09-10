from __future__ import annotations

import threading
import time
from collections import defaultdict


HTTP_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
AGENT_BUCKETS = (0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0)


class MetricsRegistry:
    """Bounded, dependency-free Prometheus metrics for a single worker."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._http_requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._http_duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self._http_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._http_duration_buckets: dict[tuple[str, str, float], int] = defaultdict(int)
        self._agent_runs: dict[tuple[str, str], int] = defaultdict(int)
        self._agent_duration_count: dict[str, int] = defaultdict(int)
        self._agent_duration_sum: dict[str, float] = defaultdict(float)
        self._agent_duration_buckets: dict[tuple[str, float], int] = defaultdict(int)
        self._agent_tool_calls: dict[tuple[str, str], int] = defaultdict(int)
        self._agent_tokens: dict[tuple[str, str], int] = defaultdict(int)

    def observe_http(
        self, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        key = (method, route)
        with self._lock:
            self._http_requests[(method, route, status_code)] += 1
            self._http_duration_count[key] += 1
            self._http_duration_sum[key] += duration_seconds
            for bucket in HTTP_BUCKETS:
                if duration_seconds <= bucket:
                    self._http_duration_buckets[(method, route, bucket)] += 1

    def observe_agent(
        self,
        *,
        task: str,
        status: str,
        duration_seconds: float,
        tool_names: list[str] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        with self._lock:
            self._agent_runs[(task, status)] += 1
            self._agent_duration_count[task] += 1
            self._agent_duration_sum[task] += duration_seconds
            for bucket in AGENT_BUCKETS:
                if duration_seconds <= bucket:
                    self._agent_duration_buckets[(task, bucket)] += 1
            for name in tool_names or []:
                self._agent_tool_calls[(task, name)] += 1
            for token_type in ("input_tokens", "output_tokens", "total_tokens"):
                self._agent_tokens[(task, token_type)] += int((usage or {}).get(token_type, 0))

    def render(self) -> str:
        with self._lock:
            lines = [
                "# HELP travel_agent_uptime_seconds Process uptime.",
                "# TYPE travel_agent_uptime_seconds gauge",
                f"travel_agent_uptime_seconds {time.monotonic() - self.started_at:.3f}",
                "# HELP travel_agent_http_requests_total HTTP requests by bounded route.",
                "# TYPE travel_agent_http_requests_total counter",
            ]
            for (method, route, status), count in sorted(self._http_requests.items()):
                lines.append(
                    f'travel_agent_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}'
                )
            lines.extend(
                self._render_http_histogram()
                + [
                    "# HELP travel_agent_runs_total Agent runs by task and outcome.",
                    "# TYPE travel_agent_runs_total counter",
                ]
            )
            for (task, status), count in sorted(self._agent_runs.items()):
                lines.append(
                    f'travel_agent_runs_total{{task="{task}",status="{status}"}} {count}'
                )
            lines.extend(self._render_agent_histogram())
            lines.extend(
                [
                    "# HELP travel_agent_tool_calls_total Business tool calls.",
                    "# TYPE travel_agent_tool_calls_total counter",
                ]
            )
            for (task, tool), count in sorted(self._agent_tool_calls.items()):
                lines.append(
                    f'travel_agent_tool_calls_total{{task="{task}",tool="{tool}"}} {count}'
                )
            lines.extend(
                [
                    "# HELP travel_agent_tokens_total Model tokens reported by the SDK.",
                    "# TYPE travel_agent_tokens_total counter",
                ]
            )
            for (task, token_type), count in sorted(self._agent_tokens.items()):
                lines.append(
                    f'travel_agent_tokens_total{{task="{task}",type="{token_type}"}} {count}'
                )
        return "\n".join(lines) + "\n"

    def _render_http_histogram(self) -> list[str]:
        lines = [
            "# HELP travel_agent_http_request_duration_seconds HTTP request latency.",
            "# TYPE travel_agent_http_request_duration_seconds histogram",
        ]
        for method, route in sorted(self._http_duration_count):
            for bucket in HTTP_BUCKETS:
                count = self._http_duration_buckets[(method, route, bucket)]
                lines.append(
                    f'travel_agent_http_request_duration_seconds_bucket{{method="{method}",route="{route}",le="{bucket}"}} {count}'
                )
            count = self._http_duration_count[(method, route)]
            total = self._http_duration_sum[(method, route)]
            labels = f'method="{method}",route="{route}"'
            lines.append(
                f'travel_agent_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {count}'
            )
            lines.append(f"travel_agent_http_request_duration_seconds_sum{{{labels}}} {total:.6f}")
            lines.append(f"travel_agent_http_request_duration_seconds_count{{{labels}}} {count}")
        return lines

    def _render_agent_histogram(self) -> list[str]:
        lines = [
            "# HELP travel_agent_run_duration_seconds End-to-end Agent run latency.",
            "# TYPE travel_agent_run_duration_seconds histogram",
        ]
        for task in sorted(self._agent_duration_count):
            for bucket in AGENT_BUCKETS:
                count = self._agent_duration_buckets[(task, bucket)]
                lines.append(
                    f'travel_agent_run_duration_seconds_bucket{{task="{task}",le="{bucket}"}} {count}'
                )
            count = self._agent_duration_count[task]
            total = self._agent_duration_sum[task]
            lines.append(
                f'travel_agent_run_duration_seconds_bucket{{task="{task}",le="+Inf"}} {count}'
            )
            lines.append(f'travel_agent_run_duration_seconds_sum{{task="{task}"}} {total:.6f}')
            lines.append(f'travel_agent_run_duration_seconds_count{{task="{task}"}} {count}')
        return lines
