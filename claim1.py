"""Claim 1 (economic): what share of mini-SWE-agent spend on SWE-bench Verified
went to instances the submission did not resolve.

Uses only per_instance_details.json {cost, api_calls, resolved} plus
metadata.yaml. No S3, no traj parsing, no price table, no network -- see
plan.md "Thesis". Ships independently of Claim 2 (analysis/token_waste/
metrics.py, milestone 6).

Bucket resolution is intentionally coarser here than the full three-bucket
model in plan.md ("Missing-traj buckets and bounds"): distinguishing
no_gen_no_traj from missing_traj_has_patch requires inspecting logs/trajs on
S3, which this module does not touch. See ASSUMPTIONS below.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_DIR, IMPUTE_QUANTILE, VERIFIED_N
from .entries import EntryMeta, load_per_instance_details, select_entries
from .measured import sum_where_present

OUT_DIR = DATA_DIR / "out"

GROUP_KEYS = ("entry", "model", "mini_version")


# ---------------------------------------------------------------------------
# Canonical instance-id list
# ---------------------------------------------------------------------------


def canonical_verified_ids(cache: bool = True) -> frozenset[str]:
    """The 500 SWE-bench Verified instance ids.

    plan.md's primary source is the HF dataset, cached to
    data/verified_instance_ids.json; the fallback is "union of ids across
    all verified results.json lists". Claim 1 is no-network by design, so it
    always uses the fallback -- every id that appears in ANY bucket
    (resolved, generated, no_generation, ...) of ANY evaluation/verified/*/
    results/results.json in this repo, mini or not.
    """
    from .config import EVALUATION

    cache_path = DATA_DIR / "verified_instance_ids.json"
    if cache and cache_path.is_file():
        return frozenset(json.loads(cache_path.read_text()))

    ids: set[str] = set()
    verified_dir = EVALUATION / "verified"
    for results_path in verified_dir.glob("*/results/results.json"):
        try:
            data = json.loads(results_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for value in data.values():
            if isinstance(value, list):
                ids.update(str(v) for v in value)

    if cache and ids:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(sorted(ids)))

    return frozenset(ids)


# ---------------------------------------------------------------------------
# Per-entry stats
# ---------------------------------------------------------------------------


@dataclass
class EntryClaim1Stats:
    entry: str
    model: str | None
    mini_version: str | None
    n_observed: int
    n_no_gen_no_traj: int
    n_cost_missing: int  # observed rows with cost=null; excluded from $ aggregates, not zeroed
    n_resolved_observed: int  # ALL resolved rows, cost known or not -- do not use as a $/resolved denominator
    n_resolved_priced: int  # resolved rows with a known cost -- correct denominator for cost_per_resolved
    n_priced: int  # observed rows with a known cost (== n_observed - n_cost_missing)
    total_cost_observed: float
    unresolved_cost_observed: float
    total_api_calls_observed: int  # sum over rows with a measured api_calls only
    n_calls_measured: int  # rows with a non-null api_calls -- denominator for total_api_calls_observed
    n_resolved_calls_measured: int  # resolved rows with a non-null api_calls -- correct calls_per_resolved denominator
    p50_cost_observed: float | None
    p75_cost_observed: float | None
    imputed_cost_p75: float  # n_no_gen_no_traj * p75_cost_observed
    imputed_cost_p50: float  # n_no_gen_no_traj * p50_cost_observed
    observed_costs: list[float] = field(repr=False)  # for pooled dispersion

    @property
    def waste_lower(self) -> float | None:
        if self.total_cost_observed <= 0:
            return None
        return self.unresolved_cost_observed / self.total_cost_observed

    @property
    def waste_upper(self) -> float | None:
        denom = self.total_cost_observed + self.imputed_cost_p75
        if denom <= 0:
            return None
        return (self.unresolved_cost_observed + self.imputed_cost_p75) / denom

    @property
    def waste_upper_p50(self) -> float | None:
        denom = self.total_cost_observed + self.imputed_cost_p50
        if denom <= 0:
            return None
        return (self.unresolved_cost_observed + self.imputed_cost_p50) / denom

    @property
    def total_spend_per_resolved_instance(self) -> float | None:
        """plan.md's originally-specified cost_per_resolved_instance: TOTAL
        priced spend (resolved + unresolved attempts) / resolved count.
        Reads as "what did it cost, overall, including failures, to end up
        with N resolved instances" -- a throughput/buying-power number, not
        a per-instance cost. Denominator restricted to priced rows: a
        fully-cost-null entry must not add resolved rows here with zero $
        in the numerator, or it silently drags every pooled number down.
        """
        if self.n_resolved_priced <= 0:
            return None
        return self.total_cost_observed / self.n_resolved_priced

    @property
    def cost_per_resolved(self) -> float | None:
        """Mean $ spent on a resolved instance alone (priced rows only).
        Paired with cost_per_unresolved below -- both are "mean cost of an
        instance in this outcome bucket", so the ratio between them is a
        real per-instance comparison, unlike total_spend_per_resolved_instance
        (whose numerator always includes ALL spend, not just resolved-instance
        spend, so it isn't comparable to a per-unresolved-instance number)."""
        resolved_cost_priced = self.total_cost_observed - self.unresolved_cost_observed
        if self.n_resolved_priced <= 0:
            return None
        return resolved_cost_priced / self.n_resolved_priced

    @property
    def cost_per_unresolved(self) -> float | None:
        """Mean $ spent on an unresolved instance alone (priced rows only)."""
        n_unresolved_priced = self.n_priced - self.n_resolved_priced
        if n_unresolved_priced <= 0:
            return None
        return self.unresolved_cost_observed / n_unresolved_priced

    @property
    def calls_per_resolved(self) -> float | None:
        # Same discipline as cost_per_resolved: denominator restricted to
        # resolved rows with a MEASURED api_calls, not all resolved rows.
        if self.n_resolved_calls_measured <= 0:
            return None
        return self.total_api_calls_observed / self.n_resolved_calls_measured


def compute_entry_stats(
    details: dict[str, dict],
    canonical_ids: frozenset[str],
    entry: str = "",
    model: str | None = None,
    mini_version: str | None = None,
    impute_q: float = IMPUTE_QUANTILE,
) -> EntryClaim1Stats:
    """Core, disk-free computation -- takes a raw per_instance_details dict.

    Bucket rule (Claim 1 only, see module docstring): observed = present in
    `details`; no_gen_no_traj = canonical id absent from `details`.
    missing_traj_has_patch is not distinguishable without S3 and is always 0
    here (see ASSUMPTIONS).
    """
    observed_ids = set(details.keys())
    n_no_gen_no_traj = len(canonical_ids - observed_ids) if canonical_ids else 0

    rows = list(details.values())

    # A null cost is "unknown", not "free" (plan.md edge case: "details
    # without cost -> nulls, rows kept"). sum_where_present excludes it from
    # BOTH the $ total and the count -- never coerced to 0, never counted as
    # if it were measured. Same discipline for api_calls below (see
    # measured.py docstring: this is a class of bug, not a one-off).
    cost_sum = sum_where_present(rows, lambda r: r.get("cost"))
    calls_sum = sum_where_present(rows, lambda r: r.get("api_calls"))

    resolved_rows = [r for r in rows if r.get("resolved")]
    unresolved_rows = [r for r in rows if not r.get("resolved")]
    resolved_cost_sum = sum_where_present(resolved_rows, lambda r: r.get("cost"))
    unresolved_cost_sum = sum_where_present(unresolved_rows, lambda r: r.get("cost"))
    resolved_calls_sum = sum_where_present(resolved_rows, lambda r: r.get("api_calls"))

    n_resolved = len(resolved_rows)  # ALL resolved rows, cost known or not
    n_resolved_priced = resolved_cost_sum.n_measured
    n_cost_missing = cost_sum.n_absent
    total_cost = cost_sum.total
    unresolved_cost = unresolved_cost_sum.total
    total_calls = calls_sum.total
    costs = [r["cost"] for r in rows if r.get("cost") is not None]

    p50 = statistics.median(costs) if costs else None
    p75 = (
        statistics.quantiles(costs, n=4)[2]
        if len(costs) >= 2
        else (costs[0] if costs else None)
    )
    # statistics.quantiles needs >=2 points for interpolation; for a single
    # point p75 == that point, same as p50.

    imputed_p75 = n_no_gen_no_traj * (p75 or 0.0)
    imputed_p50 = n_no_gen_no_traj * (p50 or 0.0)

    return EntryClaim1Stats(
        entry=entry,
        model=model,
        mini_version=mini_version,
        n_observed=len(observed_ids),
        n_no_gen_no_traj=n_no_gen_no_traj,
        n_cost_missing=n_cost_missing,
        n_resolved_observed=n_resolved,
        n_resolved_priced=n_resolved_priced,
        n_priced=cost_sum.n_measured,
        total_cost_observed=total_cost,
        unresolved_cost_observed=unresolved_cost,
        total_api_calls_observed=total_calls,
        n_calls_measured=calls_sum.n_measured,
        n_resolved_calls_measured=resolved_calls_sum.n_measured,
        p50_cost_observed=p50,
        p75_cost_observed=p75,
        imputed_cost_p75=imputed_p75,
        imputed_cost_p50=imputed_p50,
        observed_costs=costs,
    )


def load_all_entry_stats(
    entries: list[EntryMeta] | None = None,
) -> list[EntryClaim1Stats]:
    entries = entries if entries is not None else select_entries()
    canonical_ids = canonical_verified_ids()
    out = []
    for e in entries:
        details = load_per_instance_details(e)
        if details is None:
            continue
        out.append(
            compute_entry_stats(
                details,
                canonical_ids,
                entry=e.entry,
                model=e.requested_model,
                mini_version=e.mini_version,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Grouping / aggregation
# ---------------------------------------------------------------------------


def _group_key(stat: EntryClaim1Stats, group: str) -> str:
    if group == "entry":
        return stat.entry
    if group == "model":
        return stat.model or "(unknown)"
    if group == "mini_version":
        return stat.mini_version or "(unknown)"
    raise ValueError(f"unknown group {group!r}")


def _aggregate(stats: list[EntryClaim1Stats]) -> dict:
    """Sum raw numerators/denominators across entries, then derive ratios
    once at the group level (never average per-entry percentages)."""
    n_observed = sum(s.n_observed for s in stats)
    n_no_gen_no_traj = sum(s.n_no_gen_no_traj for s in stats)
    n_cost_missing = sum(s.n_cost_missing for s in stats)
    n_resolved = sum(s.n_resolved_observed for s in stats)
    n_resolved_priced = sum(s.n_resolved_priced for s in stats)
    n_priced = sum(s.n_priced for s in stats)
    total_cost = sum(s.total_cost_observed for s in stats)
    unresolved_cost = sum(s.unresolved_cost_observed for s in stats)
    imputed_p75 = sum(s.imputed_cost_p75 for s in stats)
    imputed_p50 = sum(s.imputed_cost_p50 for s in stats)
    total_calls = sum(s.total_api_calls_observed for s in stats)
    n_resolved_calls_measured = sum(s.n_resolved_calls_measured for s in stats)

    waste_lower = unresolved_cost / total_cost if total_cost > 0 else None
    denom_75 = total_cost + imputed_p75
    waste_upper = (unresolved_cost + imputed_p75) / denom_75 if denom_75 > 0 else None
    denom_50 = total_cost + imputed_p50
    waste_upper_p50 = (unresolved_cost + imputed_p50) / denom_50 if denom_50 > 0 else None
    # Denominators restricted to PRICED rows (see EntryClaim1Stats.cost_per_resolved):
    # a fully-cost-null entry must not add resolved/unresolved count without
    # adding matching $, or it silently drags the pooled ratio down.
    n_unresolved_priced = n_priced - n_resolved_priced
    resolved_cost_priced = total_cost - unresolved_cost
    # Throughput reading (plan.md step-6 literal formula): total spend
    # (incl. failures) per resolved instance bought.
    total_spend_per_resolved = total_cost / n_resolved_priced if n_resolved_priced > 0 else None
    # Per-instance reading: mean $ of a resolved attempt vs a unresolved
    # one. These two ARE directly comparable (same-shape numerator/denominator
    # on each side), unlike total_spend_per_resolved vs cost_per_unresolved.
    cost_per_resolved = resolved_cost_priced / n_resolved_priced if n_resolved_priced > 0 else None
    cost_per_unresolved = (
        unresolved_cost / n_unresolved_priced if n_unresolved_priced > 0 else None
    )
    unresolved_resolved_cost_ratio = (
        cost_per_unresolved / cost_per_resolved
        if cost_per_resolved and cost_per_unresolved is not None
        else None
    )
    calls_per_resolved = (
        total_calls / n_resolved_calls_measured if n_resolved_calls_measured > 0 else None
    )

    return dict(
        n_observed=n_observed,
        n_no_gen_no_traj=n_no_gen_no_traj,
        n_cost_missing=n_cost_missing,
        n_missing_traj_has_patch=0,  # not distinguishable without S3, see ASSUMPTIONS
        n_observed_no_patch=None,  # not computable from per_instance_details, see ASSUMPTIONS
        n_resolved_observed=n_resolved,
        n_resolved_priced=n_resolved_priced,
        n_unresolved_priced=n_unresolved_priced,
        total_cost_observed=total_cost,
        unresolved_cost_observed=unresolved_cost,
        waste_lower=waste_lower,
        waste_upper=waste_upper,
        waste_upper_p50=waste_upper_p50,
        total_spend_per_resolved_instance=total_spend_per_resolved,
        cost_per_resolved_instance=cost_per_resolved,
        cost_per_unresolved_instance=cost_per_unresolved,
        unresolved_resolved_cost_ratio=unresolved_resolved_cost_ratio,
        n_resolved_calls_measured=n_resolved_calls_measured,
        calls_per_resolved_instance=calls_per_resolved,
    )


def _dispersion(stats: list[EntryClaim1Stats]) -> dict:
    pooled = sorted(c for s in stats for c in s.observed_costs)
    n = len(pooled)
    if n == 0:
        return dict(p50=None, p90=None, max=None, mean=None, top_decile_share=None, n=0)
    total = sum(pooled)
    p50 = statistics.median(pooled)
    p90 = statistics.quantiles(pooled, n=10)[8] if n >= 10 else pooled[-1]
    top_decile_n = max(1, round(n * 0.10))
    top_decile_share = sum(pooled[-top_decile_n:]) / total if total > 0 else None
    return dict(
        p50=p50,
        p90=p90,
        max=pooled[-1],
        mean=total / n,
        top_decile_share=top_decile_share,
        n=n,
    )


def waste_by(stats: list[EntryClaim1Stats], group: str) -> list[dict]:
    groups: dict[str, list[EntryClaim1Stats]] = {}
    for s in stats:
        groups.setdefault(_group_key(s, group), []).append(s)
    rows = []
    for key, members in sorted(groups.items()):
        row = {group: key}
        row.update(_aggregate(members))
        rows.append(row)
    return rows


def dispersion_by(stats: list[EntryClaim1Stats], group: str) -> list[dict]:
    groups: dict[str, list[EntryClaim1Stats]] = {}
    for s in stats:
        groups.setdefault(_group_key(s, group), []).append(s)
    rows = []
    for key, members in sorted(groups.items()):
        row = {group: key}
        row.update(_dispersion(members))
        rows.append(row)
    return rows


def cost_per_resolved_table(stats: list[EntryClaim1Stats]) -> list[dict]:
    rows = []
    for s in sorted(stats, key=lambda s: s.entry):
        cpr = s.cost_per_resolved
        cpu = s.cost_per_unresolved
        rows.append(
            dict(
                entry=s.entry,
                model=s.model,
                mini_version=s.mini_version,
                total_cost_observed=s.total_cost_observed,
                n_resolved_observed=s.n_resolved_observed,
                n_resolved_priced=s.n_resolved_priced,
                n_unresolved_priced=s.n_priced - s.n_resolved_priced,
                total_spend_per_resolved_instance=s.total_spend_per_resolved_instance,
                cost_per_resolved_instance=cpr,
                cost_per_unresolved_instance=cpu,
                unresolved_resolved_cost_ratio=(cpu / cpr if cpr and cpu is not None else None),
                n_resolved_calls_measured=s.n_resolved_calls_measured,
                calls_per_resolved_instance=s.calls_per_resolved,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Global never-resolved (demoted, per plan.md "Unresolved" / "Demote")
# ---------------------------------------------------------------------------


def global_unresolved_table(entries: list[EntryMeta] | None = None) -> list[dict]:
    """Instance ids never resolved=True by ANY mini entry with details, nor
    present in any results.json[resolved] list in the repo. Spend on these
    is expected loss, not waste (plan.md "Demote") -- reported once here,
    never used to compute the headline per-submission waste numbers above.
    """
    from .config import EVALUATION

    entries = entries if entries is not None else select_entries()
    canonical_ids = canonical_verified_ids()

    ever_resolved: set[str] = set()
    for results_path in (EVALUATION / "verified").glob("*/results/results.json"):
        try:
            data = json.loads(results_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        resolved = data.get("resolved")
        if isinstance(resolved, list):
            ever_resolved.update(str(v) for v in resolved)

    per_instance_spend: dict[str, float] = {}
    per_instance_n_priced: dict[str, int] = {}
    for e in entries:
        details = load_per_instance_details(e)
        if details is None:
            continue
        for iid, row in details.items():
            if row.get("resolved"):
                ever_resolved.add(iid)
            raw_cost = row.get("cost")
            # Same discipline as compute_entry_stats: a null cost is
            # excluded from the sum entirely, never coerced to 0 -- a
            # never-resolved instance with only null-cost attempts must
            # show up as "no spend data", not as "$0 spent".
            if raw_cost is not None:
                per_instance_spend[iid] = per_instance_spend.get(iid, 0.0) + float(raw_cost)
                per_instance_n_priced[iid] = per_instance_n_priced.get(iid, 0) + 1

    never_resolved = canonical_ids - ever_resolved
    rows = [
        dict(
            instance_id=iid,
            total_observed_spend_usd=per_instance_spend.get(iid),
            n_priced_attempts=per_instance_n_priced.get(iid, 0),
        )
        for iid in sorted(never_resolved)
    ]
    return rows


# ---------------------------------------------------------------------------
# Summary + CSV writing
# ---------------------------------------------------------------------------


def summary_row(stats: list[EntryClaim1Stats], global_rows: list[dict]) -> dict:
    agg = _aggregate(stats)
    agg["n_entries"] = len(stats)
    agg["n_global_never_resolved"] = len(global_rows)
    agg["global_never_resolved_spend_usd"] = sum(
        r["total_observed_spend_usd"] for r in global_rows if r["total_observed_spend_usd"] is not None
    )
    return agg


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run(out_dir: Path = OUT_DIR) -> dict[str, Path]:
    entries = select_entries()
    stats = load_all_entry_stats(entries)

    written = {}
    for group in GROUP_KEYS:
        p = out_dir / f"claim1_waste_by_{group}.csv"
        _write_csv(waste_by(stats, group), p)
        written[p.name] = p

        p = out_dir / f"claim1_dispersion_by_{group}.csv"
        _write_csv(dispersion_by(stats, group), p)
        written[p.name] = p

    p = out_dir / "claim1_cost_per_resolved.csv"
    _write_csv(cost_per_resolved_table(stats), p)
    written[p.name] = p

    global_rows = global_unresolved_table(entries)
    p = out_dir / "claim1_global_unresolved.csv"
    _write_csv(global_rows, p)
    written[p.name] = p

    p = out_dir / "claim1_summary.csv"
    _write_csv([summary_row(stats, global_rows)], p)
    written[p.name] = p

    return written
