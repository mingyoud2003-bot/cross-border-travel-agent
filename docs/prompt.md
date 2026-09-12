# Runtime prompt contract

## TripFlow conversation and proposal Agents

The primary product Agent is `tripflow_conversation_agent` in `tripflow_agent.py`.
It receives a bounded ordered transcript plus the previous structured draft and must
return a fully merged draft and one concise conversational reply. Incomplete input
causes one follow-up question; complete input causes the application to open a
confirmation dialog. Messages and the typed draft are persisted per trip.

The compatibility extraction path remains `tripflow_proposal_agent`.
Its typed output contains itinerary drafts and flight-lookup requests, not external
provider results. It must extract only source-present values, keep every itinerary
candidate in draft state, and treat the user's text as untrusted travel data.

After inference, deterministic code recomputes missing fields, removes ungrounded
operators/timezones/dates, blocks negated or prompt-test requests, and authorizes a
flight lookup only when explicit lookup intent, flight number, and departure date are
all present. AeroDataBox results are added by application code to the public response
schema, so a model output cannot forge provider verification. Confirmed state is
written only through a separate versioned endpoint after the user selects a stored
candidate.

Multi-turn correctness does not depend on the assistant's prose. Application code
merges prior structured fields with the latest grounded delta, handles explicit task
switches, and derives a bounded set of city timezones so users are never asked for
IANA identifiers. A model reply that claims a field was collected cannot authorize
a lookup unless that field is present in the normalized structured draft.

## Legacy Atlas Agent

The executable prompt is `BASE_INSTRUCTIONS` in `agent.py`; dynamic instructions
append `TravelState.prompt_context()` on every model turn. This document records
the behavior contract reviewers should expect.

1. Only user-provided values with provenance may authorize business tools.
2. Missing or invalid fields cause clarification, never model completion.
3. A correction invalidates affected tool results and triggers selective replay.
4. Task switching cannot reuse stale target-task values.
5. Train results distinguish schedules, realtime state, no results, and provider
   failures; the provider has no ticket-price authority.
6. Mileage value is calculated deterministically and requires explicit taxes,
   including an explicit zero.
7. Loyalty answers require retrieved evidence and returned source URLs. Out of
   scope or insufficient evidence causes abstention.
8. Integrated decisions run dependency tools sequentially, continue through
   recoverable provider failures, and terminate through the deterministic
   `compose_travel_decision` tool.

The final integrated result is JSON with exactly eight top-level sections:
request summary, transport options, redemption value, loyalty benefits,
recommendation, tradeoffs, evidence, and limitations.
