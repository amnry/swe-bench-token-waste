"""Milestone 4: per-entry extract -- list keys on S3/GitHub, fetch+parse
each traj (ThreadPool), write data/instances/<entry>.parquet and
data/calls/<entry>.parquet. Still no pricing, no dollar arithmetic --
this module moves bytes and calls parse.py; pricing.py (milestone 5) is
untouched.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from .claim1 import canonical_verified_ids
from .config import DATA_DIR, EVALUATION
from .entries import EntryMeta, select_entries
from .field_coverage import coverage_for_calls, print_coverage
from .parse import (
    QuarantineRow,
    dedupe_instance_keys,
    filter_traj_keys,
    parse_traj,
)
from .providers import cache_write_billed
from .s3 import GithubRepoSource, fetch_json, list_keys

INSTANCES_DIR = DATA_DIR / "instances"
CALLS_DIR = DATA_DIR / "calls"
QUARANTINE_DIR = DATA_DIR / "quarantine"

N_WORKERS = 16


@dataclass
class ExtractReport:
    entry: str
    metadata_tier: str
    n_trajs_found: int
    n_expected: int
    n_observed: int
    n_no_gen_no_traj: int
    n_missing_traj_has_patch: int
    n_duplicate_keys_dropped: int
    n_fetch_errors: int
    parsed_tier_counts: dict[str, int]
    field_coverage: dict[str, dict]
    n_quarantined: int
    quarantine_reasons: dict[str, int]
    total_n_calls: int
    total_api_calls_reported: int | None
    n_calls_vs_api_calls_pct_diff: float | None
    total_n_calls_unaccounted: int  # sum of api_calls_reported - n_calls, per instance (0 where either is None)
    tier_matches_metadata_guess: bool | None


def _source_for_entry(entry: EntryMeta):
    """Returns (kind, list_fn, fetch_fn) where list_fn() -> [key] and
    fetch_fn(key) -> dict, for either S3 or the one GitHub-hosted entry."""
    if entry.traj_source == "s3":
        assert entry.trajs_url and entry.trajs_url.startswith("s3://")
        rest = entry.trajs_url[len("s3://") :]
        _bucket, _, prefix = rest.partition("/")
        prefix = prefix.rstrip("/") + "/"
        # metadata.yaml's assets.trajs URL is occasionally stale -- wrong
        # case, sometimes a rename the submitter never updated (found on
        # 2 entries: qwen3-coder-480b-a35b-instruct, kimi-k2-instruct,
        # both of which have a same-repo, correctly-cased folder matching
        # the entry's own directory name). Fall back to that before giving
        # up, rather than reporting a false "no trajs" / tier-detection
        # failure for an entry whose data is really there.
        fallback_prefix = f"bash-only/{entry.entry}/trajs/"

        def _list_with_fallback() -> list[str]:
            keys = list_keys(prefix)
            if not keys and fallback_prefix != prefix:
                keys = list_keys(fallback_prefix)
            return keys

        return "s3", _list_with_fallback, fetch_json

    if entry.traj_source == "github":
        meta_path = entry.path / "metadata.yaml"
        meta = yaml.safe_load(meta_path.read_text()) or {}
        assets = meta.get("assets") or {}
        info = meta.get("info") or {}
        repo_url = assets.get("repo", "")
        # https://github.com/<org>/<repo> -> "<org>/<repo>"
        repo = repo_url.rstrip("/").split("github.com/", 1)[-1]
        trajs_url = assets.get("trajs", "")
        # https://github.com/<org>/<repo>/tree/<ref>/<subdir> -> "<subdir>"
        subdir = ""
        marker = "/tree/"
        if marker in trajs_url:
            after = trajs_url.split(marker, 1)[1]
            subdir = after.split("/", 1)[1] if "/" in after else ""
        ref = info.get("commit") or "main"  # pin to the recorded commit when available
        src = GithubRepoSource(repo=repo, ref=ref, subdir=subdir)
        return "github", src.list_keys, src.fetch_json

    raise ValueError(f"entry {entry.entry!r} has traj_source={entry.traj_source!r}, nothing to fetch")


def _write_parquet_atomic(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        # Still write an empty file marker so a re-run without --force skips
        # correctly instead of re-fetching an entry that legitimately parsed
        # to zero rows.
        table = pa.table({})
    else:
        table = pa.Table.from_pylist(records)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".parquet.tmp")
    os.close(fd)
    try:
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _instance_row_dict(row, entry: str) -> dict:
    d = dataclasses.asdict(row)
    d["entry"] = entry
    return d


def _call_row_dict(row, entry: str, instance_id: str) -> dict:
    d = dataclasses.asdict(row)
    d["entry"] = entry
    d["instance_id"] = instance_id
    d["provenance"] = json.dumps(d["provenance"])  # parquet-friendly
    return d


def _quarantine_row_dict(row: QuarantineRow, entry: str) -> dict:
    d = dataclasses.asdict(row)
    d["entry"] = entry
    d["detail"] = json.dumps(d["detail"])
    return d


def extract_entry(entry: EntryMeta, force: bool = False) -> ExtractReport | None:
    """Returns None (skips network entirely) if outputs already exist and
    force=False."""
    instances_path = INSTANCES_DIR / f"{entry.entry}.parquet"
    calls_path = CALLS_DIR / f"{entry.entry}.parquet"
    quarantine_path = QUARANTINE_DIR / f"{entry.entry}.parquet"

    if not force and instances_path.is_file() and calls_path.is_file():
        return None

    _, list_fn, fetch_fn = _source_for_entry(entry)
    all_keys = list_fn()
    traj_keys = filter_traj_keys(all_keys)
    kept, dropped = dedupe_instance_keys(traj_keys)

    canonical_ids = canonical_verified_ids()
    per_instance_details = _load_details_raw(entry)

    instance_records: list[dict] = []
    call_records: list[dict] = []
    quarantine_records: list[dict] = []
    parsed_tier_counts: dict[str, int] = {}
    all_calls_for_coverage: list = []
    n_fetch_errors = 0

    def _fetch_and_parse(iid_key):
        iid, key = iid_key
        traj = fetch_fn(key)
        instance_row, calls, quarantine = parse_traj(traj, instance_id=iid)
        _apply_provider_flags(calls)
        return instance_row, calls, quarantine

    with ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
        futures = {pool.submit(_fetch_and_parse, item): item[0] for item in kept.items()}
        for fut in as_completed(futures):
            iid = futures[fut]
            try:
                instance_row, calls, quarantine = fut.result()
            except Exception as exc:  # noqa: BLE001 -- record and continue, never abort the whole entry on one bad fetch
                n_fetch_errors += 1
                print(f"  [extract] fetch/parse failed for {entry.entry}/{iid}: {exc}")
                continue
            parsed_tier_counts[instance_row.tier] = parsed_tier_counts.get(instance_row.tier, 0) + 1
            instance_records.append(_instance_row_dict(instance_row, entry.entry))
            for c in calls:
                call_records.append(_call_row_dict(c, entry.entry, iid))
            all_calls_for_coverage.extend(calls)
            for q in quarantine:
                quarantine_records.append(_quarantine_row_dict(q, entry.entry))

    _write_parquet_atomic(instance_records, instances_path)
    _write_parquet_atomic(call_records, calls_path)
    _write_parquet_atomic(quarantine_records, quarantine_path)

    observed_ids = set(kept.keys())
    n_no_gen_no_traj = 0
    n_missing_traj_has_patch = 0
    for iid in canonical_ids:
        if iid in observed_ids:
            continue
        if iid in per_instance_details:
            n_missing_traj_has_patch += 1
        else:
            n_no_gen_no_traj += 1

    field_coverage = coverage_for_calls(all_calls_for_coverage)

    total_n_calls = sum(r["n_calls"] for r in instance_records)
    api_calls_vals = [r["api_calls_reported"] for r in instance_records if r["api_calls_reported"] is not None]
    total_api_calls_reported = sum(api_calls_vals) if api_calls_vals else None
    pct_diff = None
    if total_api_calls_reported:
        pct_diff = abs(total_n_calls - total_api_calls_reported) / total_api_calls_reported * 100
    total_n_calls_unaccounted = sum(
        r["n_calls_unaccounted"] for r in instance_records if r["n_calls_unaccounted"] is not None
    )

    quarantine_reasons: dict[str, int] = {}
    for r in quarantine_records:
        quarantine_reasons[r["reason"]] = quarantine_reasons.get(r["reason"], 0) + 1

    parsed_tier = max(parsed_tier_counts, key=parsed_tier_counts.get) if parsed_tier_counts else None
    tier_match = None if parsed_tier is None else (parsed_tier == entry.data_tier)

    return ExtractReport(
        entry=entry.entry,
        metadata_tier=entry.data_tier,
        n_trajs_found=len(kept),
        n_expected=len(canonical_ids) if canonical_ids else 500,
        n_observed=len(observed_ids),
        n_no_gen_no_traj=n_no_gen_no_traj,
        n_missing_traj_has_patch=n_missing_traj_has_patch,
        n_duplicate_keys_dropped=len(dropped),
        n_fetch_errors=n_fetch_errors,
        parsed_tier_counts=parsed_tier_counts,
        field_coverage=field_coverage,
        n_quarantined=len(quarantine_records),
        quarantine_reasons=quarantine_reasons,
        total_n_calls=total_n_calls,
        total_api_calls_reported=total_api_calls_reported,
        n_calls_vs_api_calls_pct_diff=pct_diff,
        total_n_calls_unaccounted=total_n_calls_unaccounted,
        tier_matches_metadata_guess=tier_match,
    )


def _apply_provider_flags(calls: list) -> None:
    """cache_write_tokens absent -> structurally_absent (a real 0) ONLY when
    provider_flags.yaml has a VERIFIED row saying this model does not bill
    cache writes. Unverified / unknown provider -> stays 'unknown' (null).
    Lives here, not in parse.py, because it depends on an external curated
    file; parse.py never consults anything but the traj."""
    for c in calls:
        if c.cache_write_tokens is not None:
            continue
        billed = cache_write_billed(c.answering_model)
        if billed is False:
            c.cache_write_tokens = 0
            c.provenance["cache_write_tokens"] = "structurally_absent"


def _load_details_raw(entry: EntryMeta) -> dict:
    if not entry.has_details:
        return {}
    path = entry.path / "per_instance_details.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def print_report(report: ExtractReport) -> None:
    print(f"=== {report.entry} ===")
    print(f"  metadata tier guess: {report.metadata_tier}   parsed tier(s): {report.parsed_tier_counts}")
    print(f"  tier match: {report.tier_matches_metadata_guess}")
    print(
        f"  n_trajs_found={report.n_trajs_found}  n_expected={report.n_expected}  "
        f"n_observed={report.n_observed}  n_no_gen_no_traj={report.n_no_gen_no_traj}  "
        f"n_missing_traj_has_patch={report.n_missing_traj_has_patch}"
    )
    if report.n_duplicate_keys_dropped:
        print(f"  n_duplicate_keys_dropped={report.n_duplicate_keys_dropped}")
    if report.n_fetch_errors:
        print(f"  n_fetch_errors={report.n_fetch_errors}")
    print("  field coverage (measured / structurally_absent / known_zero / unknown):")
    print_coverage(report.field_coverage)
    # NOT sum(n_calls across fields) -- every field shares the same call
    # population, so that would silently 5x the denominator and understate
    # the quarantine rate against the 1% abort threshold.
    total_calls = next(iter(report.field_coverage.values()), {}).get("n_calls", 0) + report.n_quarantined
    qrate = (report.n_quarantined / total_calls * 100) if total_calls else 0.0
    print(f"  quarantine: {report.n_quarantined} rows ({qrate:.2f}% of call rows)  reasons={report.quarantine_reasons}")
    print(
        f"  n_calls total={report.total_n_calls}  api_calls_reported total={report.total_api_calls_reported}  "
        f"pct_diff={report.n_calls_vs_api_calls_pct_diff}"
    )
    print(f"  n_calls_unaccounted total={report.total_n_calls_unaccounted}  (api_calls_reported - n_calls, summed per instance)")
