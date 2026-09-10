# Security and privacy boundary

## Threats and controls

| Threat | Current control | Remaining production work |
|---|---|---|
| Model invents a parameter | User-turn provenance and tool input guardrails | Expand adversarial Eval set |
| Prompt asks to bypass tools | Application-owned dynamic tool gating | Add a prompt-injection classifier if external content grows |
| Stale recommendation after correction | Dependency invalidation and required workflow edge | Repeated stochastic regression runs |
| Knowledge hallucination | Bounded retrieval, evidence IDs, abstention | Automated source freshness pipeline |
| API abuse/cost spike | Input limit and per-IP single-worker rate limit | Gateway/Redis quota keyed by authenticated user |
| Secret disclosure | Ignored env files; no credentials in logs/traces | Managed secrets and routine rotation |
| Trace privacy leak | No message body/session ID in request metrics and logs | Encryption, access control, retention and deletion policy |
| Browser injection | DOM `textContent`, restrictive CSP, anti-framing headers | Automated dependency and browser security scans |
| Cross-session race | Per-session async lock | Distributed lock before multiple workers |
| Local database loss | Persistent volume support | Encrypted backups and restore drill |

## Data classification

The demo may store user messages inside Agents SDK conversation history and stores
confirmed slot source text in `TravelState`. Treat the SQLite file as user data. Do
not accept real personal or travel-document information until authentication,
encryption, retention, export, and deletion requirements are defined.

## Reporting

Do not include API keys, complete user messages, session IDs, or system prompts in
issue reports. Use the bounded request ID, tool name/status, state field names, and a
synthetic reproduction case.
