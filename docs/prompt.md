# Runtime prompt contract

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
