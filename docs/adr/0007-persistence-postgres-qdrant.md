# 7. Persistence: PostgreSQL for state, Qdrant for knowledge

- Status: Accepted
- Date: 2026-07-18

## Context

We have two distinct persistence needs: (1) durable, relational operational state
— requests, per-node run steps, produced artifacts, audit trail. With strong
consistency and queryability; and (2) semantic retrieval over a company knowledge
base to ground the agent's answers. These have different access patterns and
should not be forced into one store.

## Decision

- **PostgreSQL** is the system of record, accessed via **SQLAlchemy 2.0** (typed,
  async) with **Alembic** migrations. Core tables: `request` (intake + status),
  `run_step` (per-node checkpoint for idempotency/resumability), `artifact`
  (ticket refs, reply bodies, reports), and `service_account` (auth). All schema
  changes go through migrations, no `create_all` in production paths.
- **Qdrant** stores embedded knowledge-base chunks for semantic search in the
  `retrieve` node, behind the `KnowledgePort`. Embeddings are produced by the LLM
  adapter's embedding endpoint. A `scripts/seed_knowledge.py` loads a small,
  real seed corpus so retrieval is demonstrable immediately.
- Data access is funneled through `app/db/repository.py`; graph nodes never issue
  raw SQL. This keeps the persistence layer swappable and the nodes pure.

## Consequences

- Clear separation: relational truth in Postgres, semantic recall in Qdrant.
- Idempotency and resumability fall out naturally from the `run_step` table.
- Two datastores to operate; both are provisioned in docker-compose for local dev
  and declared for the deploy target.
- Async SQLAlchemy adds some ceremony (session management) but matches the async
  FastAPI/worker runtime and avoids thread-pool bridging.
