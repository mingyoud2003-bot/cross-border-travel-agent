# TripFlow Agent Eval report

## Baseline

- Dataset: `tripflow_cases.json`
- Date: 2026-09-12
- Production path: `AgentsProposalService` with typed `ItineraryProposal`
- Cases: 90
- Initial 30-case run: 26/30 (86.7%)
- Expanded first run: 83/90 (92.2%), infrastructure errors: 0
- Full regression: 87/90 (96.7%), infrastructure errors: 0
- Offline regrade of that same full output after documented equivalent-value
  corrections: 90/90 (100%)

The dataset covers arbitrary European, Chinese, and Japanese train operators;
international and domestic flights; stays; multi-item text; missing dates,
times, cities, and operators; non-itinerary input; quoted examples; and prompt
injection. The runner checks collection cardinality, exact high-value fields,
contractual missing-field labels, and small explicit sets of source-equivalent
operator/product-line splits.

| Category | Passed |
|---|---:|
| Train | 30/30 |
| Flight | 20/20 |
| Stay | 13/13 |
| Missing evidence | 13/13 |
| Multi-item | 3/3 |
| Negative | 6/6 |
| Adversarial | 5/5 |

## Failure analysis and changes

| Failure | Root cause | Product change |
|---|---|---|
| City names became IANA timezones | Model used world knowledge beyond the source | Remove timezone unless its exact IANA value occurs in source text |
| `MU5100` became operator `MU` | Model inferred an airline from the service-number prefix | Remove operator unless it remains grounded after service number is removed from evidence |
| `12:30` became a departure datetime | Output schema accepted partial strings | Require a valid ISO date plus time before a datetime candidate is actionable |
| `ÖBB Railjet` differed from `ÖBB` | Eval expected a narrower but not more correct label | Correct the case because the returned value is verbatim source evidence |
| Unbooked and prompt-test routes became candidates | The model extracted entity-shaped text despite document-level negation | Add a deterministic deny gate for explicit non-booking language |
| Product line moved between operator and service number | Both structured forms preserved the same verbatim source facts | Allow only enumerated source-equivalent field splits and regrade saved outputs |

These checks run after the model and before any candidate reaches the
confirmation form. Model extraction remains useful for heterogeneous language,
while authority stays in deterministic application code.

## Reproduction

```bash
python evals/run_tripflow_eval.py --validate-only
python evals/run_tripflow_eval.py
python evals/run_tripflow_eval.py --regrade
```

The real run requires `OPENAI_API_KEY`. CI performs offline dataset validation
and deterministic unit/API tests; it does not spend API credits.
