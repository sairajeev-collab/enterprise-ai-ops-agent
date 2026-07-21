# 21. SSRF egress guard on caller-supplied callback URLs

- Status: Accepted
- Date: 2026-07-21

## Context

A request may include a `callback_url` that the worker POSTs the final status to
when the job finishes. That URL is chosen by the caller, and the worker runs inside
our network — which makes it a classic Server-Side Request Forgery surface. A
caller could set it to `http://169.254.169.254/latest/meta-data/` (the cloud
metadata endpoint, from which credentials are often stealable), or to an internal
service on `10.0.0.0/8` / `127.0.0.1`, and use our worker as a confused-deputy proxy
into the private network.

The old code validated only that the URL was http(s), and a code comment openly
flagged the gap ("a production deployment should further restrict it to an
allowlist"). This closes it.

## Decision

Add `app/security/ssrf.py` — `validate_egress_url()` — and call it in two places:

- **At intake** (`schemas.py`), synchronously and cheaply: scheme must be http(s),
  no embedded credentials, host must be present. No DNS here (a request validator
  shouldn't block on name resolution).
- **At fire time** (`worker.fire_callback`), the authoritative check: resolve the
  host and reject if *any* resolved address is private, loopback, link-local
  (covers the metadata range), multicast, reserved, or unspecified. Run via
  `asyncio.to_thread` so the blocking `getaddrinfo` stays off the event loop. A
  blocked callback is logged and skipped — it never fails the job.

Re-checking at fire time (not just intake) matters: DNS answers can change between
when the request was accepted and when the callback fires (DNS rebinding).

Two settings gate it: `callback_allowed_hosts` (a strict allowlist; if set, an
internal host can be reached deliberately and the IP check is skipped for it) and
`callback_block_private` (default true).

## Honest limitations

- **TOCTOU / DNS rebinding isn't fully closed.** We resolve and then let `httpx`
  resolve again to connect; a hostile resolver could return a public IP to our
  check and a private one to httpx. Fully closing this needs pinning the connection
  to the vetted IP (a custom transport). Documented; the current guard stops the
  overwhelmingly common static-target attack, not a bespoke rebinding one.
- **The allowlist trusts the operator.** An allowlisted host skips the IP check by
  design — if you allowlist an internal host, that's a deliberate choice.

## Consequences

- The metadata endpoint and private ranges are blocked out of the box.
- Genuinely internal callbacks are reachable via an explicit allowlist, not by
  weakening the default.
- The residual DNS-rebinding gap is written down, not hidden — consistent with
  ADR-0020.
