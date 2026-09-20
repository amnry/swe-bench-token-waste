"""Per-MODEL reasoning_in_output classification, plus a rough $ estimate.
Ad-hoc, run once after extract --all; not a plan.md milestone module and
NOT pricing.py -- the dollar figure is a crude proxy (entry's own reported
$ / its own token volume), not milestone 5's real per-token pricing. The
CSV column is named to say so, so it cannot be quoted as a Claim 2 number.

WHAT THIS IS A FINDING ABOUT -- telemetry integrity, not billing:

  Across 873,531 calls, models from 5 of 8 providers emit reasoning_tokens
  > output_tokens, violating the documented invariant of the SDK that
  reported them (litellm's standard, inherited from OpenAI: reasoning is a
  subset of output). Anthropic and OpenAI models never do. Cost impact is
  immaterial (~$13.85 on $7,611 = 0.18%). The point is that the number a
  cost tool reads is not reliably the number the provider billed, and
  nothing in the stack detects it -- litellm computed instance_cost from
  these same fields at run time and no assertion anywhere fired.

Why per MODEL (raw answering_model string, routing prefix kept), not per
provider: the leading explanation is a litellm/vLLM adapter bug, and
adapters vary by serving path, not vendor. gemini-3-pro-preview at ~0.01%
violations must not inherit gemini-3-flash-high's 12% just because both
are Google. Keeping the routing prefix ("moonshot/kimi-k2-thinking" vs
"moonshotai/kimi-k2.5") preserves the serving-path signal.

Classification per model:
  additive     -- any call with reasoning > output; confidence "clear" if
                  violation rate >= 1%, "ambiguous" below that (visible,
                  not hidden behind the any-violation rule)
  subset       -- >= 100 calls with both fields measured, 0 violations
  no_evidence  -- < 100 calls with both fields measured. A default must
                  not look like a finding.

Two caveats travel with the finding, always:
  1. DETECTION FLOOR, not incidence. The comparison only fires when
     reasoning happens to exceed output on a given call.
  2. MECHANISM UNRESOLVED. Genuine billing convention vs litellm/vLLM
     adapter bug (BerriAI/litellm#24526 reproduces the symptom exactly;
     Google's own docs disagree with themselves on Gemini API vs Vertex
     AI). Nothing here is verified against a provider doc -- that lives in
     provider_flags.yaml, and every non-Anthropic/OpenAI row there is
     verified: false.

    python -m analysis.token_waste.reasoning_convention
"""

from __future__ import annotations

import csv
from pathlib import Path

import pyarrow.parquet as pq

from .claim1 import OUT_DIR
from .config import DATA_DIR
from .entries import select_entries
from .providers import infer_provider

CALLS_DIR = DATA_DIR / "calls"
MIN_EVIDENCE_CALLS = 100
AMBIGUOUS_BELOW_PCT = 1.0

DOLLAR_COL = "est_dollar_undercount_ROUGH_PROXY_not_a_claim2_number"
DOLLAR_NOTE = (
    "proxy = entry reported $ / entry (input_raw+output) tokens, x extra reasoning tokens; "
    "blended rate, ignores cached-vs-uncached input pricing; sized to show immateriality only"
)


def _iter_calls():
    for path in sorted(CALLS_DIR.glob("*.parquet")):
        t = pq.read_table(path)
        if "answering_model" not in t.column_names:
            continue
        entry = path.stem
        for row in t.to_pylist():
            row["entry"] = entry
            yield row


def classify_by_model() -> dict[str, dict]:
    by_model: dict[str, dict] = {}
    for row in _iter_calls():
        model = row.get("answering_model")
        if not model:
            continue
        s = by_model.setdefault(
            model,
            dict(
                model=model,
                provider=infer_provider(model),
                n_calls=0,
                n_both_measured=0,
                n_violating=0,
                sum_reasoning_tokens=0,
                entries=set(),
            ),
        )
        s["n_calls"] += 1
        s["entries"].add(row["entry"])
        reasoning, output = row.get("reasoning_tokens"), row.get("output_tokens")
        if reasoning is not None:
            s["sum_reasoning_tokens"] += reasoning
        if reasoning is not None and output is not None:
            s["n_both_measured"] += 1
            if row.get("reasoning_exceeds_output"):
                s["n_violating"] += 1

    for s in by_model.values():
        n, v = s["n_both_measured"], s["n_violating"]
        s["violation_pct"] = (v / n * 100) if n else None
        if n < MIN_EVIDENCE_CALLS:
            s["classification"], s["confidence"] = "no_evidence", "n/a"
        elif v == 0:
            s["classification"], s["confidence"] = "subset", "clear"
        else:
            s["classification"] = "additive"
            s["confidence"] = "clear" if s["violation_pct"] >= AMBIGUOUS_BELOW_PCT else "ambiguous"
    return by_model


def _entry_price_proxy() -> dict[str, float]:
    """entry -> reported $ / (input_raw + output) tokens. Rough. See DOLLAR_NOTE."""
    out = {}
    entries = {e.entry: e for e in select_entries()}
    for path in CALLS_DIR.glob("*.parquet"):
        e = entries.get(path.stem)
        if e is None or e.reported_cost_usd is None:
            continue
        t = pq.read_table(path)
        if "input_tokens_raw" not in t.column_names:
            continue
        rows = t.to_pylist()
        tokens = sum((r.get("input_tokens_raw") or 0) + (r.get("output_tokens") or 0) for r in rows)
        if tokens > 0:
            out[path.stem] = e.reported_cost_usd / tokens
    return out


def estimate_by_model(by_model: dict[str, dict]) -> None:
    """Adds extra_reasoning_tokens + DOLLAR_COL per additive model. Extra
    tokens = all reasoning tokens of that model (rule: additive applies to
    every call once any call violates), priced by the entry proxy."""
    price = _entry_price_proxy()
    per_model_dollars: dict[str, float] = {}
    per_model_unpriced: dict[str, int] = {}
    for row in _iter_calls():
        model = row.get("answering_model")
        s = by_model.get(model)
        if not s or s["classification"] != "additive":
            continue
        r = row.get("reasoning_tokens") or 0
        p = price.get(row["entry"])
        if p is None:
            per_model_unpriced[model] = per_model_unpriced.get(model, 0) + r
        else:
            per_model_dollars[model] = per_model_dollars.get(model, 0.0) + r * p
    for model, s in by_model.items():
        if s["classification"] == "additive":
            s["extra_reasoning_tokens"] = s["sum_reasoning_tokens"]
            s[DOLLAR_COL] = round(per_model_dollars.get(model, 0.0), 2)
            s["unpriced_reasoning_tokens"] = per_model_unpriced.get(model, 0)
        else:
            s["extra_reasoning_tokens"] = None
            s[DOLLAR_COL] = None
            s["unpriced_reasoning_tokens"] = None


def write_csv(by_model: dict[str, dict], path: Path) -> None:
    cols = [
        "model", "provider", "classification", "confidence", "n_calls", "n_both_measured",
        "n_violating", "violation_pct", "extra_reasoning_tokens", DOLLAR_COL,
        "unpriced_reasoning_tokens", "entries", "note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in sorted(by_model.values(), key=lambda s: (s["provider"] or "", s["model"])):
            row = {c: s.get(c) for c in cols}
            row["entries"] = ";".join(sorted(s["entries"]))
            row["violation_pct"] = None if s["violation_pct"] is None else round(s["violation_pct"], 3)
            row["note"] = DOLLAR_NOTE if s["classification"] == "additive" else ""
            w.writerow(row)


def run() -> None:
    by_model = classify_by_model()
    estimate_by_model(by_model)
    out = OUT_DIR / "reasoning_convention_by_model.csv"
    write_csv(by_model, out)
    print(f"wrote {out}\n")
    print(f"{'model':<42} {'provider':<10} {'class':<12} {'conf':<10} {'both':>7} {'viol':>6} {'viol%':>8} {'$proxy':>8}")
    for s in sorted(by_model.values(), key=lambda s: (s["provider"] or "", s["model"])):
        vp = "" if s["violation_pct"] is None else f"{s['violation_pct']:.3f}"
        d = "" if s[DOLLAR_COL] is None else f"{s[DOLLAR_COL]:.2f}"
        print(f"{s['model']:<42} {str(s['provider']):<10} {s['classification']:<12} {s['confidence']:<10} "
              f"{s['n_both_measured']:>7} {s['n_violating']:>6} {vp:>8} {d:>8}")
    total = sum(s[DOLLAR_COL] or 0 for s in by_model.values())
    print(f"\ntotal {DOLLAR_COL}: ${total:.2f}  ({DOLLAR_NOTE})")


if __name__ == "__main__":
    run()
