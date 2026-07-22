# Measuring a real model

The eval numbers in the README come from the **deterministic sandbox model**, which
measures the pipeline's wiring, not a model's judgment (ADR-0011). Closing that gap
takes about twenty minutes and roughly a cent. This is the procedure.

> **Status: not yet run.** The results table below is a template with blanks. Until
> it's filled in with a real run, the README's claim stays "real-model accuracy is
> unverified". That's deliberate (ADR-0020), not an oversight.

## Run it

```bash
export OPENAI_API_KEY=sk-...        # your key; never commit it
make eval-real
```

That runs the 24-case golden set against `gpt-4o-mini` and writes
`real-model-report.json`. The run prints what it actually spent. Cost is
**metered from the response `usage` block**, not estimated by hand.

To try a stronger model, override the one variable:

```bash
LLM_CHAT_MODEL=gpt-4o make eval-real
```

Any OpenAI-compatible endpoint works the same way (vLLM, Together, a local
Ollama). Change `LLM_BASE_URL`.

### What it costs

24 cases × 2 calls (classify + extract), short prompts. At published rates
(`app/cost.py`): **gpt-4o-mini ≈ $0.15/$0.60 per 1M tokens** → on the order of a
cent per full run. `gpt-4o` is ~17× that and still under a dollar. The printed
number is the authority; these are the rates it's computed from.

## What to record

Fill this in from `real-model-report.json` and commit it. Four things matter, and
the interesting one is the last:

| Metric | Sandbox | gpt-4o-mini | Notes |
|---|---|---|---|
| classification accuracy | 100% | _TBD_ | |
| macro-F1 | 1.000 | _TBD_ | |
| priority accuracy | 83.3% | _TBD_ | |
| email extraction | 100% | _TBD_ | |
| mean confidence (correct) | 0.83 | _TBD_ | |
| mean confidence (**wrong**) | 0.00 | _TBD_ | calibration; see below |
| run cost | $0.0000 | _TBD_ | |
| cost per case | $0.0000 | _TBD_ | |

**Then write two or three sentences on where it failed.** A per-class table from
the report shows which categories it confuses. Expect the ambiguous ones —
`billing` vs `complaint` on an angry invoice message, `account` vs
`technical_support` on a login failure. Name the specific cases.

**Confidence calibration is the finding that matters.** The `needs_review`
threshold only makes sense if wrong answers get lower confidence than right ones.
If a real model is confidently wrong, that threshold needs to move, and saying so
is a stronger result than a high accuracy number.

## Why this is worth doing

It converts the honest-but-weak claim *"real-model accuracy is unverified"* into
*"measured: N% on gpt-4o-mini at $X per run, and here's the failure mode."* That's
a specific, defensible sentence in an interview, and it costs about a cent.

Record the result here even if it's unflattering. Especially if it's unflattering.
A documented failure mode is evidence of engineering judgment; a suspiciously clean
number invites a question you can't answer.
