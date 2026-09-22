"""M6 follow-up: mechanisms behind the 1.51x unresolved/resolved cost ratio
(Claim 1), plus two closing checks on open items from the README.

Scope: the 26 B-tier entries that carry both per-call tokens (data/calls/*.parquet
is non-empty) and a per_instance_details.json (resolved flag). One entry with
per-call data, 20260219_mini-v2.0.0_gpt-5-2-codex, has no per_instance_details.json
(plan.md's 7-entry exclusion list) and is dropped here for the same reason
claim1.py drops it -- no resolved flag, no denominator.

n_calls_unaccounted is meaningful only where a trajectory was actually parsed
(parse.py sets n_calls_unaccounted = api_calls_reported - n_calls, where n_calls
comes from the parsed traj). A_dollar-tier entries have no parsed traj at all,
so n_calls stays 0 and n_calls_unaccounted trivially equals api_calls_reported
for every single instance -- a different field meaning, not a genuine
call-accounting gap. Restricting to the calls-parquet-bearing entries (the same
set claim2's "1,726 of 873,531" figure is scoped to) avoids mixing the two.

Discipline: every field access goes through .get(...) and a None is dropped
from both sides of whatever ratio it would have fed (see measured.py); no
mean or fraction below is computed over a denominator that silently includes
rows the numerator had to skip.
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from .config import DATA_DIR
from .entries import load_per_instance_details, select_entries
from .extract import CALLS_DIR, INSTANCES_DIR

OUT_DIR = DATA_DIR / "out"

LIMIT_EXIT_STATUSES = {"LimitsExceeded", "submitted (exit_cost)", "exit_cost"}


def _btier_entries():
    """Entries with a non-empty calls parquet AND a per_instance_details.json."""
    entries = {e.entry: e for e in select_entries()}
    out = []
    dropped_no_details = []
    for f in sorted(CALLS_DIR.glob("*.parquet")):
        name = f.stem
        if pq.read_table(f).num_rows == 0:
            continue
        em = entries.get(name)
        if em is None or not em.has_details:
            dropped_no_details.append(name)
            continue
        out.append(em)
    return out, dropped_no_details


def _load_rows(entry) -> dict:
    t = pq.read_table(INSTANCES_DIR / f"{entry.entry}.parquet")
    return {r["instance_id"]: r for r in t.to_pylist()}


def _load_calls_by_instance(entry) -> dict:
    t = pq.read_table(CALLS_DIR / f"{entry.entry}.parquet")
    by_inst: dict = defaultdict(list)
    for c in t.to_pylist():
        by_inst[c["instance_id"]].append(c)
    return by_inst


def _write_csv(path: Path, fieldnames, rows) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Mechanism (a): turn/cost-limit exhaustion
# ---------------------------------------------------------------------------


def mechanism_a(btier) -> dict:
    exit_by_outcome = defaultdict(lambda: defaultdict(int))
    cost_by_group = defaultdict(list)  # (outcome, limit_hit) -> [cost]
    n_calls_by_outcome = defaultdict(list)

    for entry in btier:
        details = load_per_instance_details(entry)
        rows = _load_rows(entry)
        for iid, d in details.items():
            r = rows.get(iid)
            if r is None:
                continue
            exit_status = r.get("exit_status")
            if exit_status is None:
                continue
            resolved = bool(d.get("resolved"))
            outcome = "resolved" if resolved else "unresolved"
            exit_by_outcome[outcome][exit_status] += 1
            cost = d.get("cost")
            if cost is not None:
                cost_by_group[(outcome, exit_status in LIMIT_EXIT_STATUSES)].append(cost)
            n_calls = r.get("n_calls")
            if n_calls is not None:
                n_calls_by_outcome[outcome].append(n_calls)

    rows_out = []
    for outcome in ("resolved", "unresolved"):
        total = sum(exit_by_outcome[outcome].values())
        for status, n in sorted(exit_by_outcome[outcome].items(), key=lambda x: -x[1]):
            rows_out.append(
                {
                    "outcome": outcome,
                    "exit_status": status,
                    "n": n,
                    "pct_of_outcome": round(100 * n / total, 3) if total else "",
                }
            )
    _write_csv(
        OUT_DIR / "m6_mechanism_a_exit_status.csv",
        ("outcome", "exit_status", "n", "pct_of_outcome"),
        rows_out,
    )

    unresolved_limit = cost_by_group[("unresolved", True)]
    unresolved_other = cost_by_group[("unresolved", False)]
    resolved_all = cost_by_group[("resolved", True)] + cost_by_group[("resolved", False)]

    n_unresolved_total = len(unresolved_limit) + len(unresolved_other)
    share_limit = len(unresolved_limit) / n_unresolved_total if n_unresolved_total else None
    mean_limit = statistics.mean(unresolved_limit) if unresolved_limit else None
    mean_other = statistics.mean(unresolved_other) if unresolved_other else None
    mean_resolved = statistics.mean(resolved_all) if resolved_all else None
    cost_per_unresolved = (
        (sum(unresolved_limit) + sum(unresolved_other)) / n_unresolved_total
        if n_unresolved_total
        else None
    )
    excess = cost_per_unresolved - mean_resolved if cost_per_unresolved is not None and mean_resolved is not None else None
    contribution = (
        share_limit * (mean_limit - mean_resolved) / excess
        if share_limit is not None and mean_limit is not None and mean_resolved is not None and excess
        else None
    )

    summary = {
        "n_unresolved_limit_exit": len(unresolved_limit),
        "n_unresolved_other_exit": len(unresolved_other),
        "share_unresolved_limit_exit": share_limit,
        "mean_cost_unresolved_limit_exit": mean_limit,
        "mean_cost_unresolved_other_exit": mean_other,
        "mean_cost_resolved": mean_resolved,
        "mean_cost_unresolved_all": cost_per_unresolved,
        "excess_cost_per_unresolved_attempt": excess,
        "mechanism_a_share_of_excess": contribution,
        "n_calls_mean_resolved": statistics.mean(n_calls_by_outcome["resolved"]),
        "n_calls_median_resolved": statistics.median(n_calls_by_outcome["resolved"]),
        "n_calls_mean_unresolved": statistics.mean(n_calls_by_outcome["unresolved"]),
        "n_calls_median_unresolved": statistics.median(n_calls_by_outcome["unresolved"]),
    }
    _write_csv(
        OUT_DIR / "m6_mechanism_a_summary.csv",
        list(summary.keys()),
        [summary],
    )
    return summary


# ---------------------------------------------------------------------------
# Mechanism (b): context growth over the trajectory
# ---------------------------------------------------------------------------


def mechanism_b(btier) -> dict:
    decile_sums = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    total_input_by_outcome = defaultdict(list)

    for entry in btier:
        details = load_per_instance_details(entry)
        by_inst = _load_calls_by_instance(entry)
        for iid, d in details.items():
            calls = by_inst.get(iid)
            if not calls:
                continue
            calls = sorted(calls, key=lambda c: c["call_idx"])
            n = len(calls)
            outcome = "resolved" if d.get("resolved") else "unresolved"
            total_input = 0
            n_priced = 0
            for c in calls:
                tok = c.get("input_tokens_raw")
                if tok is None:
                    continue
                total_input += tok
                n_priced += 1
                decile = min(9, int(9 * c["call_idx"] / max(1, n - 1))) if n > 1 else 0
                decile_sums[outcome][decile][0] += tok
                decile_sums[outcome][decile][1] += 1
            if n_priced > 0:
                total_input_by_outcome[outcome].append(total_input)

    rows_out = []
    for dec in range(10):
        rs, rn = decile_sums["resolved"][dec]
        us, un = decile_sums["unresolved"][dec]
        rows_out.append(
            {
                "decile": dec,
                "resolved_mean_input_tokens": round(rs / rn, 1) if rn else "",
                "resolved_n_calls": rn,
                "unresolved_mean_input_tokens": round(us / un, 1) if un else "",
                "unresolved_n_calls": un,
            }
        )
    _write_csv(
        OUT_DIR / "m6_mechanism_b_context_deciles.csv",
        ("decile", "resolved_mean_input_tokens", "resolved_n_calls", "unresolved_mean_input_tokens", "unresolved_n_calls"),
        rows_out,
    )

    mean_resolved = statistics.mean(total_input_by_outcome["resolved"])
    mean_unresolved = statistics.mean(total_input_by_outcome["unresolved"])
    summary = {
        "n_instances_resolved": len(total_input_by_outcome["resolved"]),
        "n_instances_unresolved": len(total_input_by_outcome["unresolved"]),
        "mean_total_input_tokens_resolved": mean_resolved,
        "median_total_input_tokens_resolved": statistics.median(total_input_by_outcome["resolved"]),
        "mean_total_input_tokens_unresolved": mean_unresolved,
        "median_total_input_tokens_unresolved": statistics.median(total_input_by_outcome["unresolved"]),
        "unresolved_over_resolved_ratio": mean_unresolved / mean_resolved,
    }
    _write_csv(OUT_DIR / "m6_mechanism_b_summary.csv", list(summary.keys()), [summary])
    return summary


# ---------------------------------------------------------------------------
# Mechanism (c): reasoning burn, subset-classified models only (no adapter
# contamination -- see reasoning_convention_by_model.csv "classification")
# ---------------------------------------------------------------------------


def _entries_by_classification(classification: str) -> set[str]:
    path = OUT_DIR / "reasoning_convention_by_model.csv"
    out = set()
    with open(path) as f:
        for row in csv.DictReader(f):
            if row["classification"] == classification:
                for e in row["entries"].split(";"):
                    out.add(e.strip())
    return out


def mechanism_c(btier) -> dict:
    subset_names = _entries_by_classification("subset")
    used = [e for e in btier if e.entry in subset_names]

    reasoning_by_outcome = defaultdict(list)
    for entry in used:
        details = load_per_instance_details(entry)
        by_inst = _load_calls_by_instance(entry)
        for iid, d in details.items():
            calls = by_inst.get(iid)
            if not calls:
                continue
            total = 0
            any_measured = False
            for c in calls:
                rt = c.get("reasoning_tokens")
                if rt is None:
                    continue
                any_measured = True
                total += rt
            if any_measured:
                outcome = "resolved" if d.get("resolved") else "unresolved"
                reasoning_by_outcome[outcome].append(total)

    mean_r = statistics.mean(reasoning_by_outcome["resolved"])
    mean_u = statistics.mean(reasoning_by_outcome["unresolved"])
    summary = {
        "n_entries_used": len(used),
        "entries_used": ";".join(e.entry for e in used),
        "n_instances_resolved": len(reasoning_by_outcome["resolved"]),
        "n_instances_unresolved": len(reasoning_by_outcome["unresolved"]),
        "mean_reasoning_tokens_resolved": mean_r,
        "median_reasoning_tokens_resolved": statistics.median(reasoning_by_outcome["resolved"]),
        "mean_reasoning_tokens_unresolved": mean_u,
        "median_reasoning_tokens_unresolved": statistics.median(reasoning_by_outcome["unresolved"]),
        "unresolved_over_resolved_ratio": mean_u / mean_r,
    }
    _write_csv(OUT_DIR / "m6_mechanism_c_summary.csv", list(summary.keys()), [summary])
    return summary


# ---------------------------------------------------------------------------
# Item 2: n_calls_unaccounted vs resolution
# ---------------------------------------------------------------------------


def calls_unaccounted_vs_resolution(btier) -> list[dict]:
    rows_out = []

    def _group_stats(label, resolved_vals, unresolved_vals):
        return {
            "group": label,
            "resolved_n": len(resolved_vals),
            "resolved_mean": statistics.mean(resolved_vals) if resolved_vals else "",
            "resolved_pct_nonzero": round(100 * sum(1 for v in resolved_vals if v) / len(resolved_vals), 3) if resolved_vals else "",
            "unresolved_n": len(unresolved_vals),
            "unresolved_mean": statistics.mean(unresolved_vals) if unresolved_vals else "",
            "unresolved_pct_nonzero": round(100 * sum(1 for v in unresolved_vals if v) / len(unresolved_vals), 3) if unresolved_vals else "",
        }

    pooled_r, pooled_u = [], []
    kimi_r, kimi_u = [], []
    for entry in btier:
        details = load_per_instance_details(entry)
        rows = _load_rows(entry)
        for iid, d in details.items():
            r = rows.get(iid)
            if r is None:
                continue
            nu = r.get("n_calls_unaccounted")
            if nu is None:
                continue
            target = pooled_r if d.get("resolved") else pooled_u
            target.append(nu)
            if entry.entry == "20260217_mini-v2.0.0_kimi-k2-5-high":
                (kimi_r if d.get("resolved") else kimi_u).append(nu)

    rows_out.append(_group_stats("pooled_btier", pooled_r, pooled_u))
    rows_out.append(_group_stats("kimi_k2_5_high_only", kimi_r, kimi_u))

    _write_csv(
        OUT_DIR / "m6_calls_unaccounted_vs_resolution.csv",
        ("group", "resolved_n", "resolved_mean", "resolved_pct_nonzero", "unresolved_n", "unresolved_mean", "unresolved_pct_nonzero"),
        rows_out,
    )
    return rows_out


# ---------------------------------------------------------------------------
# Item 3: does the reasoning undercount (Claim 2 evidence (b)) concentrate
# in resolved or unresolved instances?
# ---------------------------------------------------------------------------


def reasoning_undercount_concentration(btier) -> list[dict]:
    additive_names = _entries_by_classification("additive")
    used = [e for e in btier if e.entry in additive_names]

    rows_out = []
    for entry in used:
        details = load_per_instance_details(entry)
        by_inst = _load_calls_by_instance(entry)
        fr, fu = [], []
        for iid, d in details.items():
            calls = by_inst.get(iid)
            if not calls:
                continue
            n_viol = sum(1 for c in calls if c.get("reasoning_exceeds_output"))
            frac = n_viol / len(calls)
            (fr if d.get("resolved") else fu).append(frac)
        rows_out.append(
            {
                "entry": entry.entry,
                "resolved_n": len(fr),
                "resolved_mean_violation_frac": round(statistics.mean(fr), 5) if fr else "",
                "unresolved_n": len(fu),
                "unresolved_mean_violation_frac": round(statistics.mean(fu), 5) if fu else "",
            }
        )

    _write_csv(
        OUT_DIR / "m6_reasoning_undercount_by_entry.csv",
        ("entry", "resolved_n", "resolved_mean_violation_frac", "unresolved_n", "unresolved_mean_violation_frac"),
        rows_out,
    )
    return rows_out


def run() -> dict:
    btier, dropped = _btier_entries()
    print(f"B-tier entries used: {len(btier)}  (dropped, no per_instance_details: {dropped})")
    a = mechanism_a(btier)
    b = mechanism_b(btier)
    c = mechanism_c(btier)
    unacc = calls_unaccounted_vs_resolution(btier)
    undercount = reasoning_undercount_concentration(btier)
    return {"mechanism_a": a, "mechanism_b": b, "mechanism_c": c, "calls_unaccounted": unacc, "reasoning_undercount": undercount}


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(run(), indent=2, default=str))
