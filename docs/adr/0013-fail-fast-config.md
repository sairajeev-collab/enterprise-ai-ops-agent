# 13. Fail fast on unsafe configuration at startup

- Status: Accepted
- Date: 2026-05-09

## Context

The worst production incident is the quiet one: booting with a placeholder JWT
secret, or with an integration set to `real` mode but missing its credentials, and
only discovering it when the first request mishandles auth or a live call fails
half-configured. Configuration errors should surface at deploy time, not at 3am on
the first bad request.

## Decision

Settings are a single validated Pydantic model (`app/config.py`) with a
`model_validator` that **refuses to boot in production** with the shipped
placeholder secret, a too-short secret, or a half-configured integration. Local and
test environments keep permissive defaults so `docker compose up` and the suite run
with zero setup; the strict checks bind only when `APP_ENV=production`.

## Consequences

- A misconfigured production deploy crashes at startup with a clear message instead
  of failing subtly later.
- Local dev stays zero-config; the safety net is environment-gated, not a tax on
  every run.
- Config is one typed object, so what's required is readable in one place rather
  than scattered across `os.getenv` calls.
