# 9. Evaluation harness and quality gates

- Status: Accepted
- Date: 2026-07-19

## Context

For an AI system, the highest-risk failure is not a crash — it's the model
quietly getting worse: a prompt tweak, a model swap, or a dependency bump that
degrades classification or extraction. Unit tests over deterministic sandbox
logic cannot catch that, and we had no way to measure real-model quality or
prevent regressions.

## Decision

**A labeled golden dataset + a harness that runs the real nodes.**
`evals/dataset.py` holds a small, curated set of realistic inbound messages with
human ground-truth labels (request type, priority, customer email). `evals/`
drives the *production* `classify` and `extract` nodes over it and reports:

- classification accuracy and **macro-F1** with per-class precision/recall,
- priority accuracy,
- email-extraction accuracy,
- **confidence calibration** — mean model confidence on correct vs incorrect
  predictions, which tells us whether the `needs_review` threshold is meaningful.

**One harness, any model.** The same code evaluates the deterministic sandbox
model (via `LLM_MODE=sandbox`) or a real one (`LLM_MODE=real`). This makes model
comparison a config change, not a rewrite.

**Two gates in CI:**

1. `python -m evals --min-accuracy 0.90` runs against the sandbox — fully
   deterministic, no secrets — and fails the build on a regression. The sandbox
   currently scores 95.8% accuracy / 0.96 macro-F1, so the gate has headroom.
2. A **live smoke test** (`pytest -m smoke`) exercises the real HTTP adapter,
   JSON-mode structured output, embeddings, and the classify node against a live
   OpenAI-compatible endpoint. It self-skips when none is reachable, so the
   default suite and CI stay hermetic while developers (and a nightly job with a
   real model) get real coverage.

## Consequences

- Model/prompt regressions are caught mechanically before merge.
- Confidence calibration is measured, not assumed, which justifies the human-review
  routing threshold (ADR-0003).
- The golden set is small and hand-maintained; growing it (and adding
  inter-annotator review) is the obvious next investment.
- The smoke test's real-model assertions run only where a model is available;
  they are a safety net, not a hermetic guarantee.
