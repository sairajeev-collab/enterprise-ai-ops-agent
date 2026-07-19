# 2. Hexagonal ports-and-adapters for external integrations

- Status: Accepted
- Date: 2026-07-18

## Context

The agent talks to a lot of the outside world: an LLM, a Jira instance, Slack,
an email server, and a vector store. These systems are slow, flaky, and require
credentials we cannot commit. We must be able to (a) run and test the whole
pipeline with zero external accounts, (b) swap a vendor without rewriting the
orchestration, and (c) keep credentials out of the codebase entirely.

## Decision

Every external dependency sits behind a **port** — a `typing.Protocol` that
declares the narrow operations the domain actually needs (e.g. `LlmPort.complete`,
`TicketPort.create_ticket`, `NotifierPort.notify`). Each port has:

- one or more **real adapters** (e.g. `slack/webhook.py`, `jira/rest.py`,
  `llm/openai_compatible.py`), and
- a **sandbox adapter** (`*/sandbox.py`) that implements the same Protocol with
  deterministic, in-memory behavior and no network I/O.

Adapter selection is driven by environment configuration (`*_MODE=real|sandbox`),
resolved once at startup in `app/deps.py`. Domain code and graph nodes depend
only on the Protocol type, never on a concrete adapter.

Typed exceptions live in `app/adapters/base.py` (`AdapterError`,
`TransientAdapterError`, `PermanentAdapterError`). Adapters translate vendor
errors into these so the orchestration layer can make retry decisions without
knowing vendor specifics.

## Consequences

- The full pipeline runs and is fully testable with `*_MODE=sandbox` and no
  secrets — this is the default for local dev and CI.
- Swapping Jira for, say, Linear is a new adapter file, not a refactor.
- Slight indirection cost: one Protocol + one sandbox per integration. Worth it.
- See [ADR-0005](0005-real-vs-sandbox-integrations.md) for which integration is
  wired real end-to-end.
