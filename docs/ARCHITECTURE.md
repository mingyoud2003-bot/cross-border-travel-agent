# Architecture and design decisions

## System boundary

Atlas is a decision-support product, not a booking system. One code-first Agent
classifies the current task and can call three domain tools plus one deterministic
decision composer. The LLM chooses how to communicate; application code owns
parameter extraction, provenance, validation, workflow authorization, calculation,
result invalidation, persistence, and the final recommendation policy.

```text
Browser / API client
        |
FastAPI boundary -- request ID, rate limit, metrics, validation
        |
AgentService -- session lock, rollback, trace, persistence
        |
TravelState -- confirmed values, provenance, pending workflow edge
        |
Agents SDK loop -- dynamically enabled tools, required tool control
        |
Transitous | calculator | grounded loyalty retrieval | decision composer
```

## Reliability invariants

1. A tool argument must equal a value explicitly captured from a user turn.
2. Missing or invalid values cannot enable an execution tool.
3. Changing a value invalidates only dependent results and the final decision.
4. A task switch clears fields that could silently authorize the new task.
5. A decision cannot finish before every required dependency has been attempted.
6. Knowledge answers require returned evidence; provider failure becomes a visible
   limitation rather than an invitation to guess.
7. Model/API failure rolls back the business state for that turn.

## Key decisions

### One Agent, deterministic control plane

The domains share one user intent and one state object, so specialist handoffs would
add coordination state without improving the product boundary. A single Agent keeps
the interaction coherent. Deterministic tool gating and `tool_choice=required` are
used where a model choice would violate workflow correctness.

### Conversation memory is not business state

Agents SDK `SQLiteSession` stores conversational history. `TravelState` is serialized
separately because application authorization must not depend on facts reconstructed
from prose. Both use one SQLite file but separate tables and lifecycles.

### Selective invalidation

A destination change invalidates the railway result but preserves mileage value. A
cash-price change does the reverse. This reduces external calls while guaranteeing
that the final decision cannot reuse a stale dependency.

### Single-worker deployment baseline

SQLite, in-memory locks, metrics, and rate limiting deliberately define a single
worker deployment. Horizontal scaling requires PostgreSQL/Redis, distributed locks,
shared rate limiting, and an external metrics backend. This boundary is explicit
rather than hidden behind a misleading multi-worker configuration.

## Failure model

| Failure | Product behavior |
|---|---|
| Missing/ambiguous input | Ask only for unresolved fields |
| Unsupported knowledge | Abstain and disclose scope |
| Transport no-results | Continue other dependencies; expose limitation |
| Provider/API failure | Return a controlled error or degraded decision |
| Model service failure | HTTP 503 and state rollback |
| Duplicate concurrent turn | Serialize within the session |
| Restart | Restore conversation, state, and bounded traces from SQLite |

## Scaling path

The next architecture step is not more agents. It is replacing local coordination
components with shared infrastructure, adding authenticated users, encrypting stored
conversation data, and defining retention/deletion policies before horizontal scale.
