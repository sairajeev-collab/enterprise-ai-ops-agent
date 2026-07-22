# 17. Idempotency strategy

- Status: Accepted
- Date: 2026-07-21

## Context

The pipeline performs external side effects. It opens a Jira ticket, emails the
customer, posts to Slack. The delivery guarantee is at-least-once (ADR-0004/0008):
a job can be redelivered after a crash, re-driven on retry, or replayed by an
operator. Without care, "at least once" means duplicate tickets, duplicate emails,
and a customer who gets three "we're on it" messages. The whole reliability story
is only safe if replaying a request is a no-op for effects that already happened.

## Decision

**Every side-effecting call carries an idempotency key derived deterministically
from `request_id`**, never a timestamp, never a fresh UUID:

| Effect | Key | How the adapter enforces it |
|--------|-----|-----------------------------|
| Jira ticket | `req-<id>` | REST adapter searches for the label before creating; returns the existing issue if found. |
| Customer email | `req-<id>-reply` | SMTP adapter sets a deterministic `Message-ID` from the key so a mail system can collapse dupes. |
| Slack notify | `req-<id>-notify` | Webhook adapter dedupes by key. |

Two independent guards back this up:

1. **Deterministic keys (primary).** Because keys are a pure function of
   `request_id`, a replay produces the *same* key and the effect collapses. This is
   asserted two ways: `test_idempotency.py` replays one request 10× against the
   deduping sandbox adapters and asserts **exactly one** ticket / email / Slack
   post; and `scripts/idempotency_check.py` (CI, `make idempotency-check`) drives
   the pipeline twice and fails if any captured key differs between runs. I.e. it
   catches the day someone adds `datetime.now()` to a key.
2. **Step checkpointing (secondary).** Completed nodes are recorded in `run_step`,
   so a re-driven run can also short-circuit past finished terminal steps.

## Honest audit, where this is weaker than it looks

- **Slack has no server-side idempotency.** An Incoming Webhook will happily post
  the same payload twice. The webhook adapter's dedupe is an **in-process set**
  keyed by idempotency key. It does *not* survive a worker restart. In practice
  the deterministic key + `run_step` checkpoint means the notify node doesn't
  re-run within a single delivery, so it hasn't bitten. But a crash *between* the
  Slack post and the checkpoint commit could, in theory, double-post. Documented,
  not fixed. The blast radius is a duplicate Slack message, not a duplicate
  refund, so it's below the line.
- **The idempotency is inside our adapters, not the DB.** There's no unique
  constraint enforcing "one ticket per request_id" at the storage layer — if a
  future adapter forgets the key, nothing stops it. `scripts/idempotency_check.py`
  is the compensating control, but it only covers keys that flow through the
  pipeline, not a rogue direct call.

## Consequences

- Replays and redeliveries are safe for the effects that matter (tickets, email).
- A regression that makes a key non-deterministic fails CI, not production.
- The Slack in-process dedupe is a known, bounded weakness. If Slack effects ever
  become load-bearing, the fix is a persisted dedupe table keyed by
  `(request_id, effect)`. A small change behind the existing port.
