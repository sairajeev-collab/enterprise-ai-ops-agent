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
from app.cost import current_total_usd, open_ledger
from app.logging import configure_logging

from evals.harness import (
    build_eval_context,
    build_llm,
    default_cases,
    format_report,
    model_label,
    run_cases,
    summarize,
    to_dict,
)


async def _run(min_accuracy: float, min_guardrail: float, json_path: str | None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Meter the run so a real-model eval reports what it actually cost (ADR-0016).
    # $0 in sandbox mode, and the line still prints — the number is measured here,
    # not asserted in a README.
    ctx = build_eval_context(build_llm(settings))
    with open_ledger():
        results = await run_cases(ctx, default_cases())
        run_cost = current_total_usd()
    summary = summarize(results, model=model_label(settings))
    per_case = run_cost / len(results) if results else 0.0

    print(format_report(summary))
    print(
        f"run cost:                  ${run_cost:.4f} (${per_case:.4f}/case, est. published rates)"
    )
    if json_path:
        payload = {
            **to_dict(summary),
            "run_cost_usd": round(run_cost, 6),
            "cost_per_case_usd": round(per_case, 6),
        }
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"\nWrote {json_path}")

    accuracy = summary.classification.accuracy
    if accuracy < min_accuracy:
        print(f"\nFAIL: accuracy {accuracy:.1%} is below threshold {min_accuracy:.1%}")
        return 1
    if summary.guardrail_catch_rate < min_guardrail:
        print(
            f"\nFAIL: guardrail catch rate {summary.guardrail_catch_rate:.1%} is below "
            f"threshold {min_guardrail:.1%}"
        )
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
    parser.add_argument(
        "--min-guardrail",
        type=float,
        default=1.0,
        help="Exit non-zero if the guardrail catch rate is below this (0-1).",
    )
    parser.add_argument("--json", default=None, help="Write the summary JSON to this path.")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.min_accuracy, args.min_guardrail, args.json)))


if __name__ == "__main__":
    main()
