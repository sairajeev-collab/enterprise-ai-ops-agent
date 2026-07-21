# 16. Cost tracking and budget guardrails

- Status: Accepted
- Date: 2026-07-20

## Context

An LLM pipeline's cost is unbounded by default. Every run makes several model
calls; a retry storm, a misconfigured model (someone points prod at GPT-4 instead
of a mini), or a traffic spike can multiply spend fast, and with usage-based
billing you find out from an invoice, not an alert. For a system meant to run
unattended, "no idea what it costs and no ceiling" is a real operational risk, not
a theoretical one.

This is a portfolio project — it has not run against a paid model at volume, so the
cost figures below are OpenAI's **published list prices**, not measured from
traffic. The point of this ADR is the mechanism, which is real and tested.

## Decision

**Price every call.** `app/cost.py` holds published per-token rates as plain
constants (no pricing API exists; they're updated by hand). Each LLM adapter, on
every completion, records an `LlmUsage` row (provider, model, tokens in/out,
computed cost, latency).

**Accumulate per run, persist once.** Usage goes into a request-scoped ledger — a
`contextvar` the adapters append to. Using a contextvar rather than threading a
return value keeps `LlmPort.complete` a plain `-> str`, so no node signatures
changed. The worker opens the ledger around a run and writes the rows to
`llm_call_log` after finalize, so cost is never lost mid-pipeline. The run's total
is denormalized onto `request.cost_usd`.

**Cap it.** Two thresholds (`daily_budget_warn_usd`, `daily_budget_cap_usd`):

- past the soft limit, log a warning;
- past the hard cap, the worker routes the run to a pre-compiled **degraded
  pipeline** that uses the free sandbox model. A dumb-but-free classification beats
  an unbounded bill. This is checked once per run with an indexed `SUM` on
  `llm_call_log.created_at`.

**Failover, not fail.** `FailoverLlm` tries providers in order and falls through on
*transient* errors only (a 4xx is a bad request everywhere). The chain ends in the
sandbox model, so a provider outage degrades the answer instead of dropping the
customer's ticket.

## Consequences

- Spend is queryable (`GET /metrics/costs` by model / day / request type) and
  bounded. `ops_llm_cost_usd_total` and `ops_budget_cap_tripped_total` are on
  `/metrics` for alerting.
- The guardrail costs one indexed DB read per run. Cheap, and worth it.
- **Known gaps, deliberately not fixed yet:**
  - The daily cap is a coarse hammer — it trips the *whole* pipeline to sandbox, not
    just the expensive node. Fine for one workload; a per-node budget would be the
    next step.
  - The SSE streaming path (`?stream=true`) bypasses the worker and currently does
    **not** log cost. Low traffic, so it hasn't bitten — but it's a real hole. See
    the `TODO(cost)` in `routes_requests._sse_pipeline`.
  - Only OpenAI-compatible + sandbox links are wired in the failover chain. Adding
    Anthropic/Google is a new adapter, not a rewrite — left undone because an
    untested paid integration is worse than an honest gap.
- Pricing constants will drift from reality until someone updates them. There's no
  test that can catch that; it's a manual chore, noted here so it isn't a surprise.
