# 11. A deterministic sandbox model, not a mock

- Status: Accepted
- Date: 2026-05-04

## Context

The pipeline is built around an LLM, but tests and CI can't depend on a model
server: no GPU in CI, no API key, no network, and. Critically, no determinism.
Yet a `Mock` that returns a constant string tests nothing about the graph's
handling of *structured, varied* output. We wanted the whole system runnable
end-to-end offline while still exercising real parsing and routing.

## Decision

Ship a `SandboxLlm` (`app/adapters/llm/sandbox.py`) that is a real `LlmPort`
implementation, not a mock: it reads a `TASK:` marker the nodes place on the first
line of every system prompt, then applies small keyword heuristics to the user text
to produce *plausible, structured* JSON (classification, extraction) or prose
(reply, report). It is deterministic. Same input, same output, so end-to-end
assertions are stable. Real models simply treat the `TASK:` marker as an ordinary
instruction, so the seam is honest: swapping in a real provider changes nothing
about the graph.

## Consequences

- `docker compose up` and the entire test suite run with zero external services.
- The eval harness runs against the sandbox as a regression gate on the *graph*,
  distinct from model quality (ADR-0009).
- **Limitation, stated plainly:** sandbox accuracy is not model accuracy. The
  sandbox exists to test wiring, not to stand in for GPT-4o's judgment; real-model
  validation is a separate, hardware-gated exercise (see README).
