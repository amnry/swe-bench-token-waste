"""Milestone 2 tests: claim1.py against synthetic per_instance_details data.

No repo files, no network -- pure unit tests on compute_entry_stats /
_aggregate. Fixture: 5 observed instances (3 resolved, 2 unresolved), 2
canonical ids never observed (no_gen_no_traj), chosen so p50 != p75 and the
imputed upper bound is checkable by hand.
"""

import math

from analysis.token_waste.claim1 import (
    _aggregate,
    _dispersion,
    compute_entry_stats,
)

# costs sorted: [1, 2, 3, 4, 10]; unresolved are the two middle-cost ones
# (3, 4) so waste isn't trivially "all cost is unresolved" or "none is".
DETAILS = {
    "solved-1": {"cost": 1, "api_calls": 10, "resolved": True},
    "solved-2": {"cost": 2, "api_calls": 20, "resolved": True},
    "unsolved-1": {"cost": 3, "api_calls": 30, "resolved": False},
    "unsolved-2": {"cost": 4, "api_calls": 40, "resolved": False},
    "solved-3": {"cost": 10, "api_calls": 50, "resolved": True},
}
# 2 extra canonical ids this entry never generated anything for.
CANONICAL_IDS = frozenset(set(DETAILS) | {"missing-1", "missing-2"})


def _isclose(a, b):
    return math.isclose(a, b, rel_tol=1e-9)


def test_bucket_counts():
    stats = compute_entry_stats(DETAILS, CANONICAL_IDS, entry="e1")
    assert stats.n_observed == 5
    assert stats.n_no_gen_no_traj == 2
    assert stats.n_resolved_observed == 3


def test_p50_and_p75_differ():
    stats = compute_entry_stats(DETAILS, CANONICAL_IDS, entry="e1")
    assert _isclose(stats.p50_cost_observed, 3.0)
    assert _isclose(stats.p75_cost_observed, 7.0)
    assert stats.p50_cost_observed != stats.p75_cost_observed


def test_waste_lower_exact():
    stats = compute_entry_stats(DETAILS, CANONICAL_IDS, entry="e1")
    # unresolved cost 3+4=7, total cost 1+2+3+4+10=20
    assert _isclose(stats.waste_lower, 7 / 20)


def test_waste_upper_uses_p75_not_median():
    stats = compute_entry_stats(DETAILS, CANONICAL_IDS, entry="e1")
    # imputed = n_no_gen_no_traj(2) * p75(7.0) = 14; always counted unresolved
    assert _isclose(stats.waste_upper, (7 + 14) / (20 + 14))
    # p50 variant uses median(3.0): imputed = 2*3=6
    assert _isclose(stats.waste_upper_p50, (7 + 6) / (20 + 6))
    # the two upper bounds must differ given p50 != p75 -- this is the whole
    # point of the milestone-3 plan revision (median -> p75 for the upper
    # bound, median kept as a secondary column).
    assert stats.waste_upper != stats.waste_upper_p50


def test_cost_per_resolved_exact():
    stats = compute_entry_stats(DETAILS, CANONICAL_IDS, entry="e1")
    # total_spend_per_resolved_instance: plan.md's original "total_spend /
    # n_resolved" throughput reading -- includes the cost of the two failed
    # attempts (3, 4) in the numerator.
    assert _isclose(stats.total_spend_per_resolved_instance, 20 / 3)
    # cost_per_resolved / cost_per_unresolved: mean $ of an instance IN that
    # outcome bucket, so the two are directly comparable to each other.
    # resolved rows cost 1+2+10=13 over 3 instances; unresolved cost 3+4=7
    # over 2 instances.
    assert _isclose(stats.cost_per_resolved, 13 / 3)
    assert _isclose(stats.cost_per_unresolved, 7 / 2)
    assert stats.total_spend_per_resolved_instance != stats.cost_per_resolved
    assert _isclose(stats.calls_per_resolved, 150 / 3)


def test_no_canonical_ids_means_no_imputation():
    # If we don't know the canonical id universe, no_gen_no_traj is 0 and
    # upper bound collapses to the lower bound -- imputation must never
    # invent unresolved cost out of nothing.
    stats = compute_entry_stats(DETAILS, frozenset(), entry="e1")
    assert stats.n_no_gen_no_traj == 0
    assert _isclose(stats.waste_lower, stats.waste_upper)
    assert _isclose(stats.waste_lower, stats.waste_upper_p50)


def test_aggregate_sums_raw_numbers_not_percentages():
    # Two entries with wildly different waste shares: aggregation must sum
    # cost/unresolved-cost and divide once, not average the two waste%s
    # (which would silently misweight a cheap entry against an expensive one).
    cheap = compute_entry_stats(
        {"a": {"cost": 1, "api_calls": 1, "resolved": False}},
        frozenset({"a"}),
        entry="cheap",
    )
    expensive = compute_entry_stats(
        {"b": {"cost": 99, "api_calls": 1, "resolved": True}},
        frozenset({"b"}),
        entry="expensive",
    )
    agg = _aggregate([cheap, expensive])
    # naive average of waste% would be (1.0 + 0.0)/2 = 0.5; correct pooled
    # answer is 1/100 = 0.01.
    assert _isclose(agg["waste_lower"], 1 / 100)


def test_dispersion_top_decile_share():
    stats = compute_entry_stats(DETAILS, CANONICAL_IDS, entry="e1")
    disp = _dispersion([stats])
    assert disp["n"] == 5
    assert _isclose(disp["max"], 10)
    # top 10% of 5 items rounds to 1 item -> the single costliest (10) / total (20)
    assert _isclose(disp["top_decile_share"], 10 / 20)


def test_zero_cost_entry_does_not_crash():
    stats = compute_entry_stats(
        {"z": {"cost": 0, "api_calls": 0, "resolved": False}},
        frozenset({"z"}),
        entry="zero",
    )
    assert stats.waste_lower is None  # 0/0 undefined, not a fake 0.0 or 1.0
    assert stats.cost_per_resolved is None
