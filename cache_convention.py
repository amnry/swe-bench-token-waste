"""Per-ENTRY detection of the cached-input convention actually present in
the trajs -- the foundation of Claim 2's primary result.

The plan assumed input_includes_cached is a per-provider constant (OpenAI /
Gemini inclusive, Anthropic exclusive). That is true of the providers' RAW
APIs (provider_flags.yaml, verified). It is NOT true of what the trajs
contain, which is litellm's normalized `prompt_tokens`: on
20250929_mini-v1.13.3_sonnet-4-5 (older litellm) prompt_tokens is
EXCLUSIVE of cache reads/writes, on 20260217_mini-v2.0.0_claude-4-6-opus
it is INCLUSIVE. Same provider, same scaffold, opposite convention -- the
mapping changed with the litellm version. So the convention is detected
here, per entry, from the data, and provider_flags.yaml is only the
reference for what the raw API would have said.

Test, over calls where input_raw, cached, cache_write are all measured:
  residual_full = input_raw - cached - cache_write
  residual_read = input_raw - cached
  inclusive       : residual_full >= 0 on ~all calls (input_raw is the total)
  exclusive       : residual_full <  0 on ~all calls where cached+write > 0
  read_only_incl  : residual_read >= 0 but residual_full < 0 (prompt_tokens
                    = input + cache_read, write NOT folded in -- what the
                    sonnet-4-5 entry shows)
  undetermined    : cached+write is 0 on nearly every call (no cache traffic,
                    convention unobservable) or evidence split

Writes data/out/cache_convention_by_entry.csv. pricing.py reads it.

    python -m analysis.token_waste.cache_convention
"""

from __future__ import annotations

import csv

import pyarrow.parquet as pq

from .claim1 import OUT_DIR
from .config import DATA_DIR
from .entries import select_entries
from .providers import infer_provider, load_provider_flags

CALLS_DIR = DATA_DIR / "calls"
MIN_CACHE_CALLS = 100  # calls with cached+write > 0 needed to call a convention
DOMINANCE = 0.98  # fraction of cache-traffic calls that must agree


def detect_entry(entry: str) -> dict:
    path = CALLS_DIR / f"{entry}.parquet"
    t = pq.read_table(path)
    if "input_tokens_raw" not in t.column_names:
        return dict(entry=entry, convention="no_calls", n_calls=0)
    rows = t.to_pylist()
    n_calls = len(rows)
    models = sorted({r["answering_model"] for r in rows if r.get("answering_model")})
    provider = infer_provider(models[0]) if models else None

    measured = [
        r for r in rows
        if r.get("input_tokens_raw") is not None and r.get("cached_tokens") is not None
    ]
    # cache_write may legitimately be structurally_absent (0) -- treat None as
    # "not measured" and fall back to the read-only test for those rows.
    with_write = [r for r in measured if r.get("cache_write_tokens") is not None]
    traffic_full = [r for r in with_write if (r["cached_tokens"] + r["cache_write_tokens"]) > 0]
    traffic_read = [r for r in measured if r["cached_tokens"] > 0]

    n_full_neg = sum(1 for r in traffic_full if r["input_tokens_raw"] - r["cached_tokens"] - r["cache_write_tokens"] < 0)
    n_read_neg = sum(1 for r in traffic_read if r["input_tokens_raw"] - r["cached_tokens"] < 0)

    if len(traffic_read) < MIN_CACHE_CALLS:
        convention = "undetermined"
    elif traffic_full and n_full_neg / len(traffic_full) <= 1 - DOMINANCE:
        convention = "inclusive"
    elif traffic_full and n_full_neg / len(traffic_full) >= DOMINANCE:
        convention = "read_only_inclusive" if n_read_neg / len(traffic_read) <= 1 - DOMINANCE else "exclusive"
    elif not traffic_full:
        # no measured cache_write anywhere (OpenAI/Gemini): read test only
        convention = "inclusive" if n_read_neg / len(traffic_read) <= 1 - DOMINANCE else "exclusive"
    else:
        convention = "undetermined"

    raw_flag = ((load_provider_flags().get(provider) or {}).get("raw_input_includes_cached") or {})
    raw_value = raw_flag.get("value")
    raw_verified = bool(raw_flag.get("verified"))
    litellm_inclusive = convention in ("inclusive", "read_only_inclusive")
    matches_raw = None if raw_value is None or convention == "undetermined" else (litellm_inclusive == raw_value)

    return dict(
        entry=entry,
        provider=provider,
        models=";".join(models),
        n_calls=n_calls,
        n_cache_traffic_calls=len(traffic_read),
        n_with_cache_write_measured=len(with_write),
        pct_full_residual_negative=round(n_full_neg / len(traffic_full) * 100, 2) if traffic_full else None,
        pct_read_residual_negative=round(n_read_neg / len(traffic_read) * 100, 2) if traffic_read else None,
        convention=convention,
        raw_api_input_includes_cached=raw_value,
        raw_api_verified=raw_verified,
        litellm_matches_raw_api=matches_raw,
    )


def run() -> None:
    rows = []
    for e in select_entries():
        if not (CALLS_DIR / f"{e.entry}.parquet").is_file():
            continue
        rows.append(detect_entry(e.entry))
    out = OUT_DIR / "cache_convention_by_entry.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}\n")
    print(f"{'entry':<52} {'prov':<10} {'convention':<20} {'traffic':>8} {'full<0%':>8} {'read<0%':>8} {'raw_incl':>9} {'match':>6}")
    for r in rows:
        if r.get("convention") == "no_calls":
            continue
        print(f"{r['entry']:<52} {str(r['provider']):<10} {r['convention']:<20} {r['n_cache_traffic_calls']:>8} "
              f"{str(r['pct_full_residual_negative']):>8} {str(r['pct_read_residual_negative']):>8} "
              f"{str(r['raw_api_input_includes_cached']):>9} {str(r['litellm_matches_raw_api']):>6}")


if __name__ == "__main__":
    run()
