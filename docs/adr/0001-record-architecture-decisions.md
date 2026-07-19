# 1. Record architecture decisions

- Status: Accepted
- Date: 2026-07-18

## Context

This is a production-intent system with several non-obvious architectural
choices (async orchestration, ports-and-adapters, a real-vs-sandbox integration
toggle). A reviewer joining cold needs to understand *why* each choice was made,
not just reverse-engineer it from code. Decisions also drift over time; we want a
durable, append-only record instead of tribal knowledge.

## Decision

We use Architecture Decision Records (ADRs), one Markdown file per significant
decision, stored in `docs/adr/` and numbered sequentially. We follow a light
MADR-style format: Context, Decision, Consequences. ADRs are immutable once
Accepted; a reversal is a new ADR that supersedes the old one.

## Consequences

- Reviewers can read `docs/adr/` top-to-bottom and understand the system's spine
  in ~10 minutes.
- Every "why is it built this way?" question has a canonical answer.
- Small overhead per decision; acceptable and intentional.
