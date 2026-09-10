# Local performance baseline

Measured on 2026-09-09 against one Uvicorn worker and a temporary SQLite database.
The workload used 200 concurrent session lifecycle operations at concurrency 20. One
operation includes `POST /api/sessions` followed by cleanup through
`DELETE /api/sessions/{id}`. It does not call the model or external providers.

| Metric | Result |
|---|---:|
| Successful operations | 200/200 |
| Throughput | 161.66 operations/second |
| p50 latency | 98.67 ms |
| p95 latency | 252.41 ms |
| p99 latency | 384.34 ms |
| Maximum latency | 429.45 ms |

Command:

```bash
python scripts/load_test.py \
  --base-url http://127.0.0.1:8765 \
  --target session \
  --requests 200 \
  --concurrency 20
```

This measures the HTTP, session-store, SQLite-write, response-serialization, and
cleanup path. It is not evidence of model latency or production capacity. Results on
another machine, filesystem, log configuration, or deployment platform will differ.
The model-backed target requires explicit `--confirm-model-cost` and should be tested
with a small bounded request count.

## Verified three-turn model demo

The portfolio demo was also executed end to end against the configured model and live
tools. The missing-field turn completed in 3.971 seconds with no tool call; the full
four-tool decision completed in 19.566 seconds; the selective cash-price
recalculation completed in 3.175 seconds with only the mileage calculator and decision
composer. These are single observations for workflow verification, not latency SLO
measurements.
