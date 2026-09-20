SWE-bench token-waste pipeline — analysis/token_waste/

Thesis (two independent claims)

Claim 1 — economic. A material share of mini-SWE-agent spend on SWE-bench Verified goes to
instances the submission did not resolve. Needs only per_instance_details.json {cost, api_calls,
resolved}. No S3, no traj parsing, no price table, no network. Ships first and stands alone.

Claim 2 — accounting. Per-task cost as reported by the tooling (litellm via mini-swe-agent) is
not correctly computed: cache conventions, reasoning tokens, model fallbacks. Needs per-call
tokens + prices. Tier B only (~25 entries). Hard failure at S3 or pricing must leave Claim 1
publishable untouched.

Scope

In: SWE-bench Verified split; leaderboard entries with info.mini-swe-agent_version in
metadata.yaml. Framed as controlled comparison — scaffold held constant, model varies.

Entry count (reconciled 2026-09-13): 48 mini entries by metadata flag. Of 48, 41 carry per_instance_details.json → Claim 1 set = 41. 7 are metadata-only
(no results/, no per_instance_details): six v0.0.0/v1.0.0 runs (gpt-4.1-mini, gpt-4o,
Llama-4-Scout, gemini-2.0-flash, gemini-2.5-flash, gpt-4.1) + 20260219_mini-v2.0.0_gpt-5-2-codex.
For these 7: Claim 1 uses entry-level info.cost/info.resolved only, no per-instance row; in M4
check S3 bash-only/<entry>/ for results.json or per_instance_details and promote if found.
gpt-5-2-codex stays in Claim 2 candidate set (B_responses, trajs on S3) but its per-instance
resolved flag must come from S3 or it is dropped from waste tables.

Out (explicit): non-mini scaffolds; multilingual and lite splits; the 8 S3-only runs with no
leaderboard entry; enforcement / prediction / cost optimization; tool-call and non-LLM cost
(sandbox, eval harness); 1h vs 5m cache TTL attribution; any claim about production agent
economics.

Claim 2 N limitation — decided: accept as stated limitation. No non-mini traj parser. Rationale:
mini-only is the design (scaffold constant); adding one other scaffold gives N≈+5 at cost of a
second parser and a confound. README states "N≈25 entries, one scaffold family".

Context (from exploration)

- Only mini-SWE-agent entries carry per-instance cost (per_instance_details.json: cost,
  api_calls, resolved; no tokens).
- Tokens only inside trajs on public S3 swe-bench-submissions, prefix
  bash-only/<entry>/trajs/<iid>/<iid>.traj.json (from assets.trajs; NOT verified/). One entry
  (20260901_mini-v2.4.2_gemini-3-5-flash) on GitHub via assets.repo. 110–580 MB/entry.
  analysis/download_logs.py builds keys from split name → wrong prefix; don't reuse.
- Multilingual trajs: S3 AccessDenied → dropped.
- Three data tiers (verified by sampling every entry):
  - A_dollar (~22, mini v0.0.0–v1.9.1): only info.model_stats.{instance_cost, api_calls}.
  - B_chat (~22, v1.13+): assistant msgs have extra.response.usage (litellm shape) +
    extra.response.model.
  - B_responses (3: 20260217_mini-v2.0.0_gpt-5-2-high, 20260217_mini-v2.0.0_gpt-5-mini,
    20260219_mini-v2.0.0_gpt-5-2-codex): assistant turns are raw Responses objects — no role
    key; have object, model, usage{input_tokens, input_tokens_details.cached_tokens,
    output_tokens, output_tokens_details.reasoning_tokens}.
- Fallback confirmed: 20250929_mini-v1.13.3_sonnet-4-5-20250929 requested claude-sonnet-4-5,
  response claude-sonnet-4-20250514.
- Fixtures: analysis/test_data/claude-4-6-opus.json (B_chat), gpt-5-2-high-response.json
  (B_responses).
- Reuse: analysis/validate_entries.py → EVALUATION, _metadata_path, all_entries.
  bash_only_get_extra_info.get_traj_info two-branch model_stats lookup (copy logic).

Decisions (user-confirmed)

┌─────────────┬──────────────────────────────────────────────────────────────────────────────┐
│    Topic    │                                   Decision                                   │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Scope       │ Verified split, mini-SWE-agent leaderboard entries only (48; 41 w/ details)  │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Headline    │ Per-submission unresolved. Global never-resolved set computed, one table,    │
│ waste def.  │ not led with (spend on never-solved instance = expected loss, not waste)     │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Cache flag  │ input_includes_cached verified from docs for openai/gemini (true) and        │
│             │ anthropic (false). Every other provider (deepseek, moonshot, minimax, z-ai,  │
│             │ mistral, openrouter, …) listed explicitly with verified: false and EXCLUDED  │
│             │ from Claim 2 until verified. No unverified default. Still in Claim 1.        │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Price key   │ response.model per call → alias table → price key. Log every mismatch vs     │
│             │ requested                                                                    │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Two price   │ (a) litellm model_prices_and_context_window.json pinned to commit current at │
│ tables      │ entry submission_date; (b) curated prices.yaml keyed (provider, model,       │
│             │ effective_from). Hand-verify (b) only for models with material $ share       │
│             │ (expect 6–8). source_url + verified flags on those.                          │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Cost        │ REPORTED = info.cost; COMPUTED = Σ per_instance_details.cost (or             │
│ numbers     │ Σ model_stats.instance_cost from trajs); R′_conv = Σ tokens × litellm table   │
│             │ under my convention; R′_full = Σ tokens × prices.yaml under my convention    │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Deltas      │ R − C (reporting drift); C − R′_conv = convention effect (THE finding);       │
│             │ R′_conv − R′_full = price staleness effect. Never a single C − R′.           │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Tier A      │ Included, dollar-only; token columns null (never 0); data_tier column        │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Missing     │ Three buckets, bounds not point (below)                                      │
│ trajs       │                                                                              │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Imputation  │ Upper bound imputes no_gen_no_traj at entry p75, not median (these runs hit  │
│             │ turn limit or crashed early — bimodal, not central). Report p50 variant as a │
│             │ secondary column waste_upper_p50.                                            │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Long ctx    │ Flag input_raw > 200k at base tier; gate: if flagged calls > 5% of $ →       │
│             │ implement tiering before publishing                                          │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Cache write │ One cache_write_tokens column priced at 5m rate. 1h sensitivity reported as  │
│             │ one number (Σ cache_write × (cw1h − cw5m)) in summary. No TTL column.        │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Grouping    │ entry, model, mini_version, mode. No "scaffold" key (scope is one scaffold)  │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ TOL=0.05    │ Parser smoke test only — "did parse extract the same order of magnitude      │
│             │ litellm did". Not validation that numbers agree. Named SMOKE_TOL.            │
├─────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Storage     │ Stream S3 → parse in memory → parquet in analysis/token_waste/data/. Raw     │
│             │ trajs never on disk                                                          │
└─────────────┴──────────────────────────────────────────────────────────────────────────────┘

Assumptions (stated here and in README)

1. Reasoning tokens are contained within output_tokens for all observed providers (sample:
   gemini completion 82 = 66 reasoning + 16 text). Asserted at parse time; violating rows
   quarantined to data/quarantine/<entry>.parquet with reason, never crash.
2. All cache writes priced at 5m tier. Dollar impact of 1h alternative stated as one number.
3. Cache-inclusivity (input_includes_cached) verified for openai, gemini, anthropic. Every other
   provider listed in provider_flags.yaml with verified: false and excluded from Claim 2.
4. Cost priced at answering model (response.model), not requested. One confirmed fallback:
   20250929_mini-v1.13.3_sonnet-4-5-20250929 requested claude-sonnet-4-5, answered
   claude-sonnet-4-20250514. Named in README body, not only mismatch_log.csv.
5. Long-context calls priced at base tier; flagged $ share reported; 5% gate.
6. Tier A contributes to dollar metrics only; token columns null, never zero.
7. SWE-bench runs are benchmark runs, not production workloads: no retry loop, no human
   escalation, no customer. Waste here ≠ production waste.

Missing-traj buckets and bounds

Per entry, every instance in canonical Verified id list (500) classified:

┌────────────────────────┬───────────────────────────────────┬───────────────────────────────┐
│         Bucket         │            Definition             │        Spend treatment        │
├────────────────────────┼───────────────────────────────────┼───────────────────────────────┤
│ observed               │ traj present                      │ actual (incl. no-patch runs — │
│                        │                                   │  most wasteful, real data)    │
├────────────────────────┼───────────────────────────────────┼───────────────────────────────┤
│                        │ no traj AND no patch (not in      │ lower: excluded; upper:       │
│ no_gen_no_traj         │ per_instance_details, not in      │ imputed at entry p75          │
│                        │ all_preds/logs)                   │ per-instance cost/tokens,     │
│                        │                                   │ unresolved (p50 secondary)    │
├────────────────────────┼───────────────────────────────────┼───────────────────────────────┤
│ missing_traj_has_patch │ no traj but patch/report exists   │ excluded both bounds; counted │
└────────────────────────┴───────────────────────────────────┴───────────────────────────────┘

Canonical 500 ids: HF princeton-nlp/SWE-bench_Verified cached to data/verified_instance_ids.json;
fallback = union of ids across all verified results.json lists.
Patch existence: S3 logs/<iid>/ prefix listing (cheap) or all_preds.jsonl.
Every waste table carries waste_lower, waste_upper (p75), waste_upper_p50, n_observed,
n_no_gen_no_traj, n_missing_traj_has_patch, n_observed_no_patch. If bounds within 1pp → note and
move on; else flag gap.

For Claim 1 (per_instance_details only), "observed" = present in per_instance_details;
no_gen_no_traj = not in details and not in results.json; bounds computed same way.

Schema (parquet, data/)

entries — entry, submission_date, model_display, org, requested_model
(info.config.model.model_name), mini_version, mode{textbased,toolcall}, reasoning_effort,
data_tier, has_details, reported_cost_usd, reported_resolved_pct, traj_source{s3,github,none},
n_trajs_found, n_no_gen_no_traj, n_missing_traj_has_patch, claim2_eligible, claim2_excl_reason

instances PK(entry, instance_id) — resolved, bucket, exit_status, has_patch, n_calls,
api_calls_reported, computed_cost_usd, input_tokens_uncached, cached_read_tokens,
cache_write_tokens, output_tokens, reasoning_tokens, tokens_total, recomputed_conv_usd,
recomputed_full_usd, answering_model, model_mismatch, n_long_context_calls

calls PK(entry, instance_id, call_idx) — tier B only — msg_idx, answering_model_raw,
answering_model, provider, input_tokens_raw, cached_read_tokens, cache_write_tokens,
output_tokens, reasoning_tokens, input_tokens_uncached, long_context, recomputed_conv_usd,
recomputed_full_usd, litellm_price_key, price_row_id

prices.yaml — provider, model, effective_from, input_per_m, cached_input_per_m,
cache_write_5m_per_m, cache_write_1h_per_m, output_per_m, source_url, verified, note
litellm_prices/<sha>.json — pinned snapshots of model_prices_and_context_window.json, one per
distinct submission_date needed; sha + date recorded in litellm_pins.yaml
aliases.yaml — raw (exact) | pattern (regex) → provider, model, litellm_key
provider_flags.yaml — provider: {input_includes_cached, cache_write_billed, verified,
source_url}. Every provider present in any traj must have a row; verified:false → Claim 2 excl.

Modules

analysis/token_waste/
  config.py     DATA_DIR, BUCKET_URL, LONG_CTX_THRESHOLD=200_000, GATE=0.05, SMOKE_TOL=0.05,
                IMPUTE_Q=0.75
  entries.py    select_entries() -> [EntryMeta]  (reuses validate_entries.all_entries/
                _metadata_path); reads per_instance_details.json when present
  claim1.py     from entries + per_instance_details only → data/out/claim1_*.csv. No network.
  parse.py      detect_tier(traj), detect_mode(traj), parse_traj(traj) -> (InstanceRow,
                [CallRow], [QuarantineRow])
  s3.py         list_keys(prefix) paginated ?list-type=2 + continuation-token (xml.etree);
                fetch_json(key) requests, 5x backoff; GithubRepoSource (git trees API +
                raw.githubusercontent)
  extract.py    per entry: ThreadPool(16) fetch+parse → data/instances/<entry>.parquet,
                data/calls/<entry>.parquet, data/quarantine/<entry>.parquet (atomic tmp→rename;
                skip if exists unless --force)
  pricing.py    PriceTable (curated) + LitellmTable (pinned json); normalize_model(raw),
                lookup(provider, model, date), price_call(call, table) run twice → conv/full;
                UnknownModelError collected across run, fail loudly with full key list
  metrics.py    Claim 2 joins → data/out/claim2_*.csv
  cli.py        python -m analysis.token_waste.cli {list|claim1|extract|price|metrics|check|all}
  prices.yaml aliases.yaml provider_flags.yaml litellm_pins.yaml litellm_prices/
  tests/test_claim1.py test_pricing.py test_parse.py test_consistency.py
  README.md     thesis, scope, assumptions, schema, flags, limitations

parse.py dispatch

1. any msg with object=="response" and usage → B_responses
2. any role=="assistant" with extra.response.usage → B_chat
3. else A_dollar (needs info.model_stats; else warn, cost null)

B_chat row: input_raw=prompt_tokens; cached=cache_read_input_tokens or
prompt_tokens_details.cached_tokens or 0; cache_write=cache_creation_input_tokens or
prompt_tokens_details.cache_creation_tokens or 0; output=completion_tokens;
reasoning=completion_tokens_details.reasoning_tokens or 0; model=extra.response.model.
B_responses row: input_raw=usage.input_tokens, cached=input_tokens_details.cached_tokens,
output=usage.output_tokens, reasoning=output_tokens_details.reasoning_tokens, model=msg.model.
usage.cost ignored.
Mode: toolcall if any type=="function_call_output" or assistant tool_calls.
Filter keys by .traj.json suffix only (v0.0.0 has .config.yaml siblings). answering_model_raw
None → fallback requested model, model_mismatch=None, log.
reasoning > output → quarantine row (assumption 1), continue.

pricing.py

flags = provider_flags[provider]; if not flags.verified → call priced null, entry
claim2_eligible=false, reason recorded.
uncached = input_raw - cached if flags.input_includes_cached else input_raw
cost = (uncached*input + cached*cached_input + cache_write*cw5m + output*output) / 1e6
Same formula, two tables: conv uses litellm snapshot pinned to entry submission_date (fields
input_cost_per_token, cache_read_input_token_cost, cache_creation_input_token_cost,
output_cost_per_token); full uses prices.yaml.
Anthropic cache-write rows stored explicitly (1.25×), validator checks ratio.
normalize_model: exact alias → regex alias → strip -YYYYMMDD/-YYYY-MM-DD. Validator: all prices
≥ 0, cached_input <= input, every provider has flags row, every alias target exists in both
tables.
Long context: long_context = input_raw > 200k; check prints $ share; if > 5% → add *_long_per_m +
threshold columns and reprice before metrics are final.
1h sensitivity: Σ cache_write × (cw1h − cw5m) / 1e6 per entry → summary column cache_1h_delta_usd.

claim1.py (M2, no network)

- Input: entries with has_details. Spend = per_instance_details.cost; resolved flag from same.
- Waste per group g ∈ {entry, model, mini_version, mode}: Σ cost[unresolved] / Σ cost, lower/upper
  bounds via buckets above (no_gen_no_traj defined vs results.json + details).
- cost_per_resolved_instance = Σ cost / n_resolved per entry (and per group).
- Dispersion per group (observed only): top_decile_share, p50, p90, max, mean. No gini.
- Global never-resolved: ∪ over all evaluation/verified/*/results/results.json[resolved] ∪
  per_instance_details[*].resolved==true → one table claim1_global_unresolved.csv; not headline.
- Outputs: claim1_waste_by_{entry,model,mini_version,mode}.csv,
  claim1_dispersion_by_{…}.csv, claim1_cost_per_resolved.csv, claim1_global_unresolved.csv,
  claim1_summary.csv.
- api_calls per instance also carried → calls-per-resolved as a free secondary.

metrics.py (M6, Claim 2)

- cost_col choices: computed (all tiers), recomputed_conv, recomputed_full (tier B eligible).
  cost_basis column.
- Deltas per entry: reported, computed, r_conv, r_full, d_rc = R−C, d_conv = C−R′_conv,
  d_price = R′_conv−R′_full, d_conv_rel, d_price_rel, n_missing, litellm_sha.
- Waste under each cost basis (same group keys as Claim 1) — shows whether waste share moves
  under corrected accounting.
- Outputs: claim2_cost_deltas.csv, claim2_waste_by_{…}_basis.csv, mismatch_log.csv,
  long_context_report.csv, quarantine_report.csv, provider_coverage.csv (which entries excluded
  and why), claim2_summary.csv (incl. cache_1h_delta_usd).
- Primary result = cache convention (100% field coverage both tiers; OpenAI/Anthropic documented
  opposite conventions). Reasoning convention is secondary (telemetry-integrity finding, $ immaterial).
- Reasoning undercount check: does the ≤$13.85 concentrate in resolved vs unresolved instances? If
  not, 52.9% stands unchanged; say so in README.
- n_calls_unaccounted (per instance, from extract.py) joined into instances; test correlation with
  resolved. Candidate partial explanation of the 1.51x unresolved/resolved cost asymmetry.
- Join extract.py's real bucket split (n_missing_traj_has_patch) back into Claim 1 tables.

Edge cases

- Traj in S3 not in per_instance_details → resolved=None, excluded from waste, flagged.
- Duplicate keys → keep first, log. Zero assistant msgs → n_calls=0.
- Metadata without info.cost / details without cost → nulls, rows kept.
- Free/open-weight models → price by actual provider in alias map (openrouter etc.); provider
  must still have verified flags row or excluded.
- Tier A entries: deltas R−C only.
- 7 metadata-only entries: Claim 1 entry-level only; per-instance rows absent; has_details=false.
- litellm pin: nearest commit ≤ submission_date on main; if model absent in that snapshot →
  UnknownModelError with sha, do not silently use later snapshot.

Verification

- test_claim1.py: synthetic details w/ 3 resolved of 5 → waste, cost_per_resolved, bounds
  exact; p75 vs p50 upper differ as expected.
- test_pricing.py: Anthropic (1000 uncached, 5000 read, 2000 write-5m, 300 out @ Sonnet 4.5 →
  0.01950); OpenAI inclusive-cache case; Gemini; unknown model raises w/ key; date picks
  earlier row; unverified provider → null + reason; same call priced conv vs full differ only
  when tables differ.
- test_parse.py: claude-4-6-opus.json → B_chat, n_calls == api_calls;
  gpt-5-2-high-response.json → B_responses, 20 rows, first call reasoning=100; synthetic tier A;
  reasoning>output → quarantine not raise.
- cli check: SMOKE — tier B eligible entries: |Σr_conv − Σcomputed|/Σcomputed < SMOKE_TOL else
  print outliers (parser sanity, not agreement). Long-context gate report. Bounds-gap report.
  Provider coverage report.
- E2E Claim 1: cli list → cli claim1 → inspect claim1_summary.csv. Zero network.
- E2E Claim 2: cli extract --entry 20260217_mini-v2.0.0_gpt-5-mini → price → metrics → inspect;
  then extract --all (48 entries, GitHub one included, 7 metadata-only checked on S3).

Milestones

1. entries.py + cli list — 48 entries, has_details, tier guess, trajs URL, reported cost. No
   network.
2. claim1.py + test_claim1.py + all claim1_*.csv. Shippable result. Claim 1 done here.
3. parse.py + test_parse.py on fixtures (incl. quarantine path).
4. s3.py + extract.py — one entry, then all; verify bucket counts vs canonical 500; probe S3 for
   the 7 metadata-only entries.
5. pricing.py + YAMLs + litellm pins + test_pricing.py; cli price clean of unknown keys.
   provider_flags rows for every observed provider (verified only for openai/gemini/anthropic
   unless docs found). User reviews prices.yaml for the 6–8 material models before proceeding.
6. metrics.py + cli check — Claim 2 CSVs, deltas, gate + bounds + coverage reports.
7. README: thesis, scope, assumptions 1–7 verbatim, schema, flags, limitations (tier A
   token-less, N≈25 one scaffold, excluded providers, long-ctx bound, 5m cache assumption,
   fallback case named).
