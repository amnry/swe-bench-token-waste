"""Shared constants for the token-waste pipeline.

Kept as a single flat module (no environment-driven overrides) so every
milestone imports the same numbers without needing a config file on disk.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVALUATION = REPO_ROOT / "evaluation"
TOKEN_WASTE_DIR = Path(__file__).resolve().parent
DATA_DIR = TOKEN_WASTE_DIR / "data"

BUCKET_URL = "https://swe-bench-submissions.s3.amazonaws.com"

# A call with more than this many raw input tokens is flagged "long context".
LONG_CTX_THRESHOLD = 200_000

# If long-context calls carry more than this share of total $ spend, base-tier
# pricing is no longer good enough and tiered long-context prices must be
# implemented before metrics are published (see plan.md "Long ctx").
LONG_CTX_GATE = 0.05

# Parser smoke test only (see plan.md "TOL=0.05" decision): how far
# recomputed-vs-computed cost is allowed to drift before we suspect a parsing
# bug. This is NOT a claim that the two numbers should agree -- see Claim 2's
# de-confounded deltas (d_conv, d_price) for that.
SMOKE_TOL = 0.05

# Quantile used to impute per-instance cost/tokens for the no_gen_no_traj
# upper bound. p75 was chosen over the median because these instances are
# expected to be bimodal (hit the turn/step limit -> expensive, or crashed
# early -> cheap), not centrally distributed. p50 is also reported as a
# secondary column.
IMPUTE_QUANTILE = 0.75

# Canonical size of SWE-bench Verified.
VERIFIED_N = 500
