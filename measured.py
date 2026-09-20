"""Shared discipline for every ratio metric in this pipeline, Claim 1 and
Claim 2 alike: a value that wasn't measured is null, never 0, and a null
value is excluded from BOTH the numerator and the denominator of any ratio
built from it -- never folded silently into either side.

Why this exists: milestone-2's cost_per_resolved_instance divided total
priced spend by a resolved-row COUNT that included rows with no cost at
all. Those rows added to the denominator with zero in the numerator,
quietly dragging every pooled ratio down. That's a class of bug, not a
one-off -- it will recur at token level in milestone 6, where absent
reasoning/cache fields are far more common than absent per-instance cost.
Every aggregation from here on goes through this module instead of
hand-rolled `sum(... or 0 ...)` loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class MeasuredSum:
    """Sum of a field across rows where it's present, plus how many rows
    that actually covers. `n_measured` IS the denominator to report
    alongside `total` -- never the row count of the whole collection."""

    total: float
    n_measured: int
    n_absent: int

    @property
    def mean(self) -> float | None:
        return self.total / self.n_measured if self.n_measured > 0 else None


def sum_where_present(rows: Iterable[T], getter: Callable[[T], float | int | None]) -> MeasuredSum:
    """Sum getter(row) over rows where it is not None. Rows where it IS
    None are excluded from both the sum and the count -- never coerced to
    0 and never counted as if they were measured."""
    total = 0.0
    n_measured = 0
    n_absent = 0
    for row in rows:
        v = getter(row)
        if v is None:
            n_absent += 1
            continue
        total += v
        n_measured += 1
    return MeasuredSum(total=total, n_measured=n_measured, n_absent=n_absent)


def ratio(numerator: MeasuredSum, denominator_count: int) -> float | None:
    """A ratio whose denominator must itself only count measured rows.
    Pass numerator.n_measured (or an explicitly filtered count), never a
    raw len(rows) that might include rows the numerator had to skip."""
    if denominator_count <= 0:
        return None
    return numerator.total / denominator_count


