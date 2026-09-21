"""python -m analysis.token_waste.cli {list|claim1|extract|price|metrics|check|all}

Only `list` is implemented so far (milestone 1). The rest are milestones 2-6
per plan.md and will raise NotImplementedError until built.
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import claim1
from . import metrics
from .entries import select_entries
from .extract import extract_entry, print_report
from . import pricing

_LIST_COLUMNS = (
    "entry",
    "mini_version",
    "data_tier",
    "has_details",
    "traj_source",
    "reported_cost_usd",
    "reported_resolved_pct",
    "requested_model",
    "reasoning_effort",
)


def _cmd_list(args: argparse.Namespace) -> int:
    entries = select_entries()
    if not entries:
        print("no mini-SWE-agent entries found under evaluation/verified/", file=sys.stderr)
        return 1

    rows = [
        {
            "entry": e.entry,
            "mini_version": e.mini_version or "",
            "data_tier": e.data_tier,
            "has_details": e.has_details,
            "traj_source": e.traj_source,
            "reported_cost_usd": "" if e.reported_cost_usd is None else round(e.reported_cost_usd, 2),
            "reported_resolved_pct": "" if e.reported_resolved_pct is None else e.reported_resolved_pct,
            "requested_model": e.requested_model or "",
            "reasoning_effort": e.reasoning_effort or "",
        }
        for e in entries
    ]

    if args.format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=_LIST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    else:
        widths = {
            c: max(len(c), max(len(str(r[c])) for r in rows)) for c in _LIST_COLUMNS
        }
        header = "  ".join(c.ljust(widths[c]) for c in _LIST_COLUMNS)
        print(header)
        print("  ".join("-" * widths[c] for c in _LIST_COLUMNS))
        for r in rows:
            print("  ".join(str(r[c]).ljust(widths[c]) for c in _LIST_COLUMNS))

    n_details = sum(1 for e in entries if e.has_details)
    n_by_tier: dict[str, int] = {}
    for e in entries:
        n_by_tier[e.data_tier] = n_by_tier.get(e.data_tier, 0) + 1
    print(
        f"\n{len(entries)} mini-SWE-agent entries "
        f"({n_details} with per_instance_details.json). "
        f"Tier guess: {n_by_tier}",
        file=sys.stderr,
    )
    return 0


def _cmd_claim1(args: argparse.Namespace) -> int:
    written = claim1.run()
    for name, path in sorted(written.items()):
        print(f"wrote {path}", file=sys.stderr)
    try:
        summary_path = written["claim1_summary.csv"]
        with open(summary_path, newline="") as f:
            print(f.read())
    except (KeyError, OSError):
        pass
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    entries = select_entries()
    if args.entry:
        wanted = set(args.entry)
        entries = [e for e in entries if e.entry in wanted]
        missing = wanted - {e.entry for e in entries}
        for name in missing:
            print(f"no such entry: {name}", file=sys.stderr)
        if not entries:
            return 1
    elif not args.all:
        print("pass --entry NAME [--entry NAME ...] or --all", file=sys.stderr)
        return 2

    for e in entries:
        report = extract_entry(e, force=args.force)
        if report is None:
            print(f"=== {e.entry} === skipped (already extracted; use --force to redo)")
            continue
        print_report(report)
    return 0


def _cmd_price(args: argparse.Namespace) -> int:
    pricing.run()
    return 0


def _cmd_metrics(args: argparse.Namespace) -> int:
    written = metrics.run()
    for name, path in sorted(written.items()):
        print(f"wrote {path}", file=sys.stderr)
    try:
        with open(written["m6_summary.csv"], newline="") as f:
            print(f.read())
    except (KeyError, OSError):
        pass
    return 0


def _not_implemented(name: str):
    def _cmd(args: argparse.Namespace) -> int:
        print(f"'{name}' is not implemented yet (see plan.md milestones).", file=sys.stderr)
        return 2

    return _cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m analysis.token_waste.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List in-scope mini-SWE-agent entries (no network).")
    p_list.add_argument("--format", choices=("table", "csv"), default="table")
    p_list.set_defaults(func=_cmd_list)

    p_claim1 = sub.add_parser("claim1", help="Build Claim 1 (economic) CSVs. No network.")
    p_claim1.set_defaults(func=_cmd_claim1)

    p_extract = sub.add_parser("extract", help="Fetch+parse trajs from S3/GitHub. Network.")
    p_extract.add_argument("--entry", action="append", help="Entry name(s) to extract; repeatable.")
    p_extract.add_argument("--all", action="store_true", help="Extract every in-scope entry.")
    p_extract.add_argument("--force", action="store_true", help="Redo entries with existing output.")
    p_extract.set_defaults(func=_cmd_extract)

    p_price = sub.add_parser("price", help="Dual-run pricing (litellm-pinned vs curated). Network for pins.")
    p_price.set_defaults(func=_cmd_price)

    p_metrics = sub.add_parser("metrics", help="Build M6 (Claim 2 / cost-asymmetry) CSVs. No network.")
    p_metrics.set_defaults(func=_cmd_metrics)

    for name in ("check", "all"):
        p = sub.add_parser(name, help="(not yet implemented)")
        p.set_defaults(func=_not_implemented(name))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
