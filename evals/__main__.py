"""CLI entrypoint: ``python -m evals``.

Runs the evaluation against the configured model (``LLM_MODE=sandbox`` by default,
``real`` to hit a live OpenAI-compatible endpoint), prints a report, optionally
writes JSON, and exits non-zero if accuracy falls below a threshold so it can gate
CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config import get_settings
from app.logging import configure_logging

from evals.harness import (
    build_eval_context,
    build_llm,
    default_cases,
    format_report,
    run_cases,
    summarize,
    to_dict,
)


async def _run(min_accuracy: float, json_path: str | None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    ctx = build_eval_context(build_llm(settings))
    results = await run_cases(ctx, default_cases())
    summary = summarize(results)

    print(format_report(summary))
    if json_path:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(to_dict(summary), handle, indent=2)
        print(f"\nWrote {json_path}")

    accuracy = summary.classification.accuracy
    if accuracy < min_accuracy:
        print(f"\nFAIL: accuracy {accuracy:.1%} is below threshold {min_accuracy:.1%}")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent evaluation harness.")
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.0,
        help="Exit non-zero if classification accuracy is below this (0-1).",
    )
    parser.add_argument("--json", default=None, help="Write the summary JSON to this path.")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.min_accuracy, args.json)))


if __name__ == "__main__":
    main()
