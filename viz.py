"""M6 visualizations. Reads the m6_*.csv tables written by metrics.run() and
renders three figures to data/out/figures/. No network.

    python -m token_waste.viz
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .config import DATA_DIR

OUT_DIR = DATA_DIR / "out"
FIG_DIR = OUT_DIR / "figures"


def _short(entry: str) -> str:
    return entry.split("_", 1)[1] if "_" in entry else entry


def fig_cost_asymmetry(path: Path) -> None:
    """Q1: per-entry unresolved/resolved cost ratio, split into the two channels.

    The decomposition is multiplicative (cost = calls x cost-per-call), so it is
    stacked in log-space where the two segments add up to the total exactly."""
    import numpy as np

    d = pd.read_csv(OUT_DIR / "m6_cost_asymmetry_by_entry.csv").sort_values("cost_ratio")
    y = range(len(d))
    l_calls = np.log(d.calls_ratio)
    l_cpc = np.log(d.cost_per_call_ratio)
    fig, ax = plt.subplots(figsize=(9, 11))
    ax.barh(y, l_calls, color="#2b6cb0", label="more turns (calls ratio)")
    ax.barh(y, l_cpc, left=l_calls, color="#ed8936", label="pricier per turn (cost/call ratio)")
    ax.axvline(0.0, color="gray", ls="--", lw=1)
    ax.axvline(np.log(1.51), color="red", ls=":", lw=1.4, label="pooled 1.51x")
    ticks = [1, 1.5, 2, 3, 5, 9]
    ax.set_xticks([np.log(t) for t in ticks])
    ax.set_xticklabels([f"{t}x" for t in ticks])
    ax.set_yticks(list(y))
    ax.set_yticklabels([_short(e)[:34] for e in d.entry], fontsize=7)
    ax.set_xlabel("unresolved / resolved cost  (log scale; segments add to total)")
    ax.set_title("Q1  Failed attempts cost more: it's mostly more turns\n"
                 "cost ratio = (turns ratio) x (cost-per-turn ratio), every entry > 1")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_unaccounted(path: Path) -> None:
    """Q2: resolution rate with vs without unaccounted retries (tier B entries)."""
    d = pd.read_csv(OUT_DIR / "m6_unaccounted_by_entry.csv")
    d = d[d.total_unaccounted > 0].sort_values("total_unaccounted", ascending=False)
    y = range(len(d))
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(d.resrate_without, y, color="#2b6cb0", s=40, label="no retries")
    ax.scatter(d.resrate_with_retries, y, color="#e53e3e", s=40, label="with >=1 retry")
    for i, r in zip(y, d.itertuples()):
        ax.plot([r.resrate_without, r.resrate_with_retries], [i, i], color="gray", lw=0.8, zorder=0)
    ax.set_yticks(list(y))
    ax.set_yticklabels([f"{_short(r.entry)[:28]}  (n={r.total_unaccounted})" for r in d.itertuples()],
                       fontsize=8)
    ax.set_xlabel("resolution rate")
    ax.set_title("Q2  Unaccounted 'retry' calls do not predict outcome\n"
                 "with-retry vs without-retry resolution rate barely moves (pooled corr = -0.008)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fig_reasoning(path: Path) -> None:
    """Q3: unresolved share of reasoning tokens vs unresolved share of cost."""
    d = pd.read_csv(OUT_DIR / "m6_reasoning_concentration_by_entry.csv")
    fig, ax = plt.subplots(figsize=(7.5, 7))
    sizes = 40 + 400 * (d.reasoning_tokens_M / d.reasoning_tokens_M.max())
    ax.scatter(d.unresolved_share_cost, d.unresolved_share_reasoning, s=sizes,
               alpha=0.6, color="#805ad5", edgecolor="black", linewidth=0.5)
    lo, hi = 0.15, 1.02
    ax.plot([lo, hi], [lo, hi], color="gray", ls="--", lw=1, label="reasoning tracks cost (no skew)")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("unresolved share of COST")
    ax.set_ylabel("unresolved share of REASONING tokens")
    ax.set_title("Q3  Reasoning spend barely skews to failures (+3.8pp)\n"
                 "and is 0.15% of $ -> waste figure moves <0.1pp. Bubble = reasoning tokens.")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run(fig_dir: Path = FIG_DIR) -> list[Path]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        (fig_dir / "q1_cost_asymmetry.png", fig_cost_asymmetry),
        (fig_dir / "q2_unaccounted.png", fig_unaccounted),
        (fig_dir / "q3_reasoning.png", fig_reasoning),
    ]
    for path, fn in outputs:
        fn(path)
    return [p for p, _ in outputs]


if __name__ == "__main__":
    for p in run():
        print(f"wrote {p}")
