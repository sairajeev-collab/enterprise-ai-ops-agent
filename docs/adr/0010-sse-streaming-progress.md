# 10. Server-Sent Events for request progress

- Status: Accepted
- Date: 2026-05-02

## Context

A request runs through eight pipeline nodes and can take seconds. Callers — the
built-in UI and any integrator — want to see progress, not stare at a spinner
until a single blocking response returns. We needed a push channel from the API to
the client for per-node updates.

## Decision

Expose a streaming variant of the submit endpoint that emits **Server-Sent Events**
(`text/event-stream`): a `node_start`/`node_delta` event per node as the graph
advances, then a terminal `complete` event (`app/api/routes_requests.py`). SSE over
WebSockets because the traffic is one-directional (server → client), SSE rides
plain HTTP/1.1 with no upgrade handshake or extra framing, and it reconnects for
free. The same pipeline `stream()` that the worker consumes backs the endpoint, so
there's one execution path, not two.

## Consequences

- The UI shows live node-by-node progress with no polling.
- No new protocol or dependency — it's HTTP and a generator.
- **Known gap:** the synchronous SSE path runs the pipeline in the request and does
  not persist a cost row (there's a `TODO(cost)` marking it); the durable path is
  the async worker. SSE is for interactive/demo use, the queue is for production
  volume. Documented rather than papered over.
