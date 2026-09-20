"""Milestone 5: the dual-run pricing engine.

  R'_conv = OUR convention logic x litellm's own price table, pinned to the
            commit current at each entry's submission_date
  R'_full = OUR convention logic x curated prices.yaml

  COMPUTED - R'_conv  = convention effect  (same prices litellm had; only
                         the accounting differs)
  R'_conv  - R'_full  = price staleness    (same accounting; only the
                         table differs)

Convention logic is PER ENTRY, from cache_convention_by_entry.csv (the
litellm layer flips meaning across versions -- see that module):
  inclusive           uncached = input_raw - cached - cache_write
  read_only_inclusive uncached = input_raw - cached        (write not folded in)
  exclusive           uncached = input_raw
  undetermined        no cache traffic observed -> same as inclusive (no effect)
cost = uncached*in + cached*cache_read + cache_write*cw_5m + output*out,
with >200k-input calls priced at the table's above-200k rates where it has
them, and flagged either way (5% gate, plan.md).

Reasoning is NOT folded into the primary numbers. For models classified
additive (reasoning_convention_by_model.csv) a separate column
r_conv_reasoning_additive_delta reports what adding reasoning to output
would cost -- secondary finding, mechanism unresolved.

Entries whose curated price row is unverified or null get
staleness_delta = null and staleness_status explaining why. R'_full is
never silently set equal to R'_conv: zero-by-construction is not agreement.

    python -m analysis.token_waste.cli price
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import requests
import yaml

from .claim1 import OUT_DIR, load_all_entry_stats
from .config import DATA_DIR, LONG_CTX_THRESHOLD, TOKEN_WASTE_DIR
from .entries import EntryMeta, select_entries
from .providers import infer_provider, strip_routing_prefix
from .s3 import _get_with_backoff

CALLS_DIR = DATA_DIR / "calls"
LITELLM_CACHE_DIR = DATA_DIR / "litellm_prices"
PINS_PATH = TOKEN_WASTE_DIR / "litellm_pins.yaml"
PRICES_PATH = TOKEN_WASTE_DIR / "prices.yaml"
LITELLM_REPO = "BerriAI/litellm"
LITELLM_FILE = "model_prices_and_context_window.json"
# From info.config.model in the trajs (checked 2026-09-20; to be captured in
# parse.py at M6). A custom registry means the submitter's COMPUTED $ came
# from a price file nobody else can see; the litellm table at the pin date
# has no row for these models at all.
COMPUTED_PRICE_SOURCE = {
    "20251209_mini-v1.17.2_devstral-2512": "custom_registry:/home/klieret/litellm_model_registry.json",
    "20251209_mini-v1.17.2_devstral-small-2512": "custom_registry:/home/klieret/litellm_model_registry.json",
    "20251201_mini-v1.17.1_glm-4.6": "custom_registry:/home/klieret/litellm_model_registry.json",
    "20260217_mini-v2.0.0_glm-5-high": "cost_tracking=ignore_errors; model absent from litellm table at pin",
}
DUPLICATE_ENTRIES = {"20260219_mini-v2.0.0_gpt-5-2-codex": "20260217_mini-v2.0.0_gpt-5-2-high"}


class UnknownModelError(Exception):
    pass


# ---------------------------------------------------------------------------
# litellm table, pinned per submission date
# ---------------------------------------------------------------------------


def _load_pins() -> dict:
    if PINS_PATH.is_file():
        return yaml.safe_load(PINS_PATH.read_text()) or {}
    return {}


def _save_pins(pins: dict) -> None:
    PINS_PATH.write_text(
        "# litellm model_prices_and_context_window.json commits pinned per submission\n"
        "# date: the last commit touching the file on or before that date. Snapshots\n"
        "# are cached (gitignored) under data/litellm_prices/<sha>.json.\n"
        + yaml.safe_dump(pins, sort_keys=True)
    )


def pin_sha_for_date(d: date) -> str:
    pins = _load_pins()
    key = d.isoformat()
    if key in pins:
        return pins[key]["sha"]
    url = f"https://api.github.com/repos/{LITELLM_REPO}/commits"
    resp = _get_with_backoff(
        url, params={"path": LITELLM_FILE, "until": f"{key}T23:59:59Z", "per_page": "1"}
    )
    data = resp.json()
    if not data:
        raise RuntimeError(f"no litellm commit touching {LITELLM_FILE} on/before {key}")
    sha = data[0]["sha"]
    pins[key] = {"sha": sha, "commit_date": data[0]["commit"]["committer"]["date"]}
    _save_pins(pins)
    return sha


def litellm_table(sha: str) -> dict:
    LITELLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = LITELLM_CACHE_DIR / f"{sha}.json"
    if not path.is_file():
        url = f"https://raw.githubusercontent.com/{LITELLM_REPO}/{sha}/{LITELLM_FILE}"
        path.write_bytes(_get_with_backoff(url).content)
    return json.loads(path.read_text())


_LITELLM_PREFIXES = {
    "anthropic": ["", "anthropic/"],
    "openai": ["", "openai/"],
    "google": ["gemini/", "", "vertex_ai/"],
    "deepseek": ["deepseek/", ""],
    "zhipu": ["zai/", ""],
    "moonshot": ["moonshot/", "moonshotai/", ""],
    "minimax": ["minimax/", ""],
    "mistral": ["mistral/", ""],
}


def _date_stripped(name: str) -> str:
    import re

    return re.sub(r"-(\d{8}|\d{4}-\d{2}-\d{2})$", "", name)


def litellm_lookup(table: dict, model: str) -> tuple[str, dict]:
    """Resolve an answering_model to a litellm table row. Tries provider
    prefixes and a date-stripped alias. Raises UnknownModelError with the
    candidates tried -- never a silent miss."""
    base = strip_routing_prefix(model)
    provider = infer_provider(model) or ""
    lower = {k.lower(): k for k in table}  # litellm keys are case-inconsistent (minimax/MiniMax-M2)
    tried = []
    for cand in (base, _date_stripped(base)):
        for pre in _LITELLM_PREFIXES.get(provider, ["", f"{provider}/"]):
            key = (pre + cand).lower()
            tried.append(key)
            real = lower.get(key)
            if real and "input_cost_per_token" in table[real]:
                return real, table[real]
    raise UnknownModelError(f"{model!r} not in litellm table; tried {tried}")


# ---------------------------------------------------------------------------
# Curated table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceRow:
    source: str  # "litellm:<key>" | "curated:<model>"
    input_per_m: float
    cached_input_per_m: float
    cache_write_5m_per_m: float
    cache_write_1h_per_m: float | None
    output_per_m: float
    long_ctx_threshold: int | None
    long_ctx_input_per_m: float | None
    long_ctx_output_per_m: float | None
    verified: bool
    note: str = ""


def row_from_litellm(key: str, r: dict) -> PriceRow:
    m = 1_000_000
    inp = r["input_cost_per_token"] * m
    return PriceRow(
        source=f"litellm:{key}",
        input_per_m=inp,
        # No cache_read cost in the row = litellm has no discount on file, so it
        # bills cached tokens at the full input rate. Mirror that; 0 would read
        # as "cached tokens are free", which is the opposite error.
        cached_input_per_m=(r["cache_read_input_token_cost"] * m) if r.get("cache_read_input_token_cost") is not None else inp,
        cache_write_5m_per_m=(r.get("cache_creation_input_token_cost") or 0.0) * m,
        cache_write_1h_per_m=(r.get("cache_creation_input_token_cost_above_1hr") or None) and r["cache_creation_input_token_cost_above_1hr"] * m,
        output_per_m=r["output_cost_per_token"] * m,
        long_ctx_threshold=200_000 if r.get("input_cost_per_token_above_200k_tokens") else None,
        long_ctx_input_per_m=(r.get("input_cost_per_token_above_200k_tokens") or None) and r["input_cost_per_token_above_200k_tokens"] * m,
        long_ctx_output_per_m=(r.get("output_cost_per_token_above_200k_tokens") or None) and r["output_cost_per_token_above_200k_tokens"] * m,
        verified=True,
    )


def load_curated() -> dict[str, PriceRow]:
    out = {}
    for r in yaml.safe_load(PRICES_PATH.read_text()) or []:
        if r.get("input_per_m") is None:
            continue  # null row: cannot price; staleness undefined for its entries
        out[r["model"]] = PriceRow(
            source=f"curated:{r['model']}",
            input_per_m=r["input_per_m"],
            cached_input_per_m=r["cached_input_per_m"],
            cache_write_5m_per_m=r["cache_write_5m_per_m"] or 0.0,
            cache_write_1h_per_m=r.get("cache_write_1h_per_m"),
            output_per_m=r["output_per_m"],
            long_ctx_threshold=r.get("long_ctx_threshold"),
            long_ctx_input_per_m=r.get("long_ctx_input_per_m"),
            long_ctx_output_per_m=r.get("long_ctx_output_per_m"),
            verified=bool(r.get("verified")),
            note=r.get("note", ""),
        )
    return out


def curated_lookup(curated: dict[str, PriceRow], model: str) -> PriceRow | None:
    base = strip_routing_prefix(model)
    return curated.get(base) or curated.get(_date_stripped(base))


# ---------------------------------------------------------------------------
# Convention + per-call pricing
# ---------------------------------------------------------------------------


def load_conventions() -> dict[str, str]:
    path = OUT_DIR / "cache_convention_by_entry.csv"
    with path.open() as f:
        return {r["entry"]: r["convention"] for r in csv.DictReader(f)}


def uncached_input(call: dict, convention: str) -> int | None:
    inp, cached, cw = call.get("input_tokens_raw"), call.get("cached_tokens"), call.get("cache_write_tokens")
    if inp is None:
        return None
    cached = cached or 0
    if convention == "exclusive":
        return inp
    if convention == "read_only_inclusive":
        return max(inp - cached, 0)
    # inclusive / undetermined (no traffic -> identical)
    return max(inp - cached - (cw or 0), 0)


def price_call(call: dict, convention: str, row: PriceRow) -> tuple[float | None, bool]:
    """(cost_usd, long_context_flag). None if any REQUIRED field is unknown
    (measured.py discipline: unknown never becomes 0)."""
    unc = uncached_input(call, convention)
    out = call.get("output_tokens")
    if unc is None or out is None:
        return None, False
    cached = call.get("cached_tokens") or 0
    cw = call.get("cache_write_tokens")  # None = unknown -> priced as 0 but see coverage
    inp_raw = call.get("input_tokens_raw") or 0
    long_ctx = inp_raw > LONG_CTX_THRESHOLD
    in_rate, out_rate = row.input_per_m, row.output_per_m
    if long_ctx and row.long_ctx_threshold and row.long_ctx_input_per_m:
        in_rate = row.long_ctx_input_per_m
        out_rate = row.long_ctx_output_per_m or out_rate
    cost = (
        unc * in_rate
        + cached * row.cached_input_per_m
        + (cw or 0) * row.cache_write_5m_per_m
        + out * out_rate
    ) / 1_000_000
    return cost, long_ctx


# ---------------------------------------------------------------------------
# Entry-level dual run
# ---------------------------------------------------------------------------


def _mini_version_bucket(v: str | None) -> str:
    return v or "?"


def price_entry(e: EntryMeta, conventions: dict[str, str], curated: dict[str, PriceRow],
                additive_models: set[str], computed: float | None) -> dict:
    path = CALLS_DIR / f"{e.entry}.parquet"
    t = pq.read_table(path)
    if "answering_model" not in t.column_names:
        return None
    calls = t.to_pylist()
    convention = conventions.get(e.entry, "undetermined")
    sha = pin_sha_for_date(e.submission_date)
    table = litellm_table(sha)

    r_conv = 0.0
    r_full = 0.0
    n_priced_conv = n_priced_full = 0
    n_unknown_model = 0
    n_long = 0
    long_dollars = 0.0
    reasoning_delta = 0.0
    cw_unknown = 0
    litellm_keys: set[str] = set()
    curated_rows: set[str] = set()
    full_ok = True
    curated_verified = True

    for c in calls:
        model = c.get("answering_model")
        try:
            key, lrow = litellm_lookup(table, model)
        except UnknownModelError:
            n_unknown_model += 1
            continue
        litellm_keys.add(key)
        prow = row_from_litellm(key, lrow)
        cost, long_ctx = price_call(c, convention, prow)
        if cost is None:
            continue
        r_conv += cost
        n_priced_conv += 1
        if long_ctx:
            n_long += 1
            long_dollars += cost
        if c.get("cache_write_tokens") is None:
            cw_unknown += 1
        if model in additive_models and c.get("reasoning_tokens"):
            reasoning_delta += c["reasoning_tokens"] * prow.output_per_m / 1_000_000

        crow = curated_lookup(curated, model)
        if crow is None:
            full_ok = False
            continue
        curated_rows.add(crow.source)
        curated_verified = curated_verified and crow.verified
        cfull, _ = price_call(c, convention, crow)
        if cfull is not None:
            r_full += cfull
            n_priced_full += 1

    if n_priced_conv == 0:
        # Nothing priceable: every call either has no litellm row at the pin
        # (unknown model) or no usable usage fields. r_conv must be null,
        # never a 0 that reads as "free".
        staleness_status = "no_litellm_row_at_pin" if n_unknown_model == len(calls) else "usage_fields_unknown"
        r_conv = None
        r_full_out = None
    elif not full_ok or n_priced_full != n_priced_conv:
        staleness_status = "curated_row_missing_or_null"
        r_full_out = None
    elif not curated_verified:
        staleness_status = "curated_row_unverified"
        r_full_out = r_full
    else:
        staleness_status = "ok"
        r_full_out = r_full

    d_conv = None if computed is None or r_conv is None else computed - r_conv
    d_price = None if r_full_out is None or staleness_status != "ok" else r_conv - r_full_out
    return dict(
        entry=e.entry,
        mini_version=_mini_version_bucket(e.mini_version),
        submission_date=e.submission_date.isoformat(),
        litellm_sha=sha[:12],
        litellm_keys=";".join(sorted(litellm_keys)),
        curated_rows=";".join(sorted(curated_rows)),
        convention=convention,
        n_calls=len(calls),
        n_priced=n_priced_conv,
        n_unknown_model=n_unknown_model,
        n_cache_write_unknown=cw_unknown,
        computed=computed,
        r_conv=r_conv,
        r_full=r_full_out,
        d_conv=d_conv,
        d_conv_rel=None if not computed or d_conv is None else d_conv / computed,
        computed_price_source=COMPUTED_PRICE_SOURCE.get(e.entry, "litellm_table"),
        long_context_dollars=long_dollars,
        d_price=d_price,
        d_price_rel=None if d_price is None or not r_conv else d_price / r_conv,
        staleness_status=staleness_status,
        n_long_context_calls=n_long,
        long_context_dollar_share=(long_dollars / r_conv) if r_conv else None,
        r_conv_reasoning_additive_delta=reasoning_delta,
        duplicate_of=DUPLICATE_ENTRIES.get(e.entry),
    )


def _additive_models() -> set[str]:
    path = OUT_DIR / "reasoning_convention_by_model.csv"
    if not path.is_file():
        return set()
    with path.open() as f:
        return {r["model"] for r in csv.DictReader(f) if r["classification"] == "additive"}


def run() -> list[dict]:
    conventions = load_conventions()
    curated = load_curated()
    additive = _additive_models()
    computed_by_entry = {s.entry: s.total_cost_observed for s in load_all_entry_stats()}
    rows = []
    for e in select_entries():
        if not (CALLS_DIR / f"{e.entry}.parquet").is_file():
            continue
        r = price_entry(e, conventions, curated, additive, computed_by_entry.get(e.entry))
        if r:
            rows.append(r)
    out = OUT_DIR / "claim2_price_by_entry.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}")
    return rows
