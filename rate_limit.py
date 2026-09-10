from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Small single-process limiter for protecting the model-backed endpoint."""

    def __init__(self, requests_per_minute: int) -> None:
        self.limit = requests_per_minute
        self.window_seconds = 60.0
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            entries = self._requests[key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= self.limit:
                retry_after = max(1, round(self.window_seconds - (now - entries[0])))
                return False, retry_after
            entries.append(now)
            return True, 0
