#!/usr/bin/env python
"""Score a guide's mutation cells by how they actually performed in real runs.

The "keep the winners" gate: run a guide several times, then this reports which
cells earned verified successes (keep), which were tried repeatedly with no
success (prune), and which the oracle liked but no signal confirmed (suspect).
See ``advtest_ext/guides/cell_performance.py``.

Usage:
    poetry run python scripts/cell_performance.py --guide sheets_skill_leak \
        --runs sk-1,sk-2,sk-3

    # also strip the proven-underperformer cells from the guide JSON
    poetry run python scripts/cell_performance.py --guide sheets_skill_leak \
        --runs sk-1,sk-2,sk-3 --prune
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--guide", required=True, help="guide id (file guides/<id>.json)")
    ap.add_argument("--runs", required=True, help="comma-separated run ids of that guide")
    ap.add_argument(
        "--min-attempts",
        type=int,
        default=3,
        help="a cell needs this many tries with no success before it's a prune candidate",
    )
    ap.add_argument(
        "--prune", action="store_true", help="remove underperformer cells from the guide JSON"
    )
    ap.add_argument("--db-url", default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"))
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args()

    from advtest_ext.guides.cell_performance import (
        cell_performance_for_runs,
        prune_from_guide_dict,
    )
    from advtest_ext.guides.corpus import load_guides
    from advtest_ext.storage import make_engine, make_session_factory

    guide_path = _REPO_ROOT / "guides" / f"{args.guide}.json"
    guide = next((g for g in load_guides(_REPO_ROOT / "guides") if g.id == args.guide), None)
    if guide is None:
        sys.exit(f"guide {args.guide!r} not found")
    run_ids = [r.strip() for r in args.runs.split(",") if r.strip()]

    factory = make_session_factory(make_engine(args.db_url))
    report = cell_performance_for_runs(
        factory, guide, run_ids, min_attempts_to_prune=args.min_attempts
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        by_op: dict[str, list] = {}
        for s in report.stats:
            by_op.setdefault(s.operator, []).append(s)
        _mark = {
            "performer": "KEEP  ",
            "unverified": "SUSPECT",
            "underperformer": "PRUNE ",
            "inconclusive": "?     ",
            "untried": "-     ",
        }
        print(f"\nCell performance for {report.guide_id} over {report.n_runs} run(s):\n")
        for operator, stats in by_op.items():
            print(f"  {operator}")
            for s in stats:
                print(
                    f"    [{_mark[s.status]}] {s.value}  "
                    f"(tried {s.tried}, verified {s.wins_verified}, oracle {s.wins_success})"
                )
        print(
            f"\n  keep: {len(report.promote)}   prune: {len(report.prune)}   "
            f"of {len(report.stats)} cells\n"
        )

    if args.prune:
        if not report.prune:
            print("nothing to prune.")
            return
        raw = json.loads(guide_path.read_text(encoding="utf-8"))
        prune_from_guide_dict(raw, report)
        guide_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"pruned {len(report.prune)} underperformer cell(s) from {guide_path}")


if __name__ == "__main__":
    main()
