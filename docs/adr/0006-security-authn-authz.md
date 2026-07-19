# 6. Security posture: JWT auth, rate limiting, boundary validation

- Status: Accepted
- Date: 2026-07-18

## Context

The intake API triggers real side effects (tickets, emails, Slack posts) and
runs LLM calls that cost money and can be abused. It must not be openly callable.
We need authentication, abuse protection, and strict validation of anything
crossing the trust boundary, without pulling in a heavyweight identity platform
for what is a service-to-service API.

## Decision

- **Authentication:** stateless **JWT** (HS256) on all protected routes. Tokens
  are minted by `POST /v1/auth/token` against service credentials and by a
  `scripts/create_token.py` CLI for operators. The signing secret comes from
  `JWT_SECRET` (env only). Tokens carry `sub`, `scope`, and `exp`; verification
  is centralized in `app/security/jwt.py` and enforced by a FastAPI dependency.
  `/health` and `/docs` are intentionally public.
- **Authorization:** coarse scopes (`requests:write`, `reports:read`). The
  dependency asserts the required scope per route.
- **Rate limiting:** a Redis-backed fixed-window limiter
  (`app/security/rate_limit.py`) keyed by token subject (falling back to client
  IP), returning `429` with `Retry-After`. Limits are configurable via env.
- **Input validation & sanitization:** all request bodies are Pydantic models
  with explicit constraints (max lengths, allowed enums). Free-text fields are
  length-capped and control-character-stripped before they reach the LLM or the
  database, mitigating oversized payloads and prompt-stuffing.
- **Secrets:** exclusively via environment (`pydantic-settings`); `.env` is
  git-ignored; `.env.example` documents every key with safe placeholders.
- **CORS:** locked to an explicit allowlist (`CORS_ORIGINS`), not `*`.
- **Dependencies:** pinned with hashes-compatible constraints in `pyproject.toml`
  and a locked resolution, so CI builds are reproducible.

## Consequences

- No unauthenticated caller can trigger side effects or spend LLM budget.
- Abuse and accidental floods are bounded per subject.
- HS256 is symmetric; acceptable for a first-party service API. Moving to RS256
  with a JWKS endpoint is a documented "next step" if third parties ever mint or
  verify tokens.
