"""Load the seed knowledge corpus into the configured knowledge store.

Idempotent: documents are upserted by stable id, so re-running updates rather
than duplicates. In sandbox mode this seeds the in-memory store (a no-op for a
fresh process); in real mode it embeds and writes to Qdrant.

Usage:
    python -m scripts.seed_knowledge
"""

from __future__ import annotations

import asyncio

from app.adapters.knowledge.sandbox import SEED_DOCS
from app.config import get_settings
from app.deps import build_container
from app.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    try:
        knowledge = container.node_context.knowledge
        await knowledge.ensure_ready()
        count = await knowledge.upsert(list(SEED_DOCS))
        logger.info("knowledge_seeded", extra={"documents": count})
        print(f"Seeded {count} documents into the knowledge base.")
    finally:
        await container.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
