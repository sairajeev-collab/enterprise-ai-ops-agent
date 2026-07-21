# 14. Structured JSON logs with a correlation id

- Status: Accepted
- Date: 2026-05-11

## Context

A single request crosses the API and the worker, and its pipeline touches an LLM,
a vector store, Jira, email, and Slack. When something goes wrong, "grep the logs
for this one request" only works if every line from every component carries the
same identifier and the logs are machine-parseable.

## Decision

Emit **structured JSON logs** (`app/logging.py`) and thread a **correlation id**
through a contextvar. The API assigns one per request (honoring an inbound
`X-Request-ID`) and returns it on the response header; the worker sets it to the
`request_id` it's processing. Every log line in that scope carries the id
automatically, so a single grep reconstructs the whole journey across both
processes. This is the log half of the observability posture whose metrics/tracing
half is ADR-0019.

## Consequences

- One id ties API and worker log lines together for any request.
- JSON logs drop straight into a log aggregator with queryable fields.
- The contextvar makes the id ambient — nodes don't have to thread it manually.
