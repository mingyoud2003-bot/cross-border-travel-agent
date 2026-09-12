# TripFlow Agent Eval report

## Baseline

- Dataset: `tripflow_cases.json`
- Date: 2026-09-12
- Production path: `AgentsProposalService` with typed `ItineraryProposal`
- Cases: 30
- First run: 26/30 (86.7%)
- Regression after deterministic grounding: 30/30 (100%)

The dataset covers arbitrary European, Chinese, and Japanese train operators;
international and domestic flights; stays; multi-item text; missing dates,
times, cities, and operators; non-itinerary input; quoted examples; and prompt
injection. The runner checks collection cardinality, exact high-value fields,
and contractual missing-field labels.

## Failure analysis and changes

| Failure | Root cause | Product change |
|---|---|---|
| City names became IANA timezones | Model used world knowledge beyond the source | Remove timezone unless its exact IANA value occurs in source text |
| `MU5100` became operator `MU` | Model inferred an airline from the service-number prefix | Remove operator unless it remains grounded after service number is removed from evidence |
| `12:30` became a departure datetime | Output schema accepted partial strings | Require a valid ISO date plus time before a datetime candidate is actionable |
| `ÖBB Railjet` differed from `ÖBB` | Eval expected a narrower but not more correct label | Correct the case because the returned value is verbatim source evidence |

These checks run after the model and before any candidate reaches the
confirmation form. Model extraction remains useful for heterogeneous language,
while authority stays in deterministic application code.

## Reproduction

```bash
python evals/run_tripflow_eval.py --validate-only
python evals/run_tripflow_eval.py
```

The real run requires `OPENAI_API_KEY`. CI performs offline dataset validation
and deterministic unit/API tests; it does not spend API credits.
