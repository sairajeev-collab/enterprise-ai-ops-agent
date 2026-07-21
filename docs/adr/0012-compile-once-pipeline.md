# 12. Compile the graph once, reuse per request

- Status: Accepted
- Date: 2026-05-06

## Context

LangGraph builds an executable graph from node and edge definitions. Compiling it
is not free, and doing it per request would put avoidable latency and allocation on
the hot path of every job. But the graph also can't be a loose module global that's
awkward to test or to vary (the cost circuit breaker needs a second, sandbox-only
graph).

## Decision

Wrap the compiled graph in a small `Pipeline` object built once at container
startup (`app/graph/build.py`, `app/deps.py`) and reused for every request via
`run()`/`stream()`. The container holds two: the normal `pipeline` and a
`degraded_pipeline` whose context pins the sandbox model, so the budget breaker
(ADR-0016) is a pointer swap, not a rebuild. Node context (adapters, config) is
injected, so tests construct a `Pipeline` with sandbox ports and no infrastructure.

## Consequences

- Graph construction cost is paid once, not per job.
- The degraded/normal split is a first-class object, not a conditional smeared
  through the worker.
- Each `Pipeline` is cheap to instantiate in tests with swapped adapters.
