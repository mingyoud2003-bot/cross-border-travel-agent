from __future__ import annotations

import argparse
import asyncio
import math
import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Sample:
    duration_ms: float
    status_code: int


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


async def issue_request(
    client: httpx.AsyncClient, target: str, message: str
) -> Sample:
    started = time.perf_counter()
    if target == "health":
        response = await client.get("/health")
    elif target == "session":
        response = await client.post("/api/sessions")
        if response.is_success:
            session_id = response.json()["session_id"]
            deleted = await client.delete(f"/api/sessions/{session_id}")
            if not deleted.is_success:
                response = deleted
    else:
        response = await client.post("/api/chat", json={"message": message})
    return Sample(
        duration_ms=(time.perf_counter() - started) * 1000,
        status_code=response.status_code,
    )


async def run(args: argparse.Namespace) -> int:
    if args.target == "chat" and not args.confirm_model_cost:
        raise SystemExit(
            "chat target calls the real model. Re-run with --confirm-model-cost."
        )
    semaphore = asyncio.Semaphore(args.concurrency)
    samples: list[Sample] = []

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=args.timeout, trust_env=False
    ) as client:
        async def bounded_request() -> None:
            async with semaphore:
                try:
                    samples.append(await issue_request(client, args.target, args.message))
                except httpx.HTTPError:
                    samples.append(Sample(duration_ms=args.timeout * 1000, status_code=0))

        started = time.perf_counter()
        await asyncio.gather(*(bounded_request() for _ in range(args.requests)))
        elapsed = time.perf_counter() - started

    durations = [sample.duration_ms for sample in samples]
    failures = sum(not 200 <= sample.status_code < 300 for sample in samples)
    print(f"target={args.target} requests={len(samples)} concurrency={args.concurrency}")
    print(f"success={len(samples) - failures} failure={failures}")
    print(f"throughput_rps={len(samples) / elapsed:.2f}")
    print(
        "latency_ms "
        f"p50={percentile(durations, 0.50):.2f} "
        f"p95={percentile(durations, 0.95):.2f} "
        f"p99={percentile(durations, 0.99):.2f} "
        f"max={max(durations, default=0):.2f}"
    )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded HTTP load test.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--target", choices=("health", "session", "chat"), default="session")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--message", default="你好")
    parser.add_argument("--confirm-model-cost", action="store_true")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.timeout <= 0:
        parser.error("requests, concurrency, and timeout must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
