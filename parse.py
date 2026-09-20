"""Milestone 3: pure traj parsing. Traj JSON in, (InstanceRow, calls,
quarantine) out. NO network, NO pricing, NO dollars anywhere in this module
-- that's s3.py/extract.py (network) and pricing.py (milestone 5, dollars).

Dispatch order (plan.md "parse.py dispatch", most specific first):
  1. any msg with object=="response" and a usage key -> B_responses
     (Responses-format assistant turns have no "role" key at all, so the
     dispatch signal is "has usage + response shape", not role)
  2. any role=="assistant" with extra.response.usage -> B_chat
  3. else A_dollar (needs info.model_stats; if that's also absent, tier is
     "unknown" -- callers (extract.py, milestone 4) must treat that as an
     abort condition, not silently proceed)

Discipline carried from Step 0 (see measured.py): a token field that isn't
in the JSON is null, never 0 -- UNLESS its true value really is known to be
zero, in which case it should be a real 0, not a null that quietly shrinks
some future denominator. Every value is tagged with how it was obtained
(`provenance`, one of):

  measured             -- read directly from the traj; a real value, may be 0.
  structurally_absent   -- the provider/API shape never bills or reports this
                           field at all; true value is 0. Applied by
                           extract.py from provider_flags.yaml only where a
                           row is verified -- never assumed from a provider
                           name alone, never decided inside parse.py.
  known_zero            -- the field is a real, billable quantity but this
                           particular call didn't trigger it (e.g. adaptive
                           extended thinking that chose not to think this
                           turn); true value is 0.
  unknown                -- genuinely missing, cause not established. MUST
                           propagate as null into every downstream numerator
                           AND denominator (see measured.py) -- this is the
                           only tag that is allowed to shrink a denominator.
  derived                -- unused here, reserved for pricing.py (milestone
                           5), which computes uncached-input from
                           input_raw - cached.

structurally_absent and known_zero both store the value 0 (a real, priceable
number); only "unknown" stores None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

TIER_B_RESPONSES = "B_responses"
TIER_B_CHAT = "B_chat"
TIER_A_DOLLAR = "A_dollar"
TIER_UNKNOWN = "unknown"

MODE_TOOLCALL = "toolcall"
MODE_TEXTBASED = "textbased"

TOKEN_FIELDS = (
    "input_tokens_raw",
    "cached_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
)


@dataclass
class QuarantineRow:
    instance_id: str
    call_idx: int
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallRow:
    call_idx: int
    answering_model_raw: str | None
    answering_model: str | None  # after fallback substitution
    model_mismatch: bool | None  # True/False if determinable, None if raw was absent (fell back)
    input_tokens_raw: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    # True/False when both are known; None when either is absent. NOT a
    # data-quality signal by itself -- some providers report reasoning_tokens
    # additive to output_tokens rather than as a subset of it (see
    # reasoning_convention.py, which classifies this per-provider from the
    # pooled data and is the only thing entitled to call it an anomaly).
    reasoning_exceeds_output: bool | None
    provenance: dict[str, str] = field(default_factory=dict)  # field name -> measured/structurally_absent/known_zero/unknown/derived


@dataclass
class InstanceRow:
    instance_id: str
    tier: str
    mode: str
    requested_model: str | None  # from info.config.model.model_name, when present in the traj
    n_calls: int  # structural count of assistant/response turns, independent of token coverage
    api_calls_reported: int | None  # info.model_stats.api_calls; None (not 0) if the field is absent
    n_calls_unaccounted: int | None  # api_calls_reported - n_calls; None if api_calls_reported is absent
    exit_status: str | None
    n_quarantined: int


# ---------------------------------------------------------------------------
# Key filtering / dedup (pure -- used by s3.py/extract.py in milestone 4)
# ---------------------------------------------------------------------------


# v0.0.0/v1.0.0-era submissions store the trajectory itself as bare
# "<iid>.traj" (no .json), alongside real siblings ".config.yaml",
# ".debug.log", ".info.log", ".patch", ".pred", ".trace.log" at the same
# prefix. ".traj.json" is checked first so it never gets truncated by the
# shorter ".traj" suffix.
_TRAJ_SUFFIXES = (".traj.json", ".traj")


def instance_id_from_key(key: str) -> str | None:
    """'.../<iid>/<iid>.traj[.json]' -> '<iid>'. Anything not ending in one
    of _TRAJ_SUFFIXES returns None -- this is how a v0.0.0 entry's
    .config.yaml/.debug.log/etc siblings get filtered out."""
    name = key.rsplit("/", 1)[-1]
    for suffix in _TRAJ_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def filter_traj_keys(keys: list[str]) -> list[str]:
    """Only *.traj / *.traj.json. Every other file at the same prefix
    (.config.yaml, .debug.log, .info.log, .patch, .pred, .trace.log) must
    never be treated as a trajectory."""
    return [k for k in keys if instance_id_from_key(k) is not None]


def dedupe_instance_keys(keys: list[str]) -> tuple[dict[str, str], list[dict]]:
    """Keep the first key seen for each instance_id; log (not crash on) the
    rest. Callers should filter_traj_keys() first."""
    kept: dict[str, str] = {}
    dropped: list[dict] = []
    for k in keys:
        iid = instance_id_from_key(k)
        if iid is None:
            continue
        if iid in kept:
            dropped.append({"instance_id": iid, "kept_key": kept[iid], "dropped_key": k})
        else:
            kept[iid] = k
    return kept, dropped


# ---------------------------------------------------------------------------
# Tier / mode detection
# ---------------------------------------------------------------------------


def detect_tier(traj: dict) -> str:
    messages = traj.get("messages") or []
    for m in messages:
        if m.get("object") == "response" and m.get("usage") is not None:
            return TIER_B_RESPONSES
    for m in messages:
        if m.get("role") == "assistant":
            resp = (m.get("extra") or {}).get("response") or {}
            if resp.get("usage") is not None:
                return TIER_B_CHAT
    if (traj.get("info") or {}).get("model_stats") is not None:
        return TIER_A_DOLLAR
    return TIER_UNKNOWN


def detect_mode(traj: dict) -> str:
    for m in traj.get("messages") or []:
        if m.get("type") == "function_call_output":
            return MODE_TOOLCALL
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return MODE_TOOLCALL
    return MODE_TEXTBASED


_ROUTING_PREFIX = re.compile(r"^@?[a-z0-9_.-]+/")


def _strip_routing_prefix(name: str) -> str:
    return _ROUTING_PREFIX.sub("", name)


def _requested_model(traj: dict) -> str | None:
    cfg = ((traj.get("info") or {}).get("config") or {}).get("model") or {}
    return cfg.get("model_name")


def _thinking_capability(traj: dict) -> bool:
    """True if this entry's model config shows an extended-thinking /
    reasoning-effort knob at all (adaptive or forced-on) -- meaning a call
    with no reasoning_tokens is a real "chose not to think this turn" zero,
    not missing data. False means no such knob was found in config, in
    which case an absent reasoning_tokens is left "unknown" rather than
    guessed at: we have not verified, per provider/model, that reasoning
    tokens are structurally never billed there (see module docstring on
    why structurally_absent requires independent verification)."""
    model_kwargs = (
        ((traj.get("info") or {}).get("config") or {}).get("model") or {}
    ).get("model_kwargs")
    if not isinstance(model_kwargs, dict):
        return False
    thinking = model_kwargs.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") not in (None, "disabled", "off"):
        return True
    if model_kwargs.get("reasoning_effort"):
        return True
    if "reasoning" in model_kwargs:
        return True
    return False




# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def _pick(*sources: tuple[dict | None, str]) -> tuple[Any, str]:
    """First (dict, key) pair where the key is present and not None.
    Returns (value, "measured") or (None, "unknown") -- a field that's in
    the JSON with an explicit null is treated the same as a missing key:
    both mean "not measured". "unknown" is the tag's default state; callers
    with independently-verified reasons to believe the true value is 0
    (_thinking_capability here; provider_flags.yaml in extract.py) reclassify
    it to structurally_absent/known_zero afterward -- this function itself
    never guesses."""
    for d, key in sources:
        if d and key in d and d[key] is not None:
            return d[key], "measured"
    return None, "unknown"


def _bchat_fields(msg: dict) -> tuple[dict[str, Any], dict[str, str], str | None]:
    resp = (msg.get("extra") or {}).get("response") or {}
    usage = resp.get("usage") or {}
    ptd = usage.get("prompt_tokens_details") or {}
    ctd = usage.get("completion_tokens_details") or {}

    values: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    values["input_tokens_raw"], provenance["input_tokens_raw"] = _pick((usage, "prompt_tokens"))
    values["cached_tokens"], provenance["cached_tokens"] = _pick(
        (usage, "cache_read_input_tokens"), (ptd, "cached_tokens")
    )
    values["cache_write_tokens"], provenance["cache_write_tokens"] = _pick(
        (usage, "cache_creation_input_tokens"), (ptd, "cache_creation_tokens")
    )
    values["output_tokens"], provenance["output_tokens"] = _pick((usage, "completion_tokens"))
    values["reasoning_tokens"], provenance["reasoning_tokens"] = _pick((ctd, "reasoning_tokens"))
    return values, provenance, resp.get("model")


def _bresponses_fields(msg: dict) -> tuple[dict[str, Any], dict[str, str], str | None]:
    usage = msg.get("usage") or {}
    itd = usage.get("input_tokens_details") or {}
    otd = usage.get("output_tokens_details") or {}

    values: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    values["input_tokens_raw"], provenance["input_tokens_raw"] = _pick((usage, "input_tokens"))
    values["cached_tokens"], provenance["cached_tokens"] = _pick((itd, "cached_tokens"))
    # The Responses API shape observed here exposes no cache-write field at
    # all -- always absent from the JSON. Whether that means 0 (verified,
    # e.g. OpenAI pre-5.6) or unknown is decided in _build_call_row, which
    # has the answering model name to check against.
    values["cache_write_tokens"], provenance["cache_write_tokens"] = _pick()
    values["output_tokens"], provenance["output_tokens"] = _pick((usage, "output_tokens"))
    values["reasoning_tokens"], provenance["reasoning_tokens"] = _pick((otd, "reasoning_tokens"))
    # usage.cost exists in this shape but is explicitly ignored -- no
    # dollars in this module (plan.md: "usage.cost ignored").
    return values, provenance, msg.get("model")


def _build_call_row(
    instance_id: str,
    call_idx: int,
    values: dict[str, Any],
    provenance: dict[str, str],
    model_raw: str | None,
    requested_model: str | None,
    reasoning_capable: bool,
) -> tuple[CallRow, QuarantineRow | None]:
    if model_raw is not None:
        answering_model = model_raw
        # Compare with litellm routing prefixes stripped ("openai/x" vs "x"):
        # the requested side carries the prefix, the answering side never
        # does, so a raw compare flags every call of every entry. Dated
        # snapshot aliases (gpt-5-mini vs gpt-5-mini-2025-08-07) are NOT
        # collapsed here -- that is pricing.py's normalize_model job.
        model_mismatch = (
            requested_model is not None
            and _strip_routing_prefix(model_raw) != _strip_routing_prefix(requested_model)
        )
    else:
        # Fallback per plan.md: substitute the requested model, but the
        # mismatch flag becomes None (unknown), not False -- we have no
        # evidence either way, so we must not claim a match.
        answering_model = requested_model
        model_mismatch = None

    # Reclassify absent reasoning from the traj's OWN config (adaptive /
    # forced thinking -> known_zero). cache_write structurally_absent needs
    # provider_flags.yaml (external, verified per row) and is applied by
    # extract.py after parsing -- parse.py stays pure and never guesses.
    if values["reasoning_tokens"] is None and reasoning_capable:
        values["reasoning_tokens"] = 0
        provenance["reasoning_tokens"] = "known_zero"

    reasoning = values["reasoning_tokens"]
    output = values["output_tokens"]
    reasoning_exceeds_output = None if reasoning is None or output is None else reasoning > output

    row = CallRow(
        call_idx=call_idx,
        answering_model_raw=model_raw,
        answering_model=answering_model,
        model_mismatch=model_mismatch,
        input_tokens_raw=values["input_tokens_raw"],
        cached_tokens=values["cached_tokens"],
        cache_write_tokens=values["cache_write_tokens"],
        output_tokens=values["output_tokens"],
        reasoning_tokens=values["reasoning_tokens"],
        reasoning_exceeds_output=reasoning_exceeds_output,
        provenance=provenance,
    )

    # reasoning_tokens > output_tokens is NOT quarantined: it was originally
    # treated as a data-integrity violation (assumption: reasoning is always
    # a subset of output, true for Anthropic/OpenAI in observed data), but
    # empirically several providers (Gemini, MiniMax, GLM, Kimi) report
    # reasoning as ADDITIVE to output instead -- a real convention
    # difference, not bad data. Classification now happens once, per
    # provider, across the whole pooled dataset (reasoning_convention.py),
    # not per-call here -- a single traj can't see whether ANY OTHER call
    # from the same provider ever violated the subset assumption, which is
    # what the classification rule requires.
    cached = row.cached_tokens
    input_raw = row.input_tokens_raw
    if cached is not None and input_raw is not None and cached > input_raw:
        return row, QuarantineRow(
            instance_id=instance_id,
            call_idx=call_idx,
            reason="cached_tokens > input_tokens_raw",
            detail={"cached_tokens": cached, "input_tokens_raw": input_raw},
        )
    return row, None


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def parse_traj(traj: dict, instance_id: str | None = None) -> tuple[InstanceRow, list[CallRow], list[QuarantineRow]]:
    """Pure function: traj dict in, (InstanceRow, calls, quarantine) out.

    A quarantined call is EXCLUDED from the returned `calls` list (so
    downstream aggregation never silently uses a value that failed an
    invariant) but fully recorded in `quarantine` (so nothing is dropped
    without a trace) -- "do not crash, do not silently drop".
    """
    iid = instance_id or traj.get("instance_id") or ""
    tier = detect_tier(traj)
    mode = detect_mode(traj)
    requested_model = _requested_model(traj)
    info = traj.get("info") or {}
    exit_status = info.get("exit_status")
    model_stats = info.get("model_stats") or {}
    api_calls_reported = model_stats.get("api_calls")  # None (not 0) if absent -- see measured.py
    reasoning_capable = _thinking_capability(traj)

    messages = traj.get("messages") or []
    calls: list[CallRow] = []
    quarantine: list[QuarantineRow] = []
    call_idx = 0
    n_structural_calls = 0

    if tier == TIER_B_RESPONSES:
        for m in messages:
            if not (m.get("object") == "response" and m.get("usage") is not None):
                continue
            n_structural_calls += 1
            values, provenance, model_raw = _bresponses_fields(m)
            row, quarantined = _build_call_row(
                iid, call_idx, values, provenance, model_raw, requested_model, reasoning_capable
            )
            call_idx += 1
            if quarantined is not None:
                quarantine.append(quarantined)
            else:
                calls.append(row)
    elif tier == TIER_B_CHAT:
        for m in messages:
            if m.get("role") != "assistant":
                continue
            n_structural_calls += 1
            resp = (m.get("extra") or {}).get("response") or {}
            if resp.get("usage") is None:
                # An assistant turn with no usage at all: still a real call
                # (n_calls counts it) but contributes no CallRow -- there is
                # nothing to quarantine, there's simply no data.
                call_idx += 1
                continue
            values, provenance, model_raw = _bchat_fields(m)
            row, quarantined = _build_call_row(
                iid, call_idx, values, provenance, model_raw, requested_model, reasoning_capable
            )
            call_idx += 1
            if quarantined is not None:
                quarantine.append(quarantined)
            else:
                calls.append(row)
    elif tier == TIER_A_DOLLAR:
        # A_dollar has no per-call token data by definition -- structural
        # call count only (assistant turns), no CallRows at all. Token
        # columns are null because there are no calls to have them, not
        # because we zeroed anything.
        for m in messages:
            if m.get("role") == "assistant":
                n_structural_calls += 1
    # TIER_UNKNOWN: n_structural_calls stays 0, calls/quarantine stay empty.
    # Callers (extract.py, milestone 4) must treat "unknown" as an abort
    # condition -- this module never guesses.

    n_calls_unaccounted = (
        api_calls_reported - n_structural_calls if api_calls_reported is not None else None
    )
    instance_row = InstanceRow(
        instance_id=iid,
        tier=tier,
        mode=mode,
        requested_model=requested_model,
        n_calls=n_structural_calls,
        api_calls_reported=api_calls_reported,
        n_calls_unaccounted=n_calls_unaccounted,
        exit_status=exit_status,
        n_quarantined=len(quarantine),
    )
    return instance_row, calls, quarantine
