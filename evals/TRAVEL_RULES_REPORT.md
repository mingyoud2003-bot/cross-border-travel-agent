# Itinerary-aware travel rules RAG Eval

## Result

Evaluated on 2026-09-14 with the production answer path and the checked-in
`text-embedding-3-small` index:

| Metric | Result |
|---|---:|
| Behavior contract | 50/50 (100%) |
| Infrastructure unavailable | 0/50 |
| Mean end-to-end latency | 2,025.9 ms |
| p95 end-to-end latency | 5,359 ms |
| Cases completed in one attempt | 50/50 |

Latency is measured around reservation selection, deterministic gates, query
embedding, retrieval, grounded generation, and citation validation. It is not a
load-test result and will vary with network and provider conditions.

## Coverage

| Category | Passed | Contract under test |
|---|---:|---|
| Grounded answer | 18/18 | Correct status and required official evidence ID |
| Itinerary context binding | 4/4 | Answer applies to the selected confirmed segment |
| Missing context | 10/10 | Ask for cabin/fare or membership before retrieval |
| Out of scope | 8/8 | Refuse unsupported domains such as visa, weather, and fares |
| Unsupported provider | 8/8 | Disclose corpus boundary before model generation |
| Prompt injection | 2/2 | Treat the question as data and retain evidence-only citations |

The grader is deterministic: a case passes only when the structured status matches
and every required evidence ID appears in the returned server-owned citations.
It does not use an LLM judge. This avoids judge variance but intentionally does not
claim to measure every aspect of prose quality.

## Iteration record

The first complete run passed 48/50. Failure analysis found two control-plane bugs:

1. A connection-baggage question was incorrectly treated as a checked-allowance
   question and asked for cabin context.
2. An unsupported rail operator reached the missing-context gate before the corpus
   scope gate.

After narrowing the allowance intent and reordering provider scope ahead of missing
context, the next run passed 49/50. The remaining failure was the Chinese paraphrase
`托运行李能带几个箱`, which was not recognized as an allowance-count question. Adding
that deterministic synonym plus a unit regression produced the final 50/50 run.

One sandboxed run returned `APIConnectionError` for every embedding request. It is
excluded from behavior accuracy as an infrastructure failure, not hidden as a model
answer. The runner now retries `unavailable` with bounded backoff, records attempts,
and opens a circuit after a persistent dependency failure.

## Knowledge and safety boundary

- 11 reviewed chunks from selected official British Airways, Lufthansa, oneworld,
  and Qatar Airways pages.
- Retrieval first filters by provider/alliance anchor and topic, then performs hybrid
  embedding and lexical ranking.
- Each record carries source URL, retrieval date, and review-after date. Expired
  evidence cannot produce an answer.
- Generated evidence IDs are checked against the current retrieval set; citations
  and URLs are reconstructed from server-owned records.
- The model has no itinerary write tool. The query endpoint is read-only and never
  changes the trip version.

This is a deliberately bounded corpus, not a claim of global airline coverage. A
production expansion would add scheduled source refresh, document-level change
detection, authenticated tenant isolation, and broader multilingual regression data.

## Reproduce

Static validation is free and runs in CI:

```bash
python evals/run_travel_rules_eval.py --validate-only
```

The real path requires `OPENAI_API_KEY` and incurs API usage:

```bash
python evals/run_travel_rules_eval.py
```
