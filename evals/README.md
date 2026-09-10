# Agent reliability evals

The dataset exercises the real `travel_agent` path. Cases are isolated from one
another; turns inside one case share both an Agents SDK session and one
`TravelState` instance.

## Commands

```bash
# Validate JSON/schema and the default smoke selection without API calls.
python evals/run_agent_eval.py --validate-only

# Run seven representative smoke cases, including one integrated decision.
python evals/run_agent_eval.py

# Run all cases or one regression.
python evals/run_agent_eval.py --all
python evals/run_agent_eval.py --case multiturn_train_three_slots
python evals/run_agent_eval.py --category decision

# Reclassify saved tool-level network failures without new model calls, then
# retry and merge only infrastructure-error cases.
python evals/run_agent_eval.py --regrade-latest
python evals/run_agent_eval.py --retry-infra
```

`OPENAI_API_KEY` may be supplied by the process environment or an ignored local
`.env.local` file. Results are written to a timestamped `evals/results/run-*.json`.
`latest.json` is updated only when at least one Agent turn completed, so a total
network outage cannot replace the last executable result with a false baseline.

## Case schema

Single-turn cases keep top-level expectations. Multi-turn cases use a `turns`
array. Single-tool turns use `expected_tool`; integrated decisions use
`expected_tools`, with dependency tools allowed in any order and
`compose_travel_decision` required last. A turn can also assert:

- `expected_missing_fields`: concepts the clarification must request;
- `expected_state`: task, slots, missing fields, and provenance turn numbers;
- `required_evidence_ids`: RAG evidence required for a grounded answer;
- `expected_pence_per_mile`: deterministic calculator output;
- `expected_choice`: the versioned recommendation-policy result;
- `required_output_terms`, `required_output_any`, `forbidden_output_terms`.

For `expected_behavior: "decision"`, the grader parses the final output as JSON,
checks all eight contract sections, verifies the dependency graph, calculation,
recommendation choice, and required evidence IDs.

Use `{{DATE+30}}` or `{{DATE+30_CN}}` for future dates. They render relative to
the execution date, preventing date guardrails from silently expiring the suite.

## Reported metrics

- case and turn counts;
- case pass rate and category breakdown;
- exact tool routing accuracy;
- behavior contract accuracy;
- structured-state/provenance accuracy;
- output-contract accuracy;
- token usage;
- model, SDK versions, UTC timestamp, and prompt/dataset/knowledge hashes.

Connection/time-out failures raised by the model call or returned inside a
function-tool error are counted as infrastructure errors, not model-quality
failures. Resume is allowed only when the prompt, dataset, knowledge,
application, and harness hashes still match.

The deterministic suite in `tests/` validates parsers, state transitions,
provenance enforcement, tool visibility, guardrail helpers, and eval-harness
failure modes without making API calls.
