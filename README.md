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

- litellm v2.0.0 meaning: $1,786.35 (baseline; -0.03% vs the $1,786.92 traffic figure)
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
separate leaderboard rows. Excluded as a duplicate; the $12 is unattributed. Of all five pieces of
evidence, this is the cleanest single illustration of the central claim: identical work, two
different reported dollar figures, and nothing in either number tells you that.

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

```
git clone --filter=blob:none --no-checkout --depth 1 https://github.com/SWE-bench/experiments.git
cd experiments && git sparse-checkout init --cone && git sparse-checkout set evaluation/verified && git checkout && cd ..

git clone https://github.com/amnry/swe-bench-token-waste.git token_waste
pip install -r token_waste/requirements.txt

export SWEBENCH_EXPERIMENTS_ROOT="$(pwd)/experiments"
python -m token_waste.cli claim1
```

The `token_waste` in `git clone ... token_waste` is a local directory alias, not a rename of the
GitHub repo — it's needed because `python -m <dir>.cli` requires `<dir>` to be a valid Python
module name, and `swe-bench-token-waste` (hyphens) is not one; `token_waste` (underscore) is. Run
this from the directory that contains both clones as siblings (not from inside either one). This
is the command actually verified to reproduce the numbers below — see "Verified reproduction" —
not a guess at what should work.

Network requirements per step:

| Step | Command | Network |
|---|---|---|
| Claim 1 | `python -m token_waste.cli claim1` | none at run time — reads the local `metadata.yaml` / `per_instance_details.json` from the `experiments` clone above and the parquet already committed in this repo |
| Extraction (only needed to regenerate the parquet from scratch) | `python -m token_waste.extract` | public S3, streams, caches nothing locally |
| Claim 2 pricing/convention | see `cli.py` | none — reads the parquet and yaml already in this repo |

Nothing in this pipeline calls Hugging Face; the only step that touches a network is a from-scratch
re-extraction, and that touches S3, not HF.

**Verified reproduction.** The exact commands above (directory aliased `token_waste`) were run in a
fresh temp directory against a clean clone of this repo. Output of `claim1_summary.csv`:

```
n_observed,...,total_cost_observed,unresolved_cost_observed,waste_lower,waste_upper,...
20441,...,7610.898307406499,4024.1428264469996,0.528734278650252,0.528734278650252,...
```

20,441 attempts, $7,610.90, $4,024.14, 0.5287 — matches the 20,441 / $7,611 / $4,024 / 52.9%
reported above.

**Why the layout matters.** `config.py` resolves the SWE-bench/experiments root as
`SWEBENCH_EXPERIMENTS_ROOT` if set, else `Path(__file__).parent.parent.parent` — a holdover from
when this code lived at `analysis/token_waste/` inside that fork. Point the env var at a sibling
clone of `evaluation/verified/` (that's where `metadata.yaml` and `per_instance_details.json`
live; this repo doesn't ship them) and it works from anywhere. Without the env var, or run from
the wrong directory, it fails in one of two ways: `python -m cli` from inside the repo raises
`ImportError: attempted relative import with no known parent package` (no package context to
resolve the module's own relative imports against); `python -m token_waste.cli` run from the right
directory but with no `experiments` clone alongside it just returns zero entries, silently,
because the metadata directory it's looking for doesn't exist. Cloning as `swe-bench-token-waste`
(the repo's actual name, hyphenated) instead of aliasing to `token_waste` doesn't just risk failure
— relying on hyphens working with `-m` is an undocumented quirk of how Python's import machinery
resolves module names given as strings, not something to depend on in a reproducibility artifact,
so the commands above alias the clone explicitly.

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

## M6: why failed attempts cost more

Scope: the 26 B-tier entries that carry both per-call tokens and a `per_instance_details.json`
(one calls-parquet entry, `20260219_mini-v2.0.0_gpt-5-2-codex`, has no per_instance_details and is
dropped here for the same reason claim1.py drops it — no resolved flag). Run:
`python -m token_waste.cli mechanisms`. Output: `data/out/m6_*.csv`.

The B-tier-local priced ratio (11,835→8,133 resolved / 8,606→4,367 unresolved attempts once
restricted to these 26 entries) is **1.77x**, not the pooled 1.51x — a real composition
difference between this subset and the full 41-entry set, not an error; the three mechanisms
below are decomposed against 1.77x, not 1.51x, and the two numbers should not be conflated.

**1a. Turn/cost-limit exhaustion.** 3.7% of unresolved instances (161 of 4,367) exit with
`LimitsExceeded` (mini-swe-agent's combined step-limit-or-cost-limit exception; the exit status
alone can't distinguish which one fired). Small in count, expensive in dollars: these instances
average $2.31 versus $0.33 for resolved and $0.53 for unresolved instances that exit some other
way. That 3.7% of unresolved instances accounts for **28% of the $ gap** between the average
unresolved and average resolved attempt. Consistent with this: unresolved instances run more
calls overall (median 48 vs 37, mean 58 vs 43) — the limit-exit group is the sharp end of a
broader tendency to run longer before giving up.

**1b. Context growth.** At every relative position in the trajectory (call 1 of N through call N
of N, bucketed into deciles), unresolved instances carry more input tokens per call than resolved
ones — roughly 30-40% more at every decile, not just at the end. Summed over the whole trajectory,
unresolved instances consume **1.93x** the total input tokens of resolved ones (mean 1,501,056 vs
777,956 tokens/instance). This is descriptive, not a $ decomposition — token volume feeds cost
through the pricing tables in Claim 2, and re-deriving a per-mechanism dollar figure here would
duplicate that machinery — but the ratio is close enough to the local 1.77x cost ratio that it is
plausibly most of the story.

**1c. Reasoning burn.** Restricted to the 13 entries where `reasoning_tokens` is cleanly measured
(models classified `subset` in `reasoning_convention_by_model.csv` — no adapter conflation with
output tokens), unresolved instances burn **1.87x** the reasoning tokens of resolved ones (mean
15,255 vs 8,144 tokens/instance; median 5,474 vs 2,961).

**These three don't sum to 1.77x and aren't meant to.** They overlap: an instance that hits the
limit has, by construction, also run more calls, accumulated more context, and (on
reasoning-capable models) burned more reasoning tokens. Only (a) is a clean, non-overlapping
dollar decomposition (28% of the excess, from a well-defined 3.7% subgroup); (b) and (c) are
token-volume evidence for the same underlying pattern — trajectories that don't converge keep
running, and everything downstream of "keeps running" gets more expensive — not independent,
additive causes.

**2. `n_calls_unaccounted` vs resolution.** Pooled across the 26 B-tier entries (12,941 instances):
resolved mean 0.128, unresolved mean 0.144; 5.9% of resolved instances have any unaccounted calls
vs 6.0% of unresolved. Point-biserial correlation: r = -0.009. **No meaningful pooled
correlation.** Kimi-K2.5-high alone (the 902-unaccounted-calls entry) shows a real gap: resolved
mean 1.54 vs unresolved mean 2.44 (r = -0.12, weak but entry-specific) — an outlier, not the
pooled pattern. n_calls_unaccounted is restricted here to the calls-parquet-bearing entries
deliberately: on A_dollar-tier entries with no parsed trajectory at all, `n_calls_unaccounted`
trivially equals `api_calls_reported` for every instance (100% "unaccounted", a different field
meaning, not a genuine accounting gap), and pooling those in inflates both means by roughly two
orders of magnitude for no real signal.

**3. Does the reasoning undercount (Claim 2 evidence (b)) concentrate in unresolved instances?**
Per entry (the 7 `additive`-classified entries, 8 rows since gemini-3-pro-preview spans two
entries): 5 of 7 comparable entries show a *higher* violation fraction (reasoning_tokens >
output_tokens) in unresolved instances than resolved, by 0.1-1.3 percentage points; 2 show the
reverse, by under 0.02 points. Unweighted mean difference across entries: +0.33 percentage points
toward unresolved. A naive pooled-instance average gives the opposite sign (resolved 9.1% vs
unresolved 4.3%) — that's a composition artifact, not a real effect: one entry
(`gemini-3-5-flash-fair`, ~57% violation rate in both groups almost identically) has a 4.4:1
resolved:unresolved instance ratio and dominates the pooled average by instance count alone,
masking the per-entry pattern. **So: yes, it concentrates slightly toward unresolved instances,
not away from them** — but the effect is a fraction of a percentage point on top of an already
immaterial total ($11.34 of $7,611, established in Claim 2 (b)), so even full concentration in
unresolved instances does not move 52.9% in any reportable way. This closes the open item: 52.9%
stands.
