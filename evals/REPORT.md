# Reliability report v3

> Historical baseline: the result below was produced before the integrated
> decision workflow was added. The current dataset contains 100 cases; do not
> present 90/90 as a measurement of the new decision workflow.

Run completed on 2026-09-09 with `gpt-5.6-luna`, Python 3.11.9,
`openai-agents` 0.22.0, and `openai` 3.3.1.

## Historical 90-case result

| Metric | Result |
|---|---:|
| Cases | 90 |
| Conversation turns | 119 |
| Case pass rate | 100% (90/90) |
| Tool routing accuracy | 100% |
| Behavior contract accuracy | 100% |
| Structured-state/provenance accuracy | 100% |
| Output contract accuracy | 100% |
| Infrastructure errors after retry | 0 |
| Total tokens across the logical run | 144,795 |

Category results: train 24/24, mileage 16/16, loyalty 20/20,
multi-turn 15/15, task-switch 8/8, and general/hard-negative 7/7.

The historical machine-readable result is retained in
`evals/results/baseline_v1_1.json`. The final 100-case result is stored in
`evals/results/latest.json`.

## What the suite verifies

- complete, missing, ambiguous, invalid, and unsupported train requests;
- complete, missing, reordered, decimal, zero-tax, and invalid redemption data;
- knowledge-base routing, required evidence, abstention, and citation URLs;
- two- and three-turn slot collection with provenance assertions;
- corrections to origin, destination, date, miles, cash price, and taxes;
- task switching and stale-state isolation;
- hard negatives that contain words such as “train” or “points” but should not
  trigger a business tool;
- provider errors and tool-internal API/network errors as separate outcomes.

## Failures found during this iteration

1. Semantic retrieval missed the Chinese Qatar Airways entity. A deterministic
   entity/intent promotion layer was added before filling remaining semantic
   Top-K positions.
2. The agent updated a departure city correctly but asked for confirmation
   instead of re-running the read-only search. The execution contract now says
   to continue immediately when corrected state remains complete and valid.
3. The clarification grader treated Chinese “零” and numeric “0” differently.
   Matching now normalizes both forms.
4. Tool-internal embedding connection errors were originally counted as answer
   failures. They are now classified as infrastructure errors and can be
   retried without rerunning successful cases.

## Interpretation and limitation

The 100% result means this version passed the finite, curated workflow-contract
suite once, with infrastructure-only retries merged under matching code,
prompt, dataset, knowledge, and harness hashes. It is not a production accuracy
claim and does not establish stability across repeated stochastic runs, unseen
distributions, adversarial prompts, or future provider behavior. The next
reliability milestone is repeated-run stability and a larger held-out set.

## Integrated decision workflow (focused regression)

The final controller version was run on 2026-09-09. All 10 decision cases and
14 turns passed: routing, behavior, state/provenance, and output-contract
accuracy were each 100%, with zero infrastructure errors and 47,997 total
tokens. The machine-readable focused run is
`evals/results/run-20260909T132823Z.json`.

The cases cover complete decisions, missing inputs, all three recommendation
threshold branches, multi-turn completion, added loyalty context, and selective
recomputation after cash-price or destination corrections. Earlier, parallel
stateful tools exposed a lost-state race, so `parallel_tool_calls=False` was
added. A later stochastic regression showed that the model could ignore a
prompt-only instruction after a correction and reuse an old answer. The
controller now derives the next workflow edge from structured state and applies
`tool_choice=required` while a decision dependency is pending. Dynamic tool
gating leaves only valid, non-stale dependencies available; both correction
cases then passed.

## Full 100-case final baseline

The final-code logical run completed on 2026-09-09 after infrastructure-only
retries. All 100 cases and 133 conversation turns passed. Tool routing,
behavior contract, structured-state/provenance, and output-contract accuracy
were each 100%; final infrastructure errors were zero. Category results were:
train 24/24, mileage 16/16, loyalty 20/20, multi-turn 15/15, task-switch 8/8,
general/hard-negative 7/7, and integrated decision 10/10. The logical run used
230,418 tokens.

The committed final merged artifact is `evals/results/latest.json` (generated as
`run-20260909T141821Z.json`). Infrastructure retries were merged
only when code, prompt, dataset, knowledge, and harness hashes matched; partial
provider failures were never scored as product passes or product failures.

## Web product layer

Version 0.4 adds a FastAPI boundary, SQLite persistence for SDK conversation
history plus application-owned structured state, per-session async locks, state
rollback on model API failure, bounded persistent traces, request IDs, JSON
request logs, readiness checks, and baseline per-IP rate limiting. It includes a
same-origin web UI, non-root Docker image, and offline GitHub Actions quality
gate. An HTTP smoke request traversed the real model and all four decision tools
successfully; deterministic unit/API coverage is 94 tests. Version 0.5 adds
bounded Prometheus metrics, security response headers, cross-session concurrency
coverage, a cost-gated load harness, Compose configuration, and architecture,
operations, and portfolio-demo guides. These delivery-layer changes do not alter
the Agent prompt, tools, state, knowledge, or Eval baseline.
