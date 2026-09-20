# SWE-bench token-waste

A measurement pipeline over public SWE-bench Verified leaderboard submissions. It asks two
questions:

- **Claim 1 (economic):** what share of agent spend went to tasks that were never resolved?
- **Claim 2 (accounting):** is the reported cost of an agent task correct?

Scope: SWE-bench Verified split, mini-SWE-agent leaderboard entries only. One scaffold family, so
model varies and scaffold is held constant. This is a controlled comparison, not a convenience
sample.

## Claim 1 results

41 entries analysed (of 48 mini Verified entries). 20,441 instance-attempts observed of 20,500
possible (41 x 500). 11,835 resolved, 8,606 unresolved. $7,611 total observed spend.

$4,024 of that went to instances the submission never resolved = **52.9%**.
`waste_lower` and `waste_upper` both equal 0.5287 — the bounds collapse to the same value; see
Limitations for why that's an untested code path rather than a tight bound.

The sharper framing:

- cost per resolved attempt: $0.313
- cost per unresolved attempt: $0.472
- ratio: 1.51x

These two rates are computed over the *priced* subset only — 11,476 resolved-and-priced and 8,524
unresolved-and-priced attempts (20,000 of 20,441 total; 441 are missing cost data and excluded).
They are not $0.313 x 11,835 and $0.472 x 8,606 — those are the observed resolved/unresolved
counts from the paragraph above, a different denominator, and multiplying by them will not
reproduce $7,611 or $4,024.

So 42.1% of attempts failed but consumed 52.9% of spend (both figures over the full 20,441
observed). The failure rate understates the cost of failure by about a quarter.

Supporting numbers:

- `total_spend_per_resolved_instance` = $0.663
- `calls_per_resolved_instance` = 73.9
- Per-entry waste across 40 computable entries: min 0.276, Q1 0.407, median 0.524, Q3 0.675, max
  1.000. The pooled 0.529 sits at the median, so it isn't a weighting artifact.
- 26 instances were never resolved by ANY entry in the Verified split, $523 spent on them.
  Reported separately, not folded into the 52.9%, because spend on an instance nobody has solved
  is expected loss rather than this submission's waste.

Requires no network and no trajectory parsing — computed from `per_instance_details.json` alone.
See "How to reproduce" below for the exact command and the directory layout it expects.

## Claim 2 — the primary finding

One claim, five pieces of evidence:

**A reported agent cost figure is not a measurement. It is an artifact of the toolchain that
produced it, and nothing in the number itself reveals which toolchain that was.**

**(a) Version-dependent field meaning.** litellm's normalized `prompt_tokens` for Anthropic is
read-only-inclusive on mini v1.13.3 (99.78% of cache-traffic calls) and v1.16.0 (99.99%), and
fully inclusive on v2.0.0. Same provider, same scaffold, opposite meaning; we found no version
note documenting the change. The same 6 Anthropic entries ($1,786.92 of traffic) price at:

- litellm v2.0.0 meaning: $1,786.35 (-0.0%)
- litellm v1.13-v1.16 meaning: $1,975.29 (+10.5%; +13.7-18.2% per v2.0.0 entry)
- Anthropic raw-API meaning: $9,912.95 (+455%)

All 6 Anthropic entries show `d_conv = 0.0000` — litellm is perfectly self-consistent with its own
mapping. The flip costs litellm nothing and costs every downstream consumer 10% to 455%,
depending on which contract they believe they're reading. Nothing detects it, because from
litellm's point of view nothing is broken. This is offered as precedent, not our own finding: the
+455% column is the same failure class as a bug fixed upstream in Drupal's `ai_metering` module,
where cached tokens from OpenAI and Gemini were charged twice in estimated-cost calculations
(stored token counts and quota consumption were unaffected), corrected in the 1.0.2 release —
[project page](https://www.drupal.org/project/ai_metering), see the 1.0.2 release notes for the
fix.

**(b) Adapter-dependent arithmetic, reproduced not inferred.** minimax-2.5's run-time reported
cost equals `R'_conv` plus reasoning-as-output to the cent: $36.86 vs $36.64. litellm's MiniMax
adapter adds reasoning tokens to output. Per-model classification (violation = `reasoning_tokens
> output_tokens`):

- clear: gemini-3-5-flash-fair 57.1%, minimax/minimax-m2 4.03%, z-ai/glm-5 2.15%,
  moonshotai/kimi-k2.5 1.62%
- ambiguous: gemini-3-pro-preview 0.076% (20 calls), minimax/minimax-m2.5 0.046%,
  deepseek/deepseek-v3.2 0.014% (6 calls)
- subset: google/gemini-3-pro-preview, gemini-3-flash-preview, deepseek-reasoner, and all
  Anthropic/OpenAI models

Note `google/gemini-3-pro-preview` is clean while bare `gemini-3-pro-preview` is not. Same
vendor, same model, different routing path, different behavior — that's an adapter fingerprint,
not a billing convention. Dollar impact is immaterial: $11.34 proxy on $7,611. This is a
telemetry-integrity finding, not a billing finding. Mechanism unresolved: genuine convention vs
litellm/vLLM adapter bug (BerriAI/litellm#24526 reproduces the symptom exactly). Also a detection
floor, since the comparison only fires when reasoning exceeds output.

**(c) Structurally present, informationally empty telemetry.** gpt-5.1-codex: 11,941 calls, every
one carrying a complete well-formed usage object in which every token field is null, against
$294.44 reported. Real dollars, zero priceable tokens.

**(d) Unreproducible pricing.** Three entries (devstral x2, glm-4.6) priced their reported dollars
from `/home/klieret/litellm_model_registry.json`, a submitter-local file. glm-5 ran with
`cost_tracking: ignore_errors` on a model absent from litellm's table. Their reported cost is not
reproducible from any public source.

**(e) Identical trajectories, divergent reported cost.** gpt-5-2-codex and gpt-5-2-high match on
17,520 of 17,520 calls across (instance, idx, input, output, reasoning), and every trajectory in
both requested `openai/gpt-5.2-2025-12-11`. They report $224.71 and $236.78, 5.4% apart, as two
separate leaderboard rows. Excluded as a duplicate; the $12 is unattributed.

## Second-order findings

**Price-table drift at submission.** Across 19 entries, COMPUTED $4,050.35 vs `R'_conv` $4,455.82,
a delta of -$405.47 (-10.0%). This is NOT the cache convention: all 6 Anthropic entries
contribute exactly zero. It's release-day drift. gpt-5.2 x2 submitted 2025-12-11 (release day),
gemini-3-pro 2025-11-18 (release day), deepseek-reasoner 2025-12-01 (V3.2 launch day, where the
pinned table row still shows $0.55/$2.19 against the $0.28/$0.42 the run was billed at).
Per-instance ratios vary 0.79 to 0.95 within each entry, so no single price or accounting swap
reproduces it. Submissions cluster on model release day, which is exactly when the community
price table is least likely to be correct.

**Price staleness** (curated vs litellm-pinned, 13 verified entries): +$31.39, +0.9%. Small.

**Four incompatible trajectory formats** across one scaffold family's own leaderboard
submissions: mini messages schema, raw Responses objects, classic SWE-agent
trajectory/history/environment, and a bare `.traj` vs `.traj.json` filename split.

**Billed calls with no token record:** 1,726 of 873,531 = 0.198%. Mechanism identified as "no
tool call in response" retries. Concentrated: Kimi-K2.5-high alone accounts for 902 of them
across 248 of 500 instances, up to 27 retries on a single instance.

## How to reproduce

This repo is the analysis code and its output (`data/out/*.csv`) plus the extracted parquet
(`data/instances/`, `data/calls/`). It is not a runnable package on its own: `config.py` resolves
its paths as `Path(__file__).parent.parent.parent`, on the assumption that these modules live at
`analysis/token_waste/` inside a clone of
[SWE-bench/experiments](https://github.com/SWE-bench/experiments) (that's where
`evaluation/verified/<entry>/metadata.yaml` and `per_instance_details.json` come from — this repo
does not ship them). Running the CLI straight from a bare clone of this repo fails: `python -m
cli` raises `ImportError: attempted relative import with no known parent package` (no package
context), and even `python -m token_waste.cli` from one directory up finds no entries, silently,
because `evaluation/` isn't there to find.

To actually run it:

```
git clone https://github.com/SWE-bench/experiments.git
git clone https://github.com/amnry/swe-bench-token-waste.git experiments/analysis/token_waste
cd experiments
python -m analysis.token_waste.cli claim1
```

Network requirements per step:

| Step | Command | Network |
|---|---|---|
| Claim 1 | `python -m analysis.token_waste.cli claim1` | none — reads local `metadata.yaml` / `per_instance_details.json` from the `experiments` clone above and the parquet shipped in this repo |
| Extraction | `python -m analysis.token_waste.extract` | public S3 (streams, caches nothing locally) |
| Claim 2 pricing/convention | see `cli.py` | none — reads the parquet and yaml already in this repo |

Nothing in this pipeline calls Hugging Face. Claim 1 and the convention detectors run entirely
against the parquet and CSVs already committed in `data/`, once the `experiments` layout above is
in place; only re-running extraction from scratch touches the network, and that touches S3, not
HF.

## Method and scope

**Three-way null taxonomy:** `structurally_absent` (provider doesn't bill this, true value 0),
`known_zero` (feature off for this run), `unknown` (genuinely missing, propagates as null into
both numerator and denominator). Only `unknown` shrinks a denominator.

**Dual-run pricing:** `R'_conv` = our convention logic with litellm's own price table pinned to
the commit current at each entry's submission date. `R'_full` = our convention logic with curated
`prices.yaml`. This separates the convention effect from price staleness so neither can be
dismissed as "you used different prices."

`provider_flags.yaml` describes RAW provider APIs, verified with quoted citations. The
trajectories contain litellm-NORMALIZED counts, detected per entry by `cache_convention.py`.
Pricing consumes the per-entry detection, never the provider-level flag.

## Limitations and stated assumptions

- One scaffold family; roughly 25 entries carry per-call tokens.
- Benchmark runs, not production: no retry loop, no human escalation, no customer.
- Leaderboard entries are voluntarily submitted, filtered toward runs people were willing to
  publish, so 52.9% is plausibly a floor.
- 7 of 48 entries excluded for missing per-instance data. They resolve at 29.9% vs 60.5% for the
  included set, so including them would likely push waste above 52.9%, not below. Unconfirmed.
- Price coverage: `prices.yaml` has 10 rows, 9 verified, covering 78.1% of B-tier dollars. Gemini
  3 Pro/Flash preview rates recovered from web.archive.org captures dated 2025-11-20 and
  2026-02-17 which agree verbatim (Pro Preview $2.00/$0.20/$12.00 at or below 200k, $4/$0.40/$18
  above; Flash Preview $0.50/$0.05/$3.00). gpt-5.1-codex assumed equal to gpt-5.1, unverified, and
  moot given (c) above.
- `cache_write` field coverage: 20.8% measured, 13.4% structurally_absent, 65.8% unknown. The
  cache READ convention is the primary result and has full coverage; the cache WRITE premium is
  secondary and rests on 20.8%.
- litellm rows lacking a cache-read rate bill cached tokens at full input, matching litellm's own
  behavior, so `R'_conv` inherits litellm's overcharge on those rows.
- Reasoning is excluded from R' entirely and tracked in a separate column, so the primary result
  is uncontaminated by the unresolved reasoning mechanism.
- Waste bounds collapsing to four identical decimals is an untested code path, not a tight bound.
  Only 59 instances are missing, all in one entry with no cost data to impute from.
- Long-context gate not tripped: max 0.39% of dollars.
- Requested-vs-answering model mismatch is explicitly NOT a finding. 46,048 apparent mismatches
  collapsed to routing prefixes and snapshot aliases, leaving one real fallback (sonnet-4-5 to
  sonnet-4) which is dollar-neutral since both share a rate.

## What is next (M6)

1. Why do failed attempts cost 1.51x successes? Candidate mechanisms: turn-limit exhaustion,
   context growth over the trajectory, reasoning burned on unsolvable instances. This is the
   bridge from Claim 1 to Claim 2.
2. Does `n_calls_unaccounted` correlate with resolution? Kimi's 902 tool-call-format retries
   across 248/500 instances is the test case.
3. Does the reasoning undercount concentrate in resolved or unresolved instances? If not, 52.9%
   stands unchanged and we say so explicitly.
