# 15. Containerized deployment, single image, two commands

- Status: Accepted
- Date: 2026-05-14

## Context

The system is two processes (API and worker) that share the same code, models,
config, and database schema. They need to deploy together and stay in lockstep on
version. We wanted a deployment story a reviewer can run locally and that maps to a
real host, without reaching for Kubernetes or Terraform for what is a two-process
app.

## Decision

One `Dockerfile` builds one image; the API and worker are the same image run with
different commands (`uvicorn app.main:app` vs `python -m app.jobs.worker`). Local
orchestration is `docker-compose.yml` (Postgres, Redis, Qdrant, Ollama, api,
worker); the reference host is Fly.io via `fly.toml`. Migrations run on the API
container's start command, so schema and code advance together. Deliberately **no**
Kubernetes/Terraform/Helm — that complexity isn't earned by a two-process
portfolio system, and adding it would be resume-driven, not problem-driven.

## Consequences

- API and worker can't drift in version — they're the same artifact.
- `docker compose up` reproduces the whole stack locally; Fly is one `deploy`.
- Scaling past a couple of instances (leader election, autoscaling, a managed
  queue) is explicitly future work, noted in the README rather than pre-built.
