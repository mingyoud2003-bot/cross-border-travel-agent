# TripFlow MVP build brief

## Confirmed product contract

TripFlow turns user-entered travel details into a trustworthy itinerary. The
primary input paths are a structured form and pasted natural language. PDF and
image ingestion are optional later capabilities, not onboarding requirements.

The MVP supports flights, trains, and stays. A train operator is free text: all
operators can be recorded, while external schedule or realtime verification is
an explicitly separate provider capability.

The core workflow is:

1. Create a trip.
2. Add transport or stays manually, or produce a candidate proposal from text.
3. Confirm candidate fields before they become authoritative state.
4. Detect deterministic timeline conflicts.
5. Export a stable calendar representation.
6. Add approval-gated calendar writes only after the state contract is stable.

## Reliability boundaries

- Application code owns IDs, timestamps, timezone validation, state versions,
  conflict detection, persistence, approvals, and idempotency.
- The model may interpret natural language and propose typed values. It cannot
  directly mutate confirmed itinerary state.
- Every confirmed field carries source type, source identifier, excerpt, and
  confirmation status.
- Changes use optimistic concurrency so a stale browser or approval cannot
  overwrite a newer trip version.
- Provider verification is additive. Failure or missing coverage never prevents
  a user-provided itinerary from being recorded.

## Implemented MVP scope

- SQLite-backed local/portfolio deployment with a repository boundary that can
  later move to PostgreSQL.
- REST APIs for trips, transport, stays, versioned changes, conflicts, and ICS export.
- A single Agents SDK agent for typed natural-language proposals with
  deterministic evidence grounding.
- A responsive browser UI for text proposals, confirmed forms, edits, conflict
  warnings, and calendar export.
- A 100-case real-model extraction/lookup-gating Eval plus 144 deterministic
  project tests.
- No booking, payment, cancellation, realtime monitoring, or calendar mutation
  in the initial slice.

## Open product questions

- Authentication provider for public beta.
- Google Calendar OAuth consent configuration.
- Long-term artifact retention policy when PDF/image ingestion is introduced.
- Which transport providers merit verified schedule/status adapters after user
  validation.
