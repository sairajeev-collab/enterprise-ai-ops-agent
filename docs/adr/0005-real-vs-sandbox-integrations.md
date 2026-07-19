# 5. One real integration end-to-end; sandbox for the rest

- Status: Accepted
- Date: 2026-07-18

## Context

The brief requires wiring **one** integration real end-to-end and giving every
other adapter a real interface plus a sandbox implementation. We must pick the
real one deliberately: it should be genuinely functional without fragile
credential dances, safe to demo, and representative of the adapter pattern.

## Decision

Two things are real by default:

- **The LLM adapter** (`llm/openai_compatible.py`) targets any OpenAI-compatible
  endpoint, including a **local Ollama** server. This is the real "brain" and
  requires no paid account — `OLLAMA` on localhost is the documented default.
- **Slack** (`slack/webhook.py`) is the designated real *external SaaS*
  integration, wired end-to-end via a Slack **Incoming Webhook**. A webhook URL
  is a single secret, posts real messages, and has no OAuth handshake — ideal for
  a reproducible end-to-end demo.

Every other external adapter — **Jira** (`jira/rest.py`), **email**
(`email/smtp.py`), and the **knowledge/vector** store (`knowledge/qdrant_store.py`)
— ships with a working real adapter *and* a sandbox adapter, selected per
integration via `*_MODE`. The default profile is:

| Integration | Default mode | Real adapter |
|-------------|-------------|--------------|
| LLM         | real (Ollama) | OpenAI-compatible HTTP |
| Slack       | sandbox\*     | Incoming Webhook |
| Jira        | sandbox      | REST v3 |
| Email       | sandbox      | SMTP |
| Knowledge   | real (Qdrant via compose) | Qdrant |

\* Slack defaults to sandbox so the project runs with zero secrets out of the
box; set `SLACK_MODE=real` + `SLACK_WEBHOOK_URL` to light up the real path. The
adapter, contract tests, and wiring are identical either way — only the env flag
changes.

## Consequences

- `git clone && docker compose up` yields a fully working pipeline with no
  secrets, exercising real LLM + real Qdrant.
- Flipping any single integration to real is a one-line env change, proving the
  adapter seam is honest and not a mock-shaped stub.
- Each real adapter carries a contract test that its sandbox must also satisfy,
  keeping the two implementations behaviorally aligned.
