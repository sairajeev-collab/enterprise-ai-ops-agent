# 18. Output guardrails on customer-facing replies

- Status: Accepted
- Date: 2026-07-21

## Context

The pipeline produces several LLM outputs, but only one leaves the building: the
reply the `reply` node drafts and *emails to the customer*. The ticket description
and the Slack notification land in front of a human who can sanity-check them; the
email does not. That makes the drafted reply the highest-blast-radius output in
the system, and the one place an LLM failure becomes an external incident —
emailing a hallucinated refund promise, echoing an injection from the customer's
own message, or pasting a second customer's address into the body.

We already route low-confidence *classifications* to human review (ADR-0006). That
covers "we're not sure what this is." It does not cover "we're sure, but the model
wrote something it shouldn't send."

## Decision

Add a deterministic output guardrail (`app/guardrails.py`) that gates the drafted
reply before the `reply` node sends it. On any violation the node **holds the
email** (does not send), records the reasons on `Reply.guardrail_violations`,
increments `ops_reply_guardrail_blocked_total`, and lets the rest of the pipeline
complete. The ticket is still opened, so a human picks the reply up from there.

The checks are rule-based on purpose. Regex and length, no model grading a model:

- **Length floor/ceiling.** Rejects degenerate ("OK") and runaway generations.
- **Foreign-address leak.** Any email address in the body that isn't the intended
  recipient is treated as a leak (wrong recipient or cross-customer data).
- **Prompt/injection echo.** Rejects drafts containing scaffolding or injection
  markers (`TASK:`, "system prompt", "ignore previous instructions", …), which
  signal the prompt leaked or the model was steered.

The same corpus of known-bad drafts (`evals/guardrails.py`) is gated three ways:
a unit test, an end-to-end wiring test, and the eval harness, which reports a
**guardrail catch rate** and fails CI below `--min-guardrail` (default 1.0). The
harness also now stamps every report with the **model** under test, so numbers are
attributable.

## Why not an LLM judge

A second model scoring the first is non-deterministic, adds cost and latency to
the customer path, and is itself injectable. For the failure modes that actually
email something harmful. Leaks, echoes, empty/runaway text. A regex is stricter,
faster, and auditable. We deliberately do **not** try to judge tone or factual
accuracy here; a rule can't, and pretending otherwise would be theater.

## Honest limitations

- **The guardrail is a denylist, not a proof of safety.** It catches the known
  bad shapes above. A fluent, plausible-but-wrong reply with no leak, no marker,
  and normal length passes. The human-review routing on low confidence is the
  backstop for "wrong," not this.
- **The email/name matcher is loose.** It flags addresses; it won't catch a phone
  number or a mailing address leak. Scope is deliberately narrow and stated.
- **Held replies need a human to notice.** A hold surfaces as `reply_held` in the
  persisted record and a metric; there is no separate reviewer queue UI yet. If
  hold volume ever became material, that queue is the next step.

## Consequences

- A bad draft costs a held email and a human glance, not an external incident.
- Weakening the guardrail fails a fast unit test and the eval gate, not production.
- One more deterministic seam on the model's output, consistent with the sandbox
  and cost-ledger seams elsewhere.
