"""Field-coverage report: for each tier, what fraction of call rows have
each token field measured vs structurally_absent/known_zero/unknown. Needed
BEFORE milestone 5 -- if reasoning_tokens or the cache fields are mostly
"unknown" (not just absent) in real trajs, Claim 2 is much weaker than
plan.md assumes, and that has to surface now, not at milestone 6.

Only "unknown" is a real data gap. structurally_absent and known_zero are
real, priceable zeros (see parse.py module docstring) -- reporting them
alongside "unknown" without distinguishing would make the pipeline look
weaker than it is, or (worse) hide a genuine gap inside an apparent 100%.

Milestone 3 only has two labeled fixtures on disk (one B_chat, one
B_responses); this reports on those, and extract.py (milestone 4) reuses
`coverage_for_calls` across every entry pulled from S3.

    python -m analysis.token_waste.field_coverage
"""

from __future__ import annotations

import json
from pathlib import Path

from .parse import TOKEN_FIELDS, parse_traj

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"

PROVENANCE_TAGS = ("measured", "structurally_absent", "known_zero", "unknown", "derived")


def coverage_for_calls(calls: list) -> dict[str, dict]:
    n = len(calls)
    out = {}
    for f in TOKEN_FIELDS:
        counts = {tag: 0 for tag in PROVENANCE_TAGS}
        for c in calls:
            counts[c.provenance.get(f, "unknown")] += 1
        out[f] = {
            "n_calls": n,
            **{f"n_{tag}": counts[tag] for tag in PROVENANCE_TAGS},
            "pct_measured": (counts["measured"] / n * 100) if n else None,
            "pct_unknown": (counts["unknown"] / n * 100) if n else None,
        }
    return out


def print_coverage(cov: dict[str, dict]) -> None:
    for field_name, stats in cov.items():
        n = stats["n_calls"]
        print(
            f"    {field_name:<22} measured {stats['n_measured']:>5}/{n:<5} "
            f"structurally_absent {stats['n_structurally_absent']:>5}  "
            f"known_zero {stats['n_known_zero']:>5}  "
            f"unknown {stats['n_unknown']:>5}"
        )


def run() -> None:
    fixtures = {
        "claude-4-6-opus.json (B_chat)": "claude-4-6-opus.json",
        "gpt-5-2-high-response.json (B_responses)": "gpt-5-2-high-response.json",
    }
    for label, filename in fixtures.items():
        traj = json.loads((TEST_DATA / filename).read_text())
        instance_row, calls, quarantine = parse_traj(traj)
        print(f"=== {label} -- tier={instance_row.tier}  n_calls={len(calls)}  quarantined={len(quarantine)} ===")
        print_coverage(coverage_for_calls(calls))
        print()


if __name__ == "__main__":
    run()
