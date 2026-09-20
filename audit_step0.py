"""Step 0: four gate checks on the Claim 1 result before milestone 3.

Ad-hoc audit, not a plan.md milestone -- run once, read the report, decide
whether 52.9% is safe to publish. No network.

    python -m analysis.token_waste.audit_step0
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path

from .claim1 import OUT_DIR, load_all_entry_stats, waste_by
from .entries import select_entries

REASON_NO_DETAILS = "no per_instance_details.json in repo"


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Check 1: entry-count reconciliation / exclusion bias
# ---------------------------------------------------------------------------


def check1_exclusions(out_dir: Path = OUT_DIR) -> dict:
    all_entries = select_entries()
    included = {e.entry for e in all_entries if e.has_details}
    excluded = [e for e in all_entries if e.entry not in included]

    rows = [
        dict(
            entry=e.entry,
            reason_excluded=REASON_NO_DETAILS,
            has_per_instance_details=e.has_details,
            reported_cost=e.reported_cost_usd,
            reported_resolved_pct=e.reported_resolved_pct,
        )
        for e in excluded
    ]
    _write_csv(rows, out_dir / "claim1_exclusions.csv")

    included_entries = [e for e in all_entries if e.entry in included]

    def _stats(entries, field_):
        vals = [getattr(e, field_) for e in entries if getattr(e, field_) is not None]
        if not vals:
            return None, None
        return statistics.mean(vals), statistics.median(vals)

    inc_cost_mean, inc_cost_med = _stats(included_entries, "reported_cost_usd")
    exc_cost_mean, exc_cost_med = _stats(excluded, "reported_cost_usd")
    inc_res_mean, inc_res_med = _stats(included_entries, "reported_resolved_pct")
    exc_res_mean, exc_res_med = _stats(excluded, "reported_resolved_pct")

    return dict(
        n_total=len(all_entries),
        n_included=len(included_entries),
        n_excluded=len(excluded),
        excluded_entries=[e.entry for e in excluded],
        included_cost_mean=inc_cost_mean,
        included_cost_median=inc_cost_med,
        excluded_cost_mean=exc_cost_mean,
        excluded_cost_median=exc_cost_med,
        included_resolved_mean=inc_res_mean,
        included_resolved_median=inc_res_med,
        excluded_resolved_mean=exc_res_mean,
        excluded_resolved_median=exc_res_med,
    )


# ---------------------------------------------------------------------------
# Check 2: pooled vs per-entry waste distribution
# ---------------------------------------------------------------------------


def check2_pooled_vs_per_entry(stats) -> dict:
    per_entry_waste = [s.waste_lower for s in stats if s.waste_lower is not None]
    per_entry_waste.sort()
    n = len(per_entry_waste)
    median = statistics.median(per_entry_waste)
    if n >= 4:
        q1, _, q3 = statistics.quantiles(per_entry_waste, n=4)
    else:
        q1, q3 = per_entry_waste[0], per_entry_waste[-1]

    pooled_row = waste_by(stats, "entry")  # per-entry rows, reuse for pooled via all stats
    pooled = _pooled_waste_lower(stats)

    return dict(
        n_entries=n,
        per_entry_median=median,
        per_entry_q1=q1,
        per_entry_q3=q3,
        per_entry_min=per_entry_waste[0],
        per_entry_max=per_entry_waste[-1],
        pooled_waste_lower=pooled,
        pooled_within_iqr=(q1 <= pooled <= q3),
    )


def _pooled_waste_lower(stats) -> float:
    total_cost = sum(s.total_cost_observed for s in stats)
    unresolved_cost = sum(s.unresolved_cost_observed for s in stats)
    return unresolved_cost / total_cost if total_cost > 0 else float("nan")


# ---------------------------------------------------------------------------
# Check 3: REPORTED (metadata info.cost) vs COMPUTED (sum per_instance cost)
# ---------------------------------------------------------------------------


def check3_reported_vs_computed(out_dir: Path = OUT_DIR) -> list[dict]:
    entries = {e.entry: e for e in select_entries() if e.has_details}
    stats = {s.entry: s for s in load_all_entry_stats([entries[k] for k in entries])}

    rows = []
    for name, e in entries.items():
        s = stats[name]
        reported = e.reported_cost_usd
        computed = s.total_cost_observed  # sum of non-null per-instance costs
        delta = None if reported is None else reported - computed
        delta_rel = None if not reported else delta / reported
        rows.append(
            dict(
                entry=name,
                reported=reported,
                computed=computed,
                delta=delta,
                delta_rel=delta_rel,
                n_cost_missing=s.n_cost_missing,
            )
        )
    rows.sort(key=lambda r: abs(r["delta_rel"] or 0), reverse=True)
    _write_csv(rows, out_dir / "claim1_reported_vs_computed.csv")
    return rows


# ---------------------------------------------------------------------------
# Check 4: cost_per_resolved vs cost_per_unresolved
# ---------------------------------------------------------------------------


def check4_resolved_vs_unresolved(stats) -> dict:
    from .claim1 import _aggregate

    agg = _aggregate(stats)
    per_entry = []
    for s in stats:
        per_entry.append(
            dict(
                entry=s.entry,
                cost_per_resolved=s.cost_per_resolved,
                cost_per_unresolved=s.cost_per_unresolved,
                ratio=(
                    s.cost_per_unresolved / s.cost_per_resolved
                    if s.cost_per_resolved and s.cost_per_unresolved is not None
                    else None
                ),
            )
        )
    return dict(
        pooled_total_spend_per_resolved=agg["total_spend_per_resolved_instance"],
        pooled_cost_per_resolved=agg["cost_per_resolved_instance"],
        pooled_cost_per_unresolved=agg["cost_per_unresolved_instance"],
        pooled_ratio_unresolved_over_resolved=agg["unresolved_resolved_cost_ratio"],
        per_entry=per_entry,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def run() -> None:
    stats = load_all_entry_stats()

    print("=" * 78)
    print("STEP 0 CHECK 1 -- entry-count reconciliation / exclusion bias")
    print("=" * 78)
    c1 = check1_exclusions()
    print(f"total mini entries: {c1['n_total']}  included: {c1['n_included']}  excluded: {c1['n_excluded']}")
    print(f"excluded: {c1['excluded_entries']}")
    print(
        f"included reported_cost  mean={c1['included_cost_mean']:.2f}  median={c1['included_cost_median']:.2f}"
    )
    if c1["excluded_cost_mean"] is not None:
        print(
            f"excluded reported_cost  mean={c1['excluded_cost_mean']:.2f}  median={c1['excluded_cost_median']:.2f}"
        )
    print(
        f"included reported_resolved_pct  mean={c1['included_resolved_mean']:.2f}  median={c1['included_resolved_median']:.2f}"
    )
    if c1["excluded_resolved_mean"] is not None:
        print(
            f"excluded reported_resolved_pct  mean={c1['excluded_resolved_mean']:.2f}  median={c1['excluded_resolved_median']:.2f}"
        )
    print("-> wrote data/out/claim1_exclusions.csv")

    print()
    print("=" * 78)
    print("STEP 0 CHECK 2 -- pooled vs per-entry waste distribution")
    print("=" * 78)
    c2 = check2_pooled_vs_per_entry(stats)
    print(f"n entries = {c2['n_entries']}")
    print(f"per-entry waste_lower: min={c2['per_entry_min']:.4f} Q1={c2['per_entry_q1']:.4f} "
          f"median={c2['per_entry_median']:.4f} Q3={c2['per_entry_q3']:.4f} max={c2['per_entry_max']:.4f}")
    print(f"pooled waste_lower = {c2['pooled_waste_lower']:.4f}   within IQR? {c2['pooled_within_iqr']}")

    print()
    print("=" * 78)
    print("STEP 0 CHECK 3 -- REPORTED (info.cost) vs COMPUTED (sum per_instance cost)")
    print("=" * 78)
    c3 = check3_reported_vs_computed()
    print(f"{'entry':<55} {'reported':>10} {'computed':>10} {'delta':>10} {'delta_rel':>10}")
    for r in c3:
        rep = r["reported"]
        comp = r["computed"]
        d = r["delta"]
        dr = r["delta_rel"]
        print(
            f"{r['entry']:<55} "
            f"{'' if rep is None else round(rep,2):>10} "
            f"{round(comp,2):>10} "
            f"{'' if d is None else round(d,2):>10} "
            f"{'' if dr is None else f'{dr:.2%}':>10}"
        )
    print("-> wrote data/out/claim1_reported_vs_computed.csv")

    print()
    print("=" * 78)
    print("STEP 0 CHECK 4 -- cost_per_resolved vs cost_per_unresolved")
    print("=" * 78)
    c4 = check4_resolved_vs_unresolved(stats)
    print(f"[throughput, plan.md step-6 literal formula: total spend / n_resolved]")
    print(f"pooled total_spend_per_resolved_instance = {c4['pooled_total_spend_per_resolved']:.4f}")
    print()
    print(f"[per-instance, mean $ of an instance IN that outcome bucket -- directly comparable]")
    print(f"pooled cost_per_resolved_instance   = {c4['pooled_cost_per_resolved']:.4f}")
    print(f"pooled cost_per_unresolved_instance = {c4['pooled_cost_per_unresolved']:.4f}")
    print(f"pooled ratio (unresolved / resolved) = {c4['pooled_ratio_unresolved_over_resolved']:.4f}")


if __name__ == "__main__":
    run()
