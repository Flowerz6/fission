"""Seed-reliability ledger: report, backfill, and override from the CLI.

The ledger records, per seed (guide), how it has performed *at execution time*
across sessions, and applies the discard rule (retire after N attack-failures with
no success; bot-detections and harness errors are inconclusive and never count).

Usage::

    # recompute every seed's ledger from the trace history and print the table
    poetry run python scripts/seed_reliability.py

    # just print the current stored ledger without recomputing
    poetry run python scripts/seed_reliability.py --no-recompute

    # operator override: an ambiguous 'error' seed was reviewed and judged
    #   bot-blocked (keep it) or a genuine failure (retire it)
    poetry run python scripts/seed_reliability.py --set sheets_skill_leak=active
    poetry run python scripts/seed_reliability.py --set docs_language_override=retired
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select

from advtest_ext.core.reliability import (
    DEFAULT_DISCARD_N,
    recompute_seed_reliability,
    set_seed_status,
    summarize_seed_reliability,
)
from advtest_ext.storage import SeedReliability, make_engine, make_session_factory, session_scope
from advtest_ext.storage.models import Trace

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _all_seed_ids(session) -> list[str]:
    """Every guide id that has at least one trace, plus any already in the ledger."""
    from_traces = {gid for (gid,) in session.execute(select(Trace.behavior_id).distinct())}
    from_ledger = {gid for (gid,) in session.execute(select(SeedReliability.guide_id))}
    return sorted(from_traces | from_ledger)


def _print_table(rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        print("No seeds with trace history yet.")
        return
    header = ("seed", "tier", "sess", "win", "fail", "bot", "err", "status", "recommend")
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*(str(x) for x in r)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db-url",
        default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"),
        help="SQLAlchemy DB URL (default: $ADVTEST_DB_URL or sqlite:///./advtest.db)",
    )
    ap.add_argument(
        "--no-recompute",
        action="store_true",
        help="Print the stored ledger only; do not recompute from traces.",
    )
    ap.add_argument(
        "--discard-n",
        type=int,
        default=DEFAULT_DISCARD_N,
        help=f"Attack-failures-with-no-success before retiring a seed (default {DEFAULT_DISCARD_N}).",
    )
    ap.add_argument(
        "--set",
        metavar="GUIDE_ID=STATUS",
        action="append",
        default=[],
        help="Operator override: pin a seed to active|retired (repeatable).",
    )
    args = ap.parse_args()

    factory = make_session_factory(make_engine(args.db_url))

    if args.set:
        with session_scope(factory) as s:
            for item in args.set:
                guide_id, _, status = item.partition("=")
                guide_id, status = guide_id.strip(), status.strip()
                if not guide_id or status not in {"active", "retired"}:
                    raise SystemExit(f"--set expects GUIDE_ID=active|retired, got {item!r}")
                set_seed_status(s, guide_id, status)
                print(f"override: {guide_id} -> {status} (manual)")

    # Map guide id -> maturity tier from the corpus (discovery vs gold).
    from advtest_ext.guides.corpus import load_guides

    maturity = {g.id: g.maturity for g in load_guides(_REPO_ROOT / "guides")}

    rows: list[tuple[Any, ...]] = []
    with session_scope(factory) as s:
        for guide_id in _all_seed_ids(s):
            summary = (
                summarize_seed_reliability(s, guide_id, discard_n=args.discard_n)
                if args.no_recompute
                else recompute_seed_reliability(s, guide_id, discard_n=args.discard_n)
            )
            rows.append((
                guide_id, maturity.get(guide_id, "?"), summary.n_sessions, summary.n_success,
                summary.n_attack_failed, summary.n_bot_detected, summary.n_error,
                summary.status, summary.recommendation,
            ))

    _print_table(rows)
    retired = [r[0] for r in rows if r[7] == "retired"]
    if retired:
        print(f"\nretired ({len(retired)}): {', '.join(retired)}")
    mature = [r[0] for r in rows if r[8] == "mature" and r[1] == "discovery"]
    if mature:
        print(f"\ndiscovery seeds reproduced enough to mature to gold ({len(mature)}): "
              f"{', '.join(mature)}")


if __name__ == "__main__":
    main()
