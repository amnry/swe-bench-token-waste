"""Select and describe the mini-SWE-agent leaderboard entries in scope.

Scope (see plan.md "Scope"): SWE-bench Verified entries whose metadata.yaml
carries info.mini-swe-agent_version. No network access here -- everything
comes from files already checked into this repo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .config import EVALUATION

SPLIT = "verified"

# See plan.md "Three data tiers". These are pre-parse GUESSES from the
# mini-swe-agent version alone; parse.py (M3) determines the real tier by
# reading the trajectory and is authoritative. The version ranges below
# overlap (v1.13-v1.9.1 could plausibly be either A_dollar or B_chat) because
# that ambiguity is real before a traj is actually inspected.
_A_DOLLAR_MAX = (1, 9, 1)
_B_CHAT_MIN = (1, 13, 0)

# The 3 known B_responses entries (raw OpenAI Responses objects in the traj,
# no "role" key) -- established by exploration, not inferable from metadata
# alone. See plan.md context section.
_B_RESPONSES_ENTRIES = {
    "20260217_mini-v2.0.0_gpt-5-2-high",
    "20260217_mini-v2.0.0_gpt-5-mini",
    "20260219_mini-v2.0.0_gpt-5-2-codex",
}

_DATE_PREFIX = re.compile(r"^(\d{8})_")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class EntryMeta:
    entry: str
    submission_date: date | None
    model_display: str | None
    org: str | None
    requested_model: str | None
    mini_version: str | None
    mini_version_tuple: tuple[int, int, int] | None
    reasoning_effort: str | None
    data_tier: str  # "A_dollar" | "B_chat" | "B_responses" | "unknown" -- pre-parse guess
    has_details: bool
    reported_cost_usd: float | None
    reported_resolved_pct: float | None
    traj_source: str  # "s3" | "github" | "none"
    trajs_url: str | None
    path: Path


def _metadata_path(entry: Path) -> Path | None:
    for name in ("metadata.yaml", "metadata.yml"):
        if (entry / name).is_file():
            return entry / name
    return None


def _parse_version(raw: object) -> tuple[str | None, tuple[int, int, int] | None]:
    if raw is None:
        return None, None
    s = str(raw)
    m = _VERSION_RE.match(s)
    if not m:
        return s, None
    return s, tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def _guess_tier(entry_name: str, version_tuple: tuple[int, int, int] | None) -> str:
    if entry_name in _B_RESPONSES_ENTRIES:
        return "B_responses"
    if version_tuple is None:
        return "unknown"
    if version_tuple >= _B_CHAT_MIN:
        return "B_chat"
    if version_tuple <= _A_DOLLAR_MAX:
        return "A_dollar"
    return "unknown"


def _submission_date(entry_name: str) -> date | None:
    m = _DATE_PREFIX.match(entry_name)
    if not m:
        return None
    try:
        return date(int(m.group(1)[0:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
    except ValueError:
        return None


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        # tags.org and tags.model are sometimes a one-item list, sometimes a
        # bare string, across entries of different vintages.
        return ", ".join(str(v) for v in value) if value else None
    return str(value)


def _first_of(value: object) -> str | None:
    if isinstance(value, list):
        return str(value[0]) if value else None
    return _as_str(value)


def _traj_source(assets: dict) -> tuple[str, str | None]:
    trajs = assets.get("trajs")
    if isinstance(trajs, str) and trajs.startswith("s3://"):
        return "s3", trajs
    repo = assets.get("repo")
    if isinstance(repo, str) and repo:
        return "github", repo
    if isinstance(trajs, str) and trajs:
        return "github", trajs
    return "none", None


def _is_mini_entry(meta: dict) -> bool:
    return (meta.get("info") or {}).get("mini-swe-agent_version") is not None


def load_entry(entry_dir: Path) -> EntryMeta | None:
    """Parse one evaluation/verified/<entry> directory. Returns None if it is
    not a mini-SWE-agent entry (or has no readable metadata)."""
    meta_path = _metadata_path(entry_dir)
    if meta_path is None:
        return None
    try:
        meta = yaml.safe_load(meta_path.read_text()) or {}
    except yaml.YAMLError:
        return None
    if not _is_mini_entry(meta):
        return None

    info = meta.get("info") or {}
    tags = meta.get("tags") or {}
    assets = meta.get("assets") or {}

    mini_version, version_tuple = _parse_version(info.get("mini-swe-agent_version"))
    traj_source, trajs_url = _traj_source(assets)
    details_path = entry_dir / "per_instance_details.json"

    return EntryMeta(
        entry=entry_dir.name,
        submission_date=_submission_date(entry_dir.name),
        model_display=_as_str(tags.get("model_display")) or _as_str(info.get("name")),
        org=_as_str(tags.get("org")),
        requested_model=_first_of(tags.get("model")),
        mini_version=mini_version,
        mini_version_tuple=version_tuple,
        reasoning_effort=_as_str(tags.get("reasoning_effort")),
        data_tier=_guess_tier(entry_dir.name, version_tuple),
        has_details=details_path.is_file(),
        reported_cost_usd=_to_float(info.get("cost")),
        reported_resolved_pct=_to_float(info.get("resolved")),
        traj_source=traj_source,
        trajs_url=trajs_url,
        path=entry_dir,
    )


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def select_entries() -> list[EntryMeta]:
    """All mini-SWE-agent entries under evaluation/verified/, sorted by name.

    No network access. Reuses the same directory-walking shape as
    validate_entries.all_entries(), narrowed to the verified split and
    filtered to mini-SWE-agent entries.
    """
    split_dir = EVALUATION / SPLIT
    if not split_dir.is_dir():
        return []
    out = []
    for entry_dir in sorted(d for d in split_dir.iterdir() if d.is_dir()):
        em = load_entry(entry_dir)
        if em is not None:
            out.append(em)
    return out


def load_per_instance_details(entry: EntryMeta) -> dict[str, dict] | None:
    """instance_id -> {cost, api_calls, resolved}, or None if the entry has none."""
    if not entry.has_details:
        return None
    path = entry.path / "per_instance_details.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
