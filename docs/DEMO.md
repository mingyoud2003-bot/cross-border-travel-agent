# Three-minute portfolio demo

## Setup

```bash
PORT=8000 python main.py
python scripts/demo_product.py
```

## Story

1. Upload a PDF or screenshot containing two bookings. Show that one artifact becomes
   two typed candidates while the confirmed itinerary remains unchanged.
2. Confirm the complete item, then complete one missing field on the second. Show
   document provenance on visible fields and form provenance on the corrected field.
3. In TripFlow, say only `帮我查 LH400`. Show that the Agent asks only for the exact
   departure date and no provider call occurs yet.
4. Reply `出发日期是 2026-09-12`. Show the structured draft combining both turns and
   the provider candidate appearing in a review dialog, while the timeline remains
   unchanged.
5. Confirm the matching candidate. Show server-owned provider provenance, the
   version increment, conflict detection, and ICS export. Contrast this with a broad
   “北京到巴黎机票多少钱” request, which must not spend a provider call.
6. Expand the right-side compact manual entry: city and operator are selectable;
   station and timezone overrides stay outside the primary flow.
7. Add a train arriving in `法兰克福` at 10:00 and a flight leaving `Frankfurt`
   at 10:55 with a 90-minute threshold. Show the unified `DEFRA` identity, Chinese
   city display, and deterministic 55-minute connection warning.
8. In the legacy view, submit a decision request without taxes. Show that the Agent asks
   only for the missing value and does not call an unauthorized tool.
9. Add taxes and BA Silver status. Show the ordered railway, deterministic mileage,
   grounded loyalty, and decision-composer calls in Trace; show turn-level provenance
   in State.
10. Change only the cash price. Show that the railway and loyalty results are retained,
    mileage is recomputed, and the recommendation changes.
11. Refresh/restart the service and reopen the session endpoint to demonstrate SQLite
    persistence.
12. Open `/metrics` and the Eval report to connect the UI behavior to operational and
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
- OCR/vision extraction may miss low-quality or unusual layouts, so every imported
  candidate remains approval-gated; the public demo does not retain raw uploads.
