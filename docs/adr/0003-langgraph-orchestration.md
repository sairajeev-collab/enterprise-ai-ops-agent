# 3. Explicit LangGraph state machine for orchestration

- Status: Accepted
- Date: 2026-07-18

## Context

The business process is a fixed pipeline: classify -> extract -> retrieve
knowledge -> create ticket -> reply -> notify -> persist -> report. We want this
flow to be inspectable, testable node-by-node, resumable after failure, and
diagrammable. A free-form "agent decides what to do next" loop would be harder to
test and reason about, and this process does not need open-ended tool selection —
the steps and their order are known.

## Decision

We model orchestration as an explicit `langgraph.graph.StateGraph` over a single
typed `AgentState` (Pydantic). Each node is a **pure function**
`node(state: AgentState, ctx: NodeContext) -> dict` that returns only the fields
it changed; LangGraph merges the partial update. Nodes receive their
dependencies (adapters) through an injected `NodeContext` rather than importing
singletons, which keeps them unit-testable in isolation.

Node functions contain no retry or transport logic. Retries/backoff are applied
by a decorator (`graph/retry.py`) around the *adapter* calls, so node logic stays
pure and deterministic. The graph edges are linear for the core flow, with a
conditional edge that routes low-confidence classifications to a
`needs_human_review` terminal state instead of taking irreversible actions.

The graph is rendered as Mermaid in `docs/architecture.md` and the README.

## Consequences

- Each node is tested in isolation with a fake `NodeContext` and asserted on its
  returned delta, no mocking of framework internals.
- The pipeline is resumable: `AgentState` is serializable and persisted per step
  (see [ADR-0004](0004-async-job-processing-redis.md)), so a crashed run restarts
  at the last incomplete node.
- We give up dynamic tool-planning. Acceptable: the process is fixed by design.
