# Operations and production checklist

## Suggested service objectives

These are initial targets to validate with real traffic, not measured production
claims:

- API availability: 99.5% monthly, excluding upstream OpenAI/transport incidents.
- Non-model endpoints: p95 below 250 ms.
- Agent endpoint: p95 below 30 seconds.
- Product contract pass rate: at least 98% on the held-out regression set.
- Unsupported-knowledge grounded abstention: 100% on the curated boundary set.

## Signals

`GET /metrics` exposes Prometheus text for bounded HTTP routes, status codes and
latency, plus Agent outcomes, latency, tool counts, and token usage. Logs are JSON and
joinable through `request_id`. Message bodies, session IDs, credentials, and prompts
are excluded from metrics and request logs.

Recommended alerts:

- 5xx ratio above 5% for five minutes;
- Agent `provider_error` ratio above 10%;
- flight-provider `quota_exceeded` or `unavailable` outcomes above 10%;
- p95 Agent latency above 30 seconds;
- sustained 429 responses;
- readiness failing for two checks;
- token usage increasing materially without a matching request increase.

Protect `/metrics` with a private network or reverse-proxy authentication in a public
deployment.

## Runbook

1. Use `request_id` to correlate the HTTP log with the persisted public trace.
2. Separate product-contract failures from upstream/network failures.
3. Confirm `/ready`, filesystem capacity, SQLite write access, and provider status.
   For flight-only failures, distinguish missing configuration, the ±365-day scope,
   quota/rate limiting, no result, upstream outage, and payload-contract drift.
4. For a product regression, add the observed input to `evals/agent_cases.json`, fix
   the smallest control boundary, run focused Eval, then run the full suite.
5. Restore service only after deterministic tests and the affected Eval category pass.

For an AeroDataBox incident, keep manual itinerary entry available and surface the
structured provider outcome. Do not loosen the grounding gate or ask the model to
reconstruct flight status. A retry should reuse the ten-minute cache when available;
candidate confirmation remains possible for 15 minutes after lookup.

## Data and backup

- Mount `/data` as a persistent volume in containers.
- Back up the SQLite database with a SQLite-aware snapshot process.
- Define a retention period before accepting real user data; the demo default expires
  inactive sessions after six hours.
- Rotate exposed API credentials immediately and keep secrets only in the deployment
  platform's secret store.

## Load test

Start the service, then test session creation without model cost:

```bash
python scripts/load_test.py --target session --requests 200 --concurrency 20
```

The `chat` target is intentionally blocked unless `--confirm-model-cost` is supplied.
This prevents an accidental load test from creating OpenAI API spend.
