# Security

The threat model this system is built against, the controls in place, and. Just
as importantly. What it does *not* defend against. Honesty about the gaps is the
point (ADR-0020); a security doc that only lists strengths is marketing.

## What we're protecting

An automated pipeline that ingests untrusted inbound text, calls an LLM, and takes
real actions (opens tickets, sends email, posts to Slack) with real money attached
(LLM spend). The main risks: unauthorized use, prompt-driven misbehavior, cost
abuse, and using the service as a proxy into the private network.

## Controls in place

| Risk | Control | Where |
|------|---------|-------|
| Unauthorized access | JWT bearer auth (HS256) with per-endpoint **scopes** (`requests:write`, `reports:read`) | `app/security/jwt.py`, `auth.py`, ADR-0006 |
| Insecure production boot | Fail-fast startup validation rejects placeholder/short secrets and half-configured integrations | `config.py`, ADR-0013 |
| Abuse / floods | Per-client **rate limiting** + request **body-size cap** (1 MiB) | `security/rate_limit.py`, `observability.py` |
| Injection via inbound text | Control-character **sanitization** at intake; LLM output **guardrail** before any reply is emailed | `schemas.py`, `guardrails.py`, ADR-0018 |
| **SSRF** via `callback_url` | Egress guard: http(s) only, no credentials, blocks private/loopback/link-local (incl. cloud metadata) unless allowlisted | `security/ssrf.py`, ADR-0021 |
| Runaway LLM cost | Daily budget **circuit breaker** forces the sandbox model at the cap | `cost.py`, worker, ADR-0016 |
| Duplicate side effects on replay | Deterministic **idempotency keys** on every external call | ADR-0017 |
| Info leak in transport | Baseline security headers (`nosniff`, `DENY` framing, `no-referrer`) | `observability.py` |
| Secrets in the image | Config from env; `.env` git-ignored; `.dockerignore` keeps it out of the build context |. |

## Known limitations (what this does NOT do)

- **JWT is symmetric (HS256) with no rotation or revocation list.** Fine for a
  single-service portfolio; a multi-service deploy wants asymmetric keys (RS256/JWKS)
  and a revocation story. Tokens are bearer. Anyone holding one is the principal.
- **No secrets manager.** Secrets come from the environment. There's no Vault/KMS
  integration; that's a deployment concern left to the host.
- **The SSRF guard doesn't fully close DNS rebinding.** It resolves-and-checks but
  doesn't pin the connection to the vetted IP (ADR-0021 spells this out).
- **The output guardrail is a denylist, not a safety proof.** It catches leaks,
  scaffolding echoes, and degenerate/runaway text, not a fluent-but-wrong reply
  (ADR-0018). Low-confidence classifications route to human review as the backstop.
- **Rate limiting is per-instance.** With multiple API instances behind a load
  balancer the limit is per-process, not global. A shared-store limiter is future
  work.
- **No pen test, no SAST/dependency-scanning gate yet.** Dependencies are pinned;
  adding `pip-audit`/Dependabot to CI is a clear next step.

## Reporting

This is a portfolio project, not a hosted service. If you're reviewing it and spot
something, open an issue — finding a gap I didn't list is exactly the kind of
feedback that's welcome.
