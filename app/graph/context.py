"""Dependencies injected into graph nodes.

Nodes are pure with respect to state, but they still need to reach the outside
world through ports. Rather than importing singletons (which would make them
untestable), each node receives a ``NodeContext`` carrying the ports and a small
bundle of configuration. Tests build a context from sandbox adapters; production
builds one from real adapters in :mod:`app.deps`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.base import (
    EmailPort,
    KnowledgePort,
    LlmPort,
    NotifierPort,
    TicketPort,
)


@dataclass(frozen=True)
class NodeConfig:
    jira_project_key: str = "OPS"
    email_from: str = "ops@example.com"
    slack_channel: str = "#ops-alerts"
    # Below this classification confidence we route to human review instead of
    # taking irreversible actions (creating tickets, emailing customers).
    confidence_threshold: float = 0.5
    knowledge_top_k: int = 4
    # Retry policy for external calls made inside nodes.
    max_attempts: int = 3
    base_delay_seconds: float = 0.2


@dataclass(frozen=True)
class NodeContext:
    llm: LlmPort
    knowledge: KnowledgePort
    tickets: TicketPort
    email: EmailPort
    notifier: NotifierPort
    config: NodeConfig
