"""M6 analysis: why failed attempts cost more, and whether two telemetry
artifacts (unaccounted retries, reasoning undercount) contaminate the Claim 1
waste figure.

Three questions (see plan.md "metrics.py (M6, Claim 2)" and README "What is next"):
  Q1  Why do unresolved attempts cost 1.51x resolved ones?
  Q2  Does n_calls_unaccounted correlate with resolution?
  Q3  Does the reasoning undercount concentrate in unresolved instances?

Data sources, same contract as claim1.py: per_instance_details.json {cost,
api_calls, resolved} from the experiments clone (via load_per_instance_details)
joined to the per-call parquet already committed under data/. No network.
"""

from __future__ import annotations

import csv
import glob
import math
from pathlib import Path

import pandas as pd

from .config import DATA_DIR
from .entries import load_per_instance_details, select_entries

OUT_DIR = DATA_DIR / "out"
B_TIERS = ("B_chat", "B_responses")


def _details_frame() -> pd.DataFrame:
    rows = []
    for e in select_entries():
        det = load_per_instance_details(e)
        if not det:
            continue
        for iid, rec in det.items():
            rows.append(
                dict(entry=e.entry, instance_id=iid, resolved=rec.get("resolved"),
                     cost=rec.get("cost"), api_calls=rec.get("api_calls"))
            )
    return pd.DataFrame(rows)


def _instances_frame() -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA_DIR / "instances" / "*.parquet")))
    cols = ["entry", "instance_id", "tier", "exit_status", "n_calls_unaccounted"]
    frames = []
    for f in files:
        df = pd.read_parquet(f)
        # a few metadata-only entries ship a stub instances parquet with no rows
        # / partial schema; reindex so the concat is column-aligned.
        frames.append(df.reindex(columns=cols))
    return pd.concat(frames, ignore_index=True)


def _call_aggregates() -> pd.DataFrame:
    frames = []
    num = ["input_tokens_raw", "output_tokens", "reasoning_tokens"]
    for f in sorted(glob.glob(str(DATA_DIR / "calls" / "*.parquet"))):
        df = pd.read_parquet(f)
        if not len(df):
            continue
        for c in num:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        g = df.groupby(["entry", "instance_id"]).agg(
            n_calls_measured=("call_idx", "count"),
            input_tokens_raw=("input_tokens_raw", "sum"),
            output_tokens=("output_tokens", "sum"),
            reasoning_tokens=("reasoning_tokens", "sum"),
            reasoning_exceeds_output_n=("reasoning_exceeds_output", "sum"),
        ).reset_index()
        frames.append(g)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_master() -> pd.DataFrame:
    """One row per (entry, instance): resolved/cost + parquet tokens/exit_status."""
    det = _details_frame()
    if det.empty:
        return det
    m = det.merge(_instances_frame(), on=["entry", "instance_id"], how="left")
    agg = _call_aggregates()
    if not agg.empty:
        m = m.merge(agg, on=["entry", "instance_id"], how="left")
    return m


def _corr(a: pd.Series, b: pd.Series) -> float:
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(a.corr(b))


def cost_asymmetry(m: pd.DataFrame) -> pd.DataFrame:
    """Q1: decompose unresolved/resolved cost ratio into calls x cost-per-call."""
    p = m[m.cost.notna()]
    rows = []
    for e, g in p.groupby("entry"):
        u, r = g[~g.resolved.astype(bool)], g[g.resolved.astype(bool)]
        if not len(u) or not len(r):
            continue
        rc = u.cost.mean() / r.cost.mean()
        rcall = u.api_calls.mean() / r.api_calls.mean()
        rows.append(dict(
            entry=e, tier=g.tier.iloc[0], n_unresolved=len(u), n_resolved=len(r),
            mean_cost_unresolved=round(u.cost.mean(), 4), mean_cost_resolved=round(r.cost.mean(), 4),
            cost_ratio=round(rc, 4), calls_ratio=round(rcall, 4),
            cost_per_call_ratio=round(rc / rcall, 4),
            mean_calls_unresolved=round(u.api_calls.mean(), 1),
            mean_calls_resolved=round(r.api_calls.mean(), 1),
        ))
    return pd.DataFrame(rows).sort_values("cost_ratio", ascending=False)


def unaccounted(m: pd.DataFrame) -> pd.DataFrame:
    """Q2: per-entry n_calls_unaccounted vs resolution (tier B only)."""
    b = m[m.tier.isin(B_TIERS)].copy()
    b["unacc"] = b.n_calls_unaccounted.fillna(0).astype(int)
    rows = []
    for e, g in b.groupby("entry"):
        u, r = g[~g.resolved.astype(bool)], g[g.resolved.astype(bool)]
        has, no = g[g.unacc > 0], g[g.unacc == 0]
        rows.append(dict(
            entry=e, total_unaccounted=int(g.unacc.sum()),
            n_instances_with_any=int((g.unacc > 0).sum()), max_on_one_instance=int(g.unacc.max()),
            mean_unacc_unresolved=round(u.unacc.mean(), 3), mean_unacc_resolved=round(r.unacc.mean(), 3),
            resrate_with_retries=round(has.resolved.mean(), 4) if len(has) else float("nan"),
            resrate_without=round(no.resolved.mean(), 4) if len(no) else float("nan"),
            corr_resolved_unacc=round(_corr(g.resolved.astype(int), g.unacc), 4),
        ))
    return pd.DataFrame(rows).sort_values("total_unaccounted", ascending=False)


def reasoning_concentration(m: pd.DataFrame) -> pd.DataFrame:
    """Q3: is reasoning-token spend concentrated in unresolved instances?"""
    b = m[m.tier.isin(B_TIERS)].copy()
    b["reasoning_tokens"] = b.reasoning_tokens.fillna(0)
    b["rex"] = b.reasoning_exceeds_output_n.fillna(0)
    rp = b[(b.reasoning_tokens > 0) & b.cost.notna()]
    rows = []
    for e, g in rp.groupby("entry"):
        if g.reasoning_tokens.sum() == 0:
            continue
        gu = g[~g.resolved.astype(bool)]
        sr = gu.reasoning_tokens.sum() / g.reasoning_tokens.sum()
        sc = gu.cost.sum() / g.cost.sum()
        rows.append(dict(
            entry=e, reasoning_tokens_M=round(g.reasoning_tokens.sum() / 1e6, 3),
            unresolved_share_reasoning=round(sr, 4), unresolved_share_cost=round(sc, 4),
            diff_pp=round((sr - sc) * 100, 2),
            reasoning_per_attempt_unres=round(gu.reasoning_tokens.mean(), 0),
            reasoning_per_attempt_res=round(g[g.resolved.astype(bool)].reasoning_tokens.mean(), 0),
            rex_calls=int(g.rex.sum()),
        ))
    return pd.DataFrame(rows).sort_values("reasoning_tokens_M", ascending=False)


def summary(m: pd.DataFrame) -> dict:
    p = m[m.cost.notna()]
    u, r = p[~p.resolved.astype(bool)], p[p.resolved.astype(bool)]
    ratio = u.cost.mean() / r.cost.mean()
    rcall = u.api_calls.mean() / r.api_calls.mean()
    b = m[m.tier.isin(B_TIERS)].copy()
    b["unacc"] = b.n_calls_unaccounted.fillna(0)
    b["reasoning_tokens"] = b.reasoning_tokens.fillna(0)
    rp = b[(b.reasoning_tokens > 0) & b.cost.notna()]
    rpx = rp[~rp.resolved.astype(bool)].reasoning_tokens.sum() / rp.reasoning_tokens.sum()
    limit_re = r"Limit|Error|cost"
    pct_limit = u.exit_status.astype(str).str.contains(limit_re, case=False).mean()
    # waste-shift ceiling: attribute the full reasoning $ proxy to unresolved
    base_unres, base_tot, reason_proxy = 4024.1428264469996, 7610.898307406499, 11.34
    shift = (base_unres + reason_proxy) / (base_tot + reason_proxy) - base_unres / base_tot
    return dict(
        pooled_cost_ratio=round(ratio, 4), pooled_calls_ratio=round(rcall, 4),
        pooled_cost_per_call_ratio=round(ratio / rcall, 4),
        pct_unresolved_died_at_limit_or_error=round(float(pct_limit), 4),
        q2_pooled_corr_resolved_unacc=round(_corr(b.resolved.astype(int), b.unacc), 4),
        q2_tierB_unaccounted_rate=round(float(b.unacc.sum() / b.api_calls.sum()), 5),
        q3_unresolved_share_reasoning=round(float(rpx), 4),
        q3_reasoning_dollar_proxy_usd=reason_proxy,
        q3_waste_shift_if_all_reasoning_unresolved_pp=round(shift * 100, 3),
    )


def run(out_dir: Path = OUT_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = build_master()
    if m.empty:
        raise SystemExit("no per_instance_details found -- set SWEBENCH_EXPERIMENTS_ROOT "
                         "to a clone of evaluation/verified (see README).")
    written: dict[str, Path] = {}
    tables = {
        "m6_cost_asymmetry_by_entry.csv": cost_asymmetry(m),
        "m6_unaccounted_by_entry.csv": unaccounted(m),
        "m6_reasoning_concentration_by_entry.csv": reasoning_concentration(m),
    }
    for name, df in tables.items():
        path = out_dir / name
        df.to_csv(path, index=False)
        written[name] = path
    s = summary(m)
    path = out_dir / "m6_summary.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(s))
        w.writeheader()
        w.writerow(s)
    written["m6_summary.csv"] = path
    return written


if __name__ == "__main__":
    for name, path in sorted(run().items()):
        print(f"wrote {path}")
