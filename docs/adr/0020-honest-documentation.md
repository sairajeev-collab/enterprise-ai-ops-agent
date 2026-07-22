# 20. Document design rationale, not invented operational history

- Status: Accepted
- Date: 2026-07-21

## Context

It is tempting, when presenting a system, to dress it up with a production history
it doesn't have: "handled N requests/day for six months," a dated incident
timeline, a real dollar figure from a cloud bill, a Grafana screenshot full of
traffic. Those artifacts make a portfolio look battle-tested. They are also, for a
system that has not actually run at load, fabrications, and a reviewer who checks
(asks a follow-up question, looks at the commit history, notices the deploy never
existed) is right to distrust everything else once they find one.

This project made cost tracking, reliability, guardrails, and observability *real*
in code. The question this ADR settles is how to describe them.

## Decision

Every claim in the docs is one of three kinds, and labeled as such:

1. **Verifiable now**. Runnable in this repo: the test suite, the eval catch
   rate, the idempotency check, the metrics endpoint. Stated plainly.
2. **Design rationale**. Why a mechanism exists and how it behaves. Stated as
   design intent, not as a war story ("the budget breaker forces the sandbox model
   at the cap," not "the day our bill hit $347").
3. **Unverified**. Anything requiring a real deployment, real traffic, or a real
   model at scale: labeled explicitly as unverified, with the reason (e.g.
   hardware-gated real-model validation; a dashboard whose panels are empty until
   scraped; tracing not exercised against a live collector).

Concretely: costs are shown as **published provider rates**, not a measured bill.
The dashboard ships as **JSON**, not a screenshot of invented data. Failure modes
are described as **design scenarios the code handles**, not as incidents that
occurred on specific dates.

## Consequences

- A reviewer can trust the docs because every strong claim is either runnable or
  labeled as intent/unverified. Nothing collapses under a follow-up question.
- The project reads as the honest work of an engineer who knows the difference
  between "built and tested" and "run in production", which is itself the signal.
- Some numbers are less impressive than a fabricated version would be. That is the
  trade, made on purpose.
