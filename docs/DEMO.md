# Three-minute portfolio demo

## Setup

```bash
PORT=8000 python main.py
python scripts/demo_product.py
```

## Story

1. In TripFlow, ask to check a specific flight with a date. Show that the Agent
   extracts a grounded lookup request and the provider returns a candidate, while
   the confirmed timeline is still unchanged.
2. Confirm the matching candidate. Show server-owned provider provenance, the
   version increment, conflict detection, and ICS export. Contrast this with a broad
   “北京到巴黎机票多少钱” request, which must not spend a provider call.
3. In the legacy view, submit a decision request without taxes. Show that the Agent asks only for the
   missing value and does not call an unauthorized tool.
4. Add taxes and BA Silver status. Show the ordered railway, deterministic mileage,
   grounded loyalty, and decision-composer calls in Trace; show turn-level provenance
   in State.
5. Change only the cash price. Show that the railway and loyalty results are retained,
   mileage is recomputed, and the recommendation changes.
6. Refresh/restart the service and reopen the session endpoint to demonstrate SQLite
   persistence.
7. Open `/metrics` and the Eval report to connect the UI behavior to operational and
   regression evidence.

## Interview explanation

The central design choice is that the model handles language but does not own facts or
authorization. Structured state decides whether a tool is legal, provenance verifies
where each argument came from, selective invalidation controls recomputation, and a
deterministic composer owns the recommendation contract. The Eval suite was built
from actual failure modes, including stale-answer reuse after user corrections.

## Honest limitations

- The railway provider returns schedules rather than ticket prices.
- AeroDataBox checks a known flight number/date; it does not search fares, inventory,
  or every possible route, and its free tier has range/rate/quota limits.
- The loyalty corpus is intentionally small and bounded.
- The local deployment is single-worker; distributed coordination is documented but
  not claimed as implemented.
- The project provides decision support and never purchases a ticket.
