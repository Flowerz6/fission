#!/usr/bin/env python
"""Authenticity check for self-extraction attacks (skill/schema/system-prompt leaks).

Given several runs of the SAME self-extraction guide (ideally across different
accounts), decide whether the "leaked" internals are REAL or a confident
HALLUCINATION, using cross-run consistency plus the fake-module negative control.
See ``advtest_ext/core/authenticity.py`` for the method.

Usage:
    # derive the internal-artifact patterns from the guide's own success signals
    poetry run python scripts/check_authenticity.py \
        --guide sheets_skill_leak \
        --control-marker cell_encryption \
        --runs run-aaa,run-bbb,run-ccc

    # or pass explicit regex patterns instead of --guide
    poetry run python scripts/check_authenticity.py \
        --patterns 'get_skills|execute_plan' --patterns 'EXECUTION_PLAN_STEP_STATUS' \
        --runs run-aaa,run-bbb
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _patterns_from_guide(guide_id: str) -> tuple[list[str], str | None]:
    """Return (artifact_patterns, control_marker_hint) derived from a guide.

    Patterns come from the guide's L0 success signals (regex_match ``pattern`` or
    token_appears ``token``). The control marker is best passed explicitly, but we
    hint one from a signal that mentions a slash-path if present.
    """
    from advtest_ext.guides.corpus import load_guides

    guides = load_guides(_REPO_ROOT / "guides")
    guide = next((g for g in guides if g.id == guide_id), None)
    if guide is None:
        sys.exit(f"guide {guide_id!r} not found under guides/")
    patterns: list[str] = []
    for sig in guide.success.signals:
        pat = sig.get("pattern")
        tok = sig.get("token") or sig.get("canary")
        if pat:
            patterns.append(str(pat))
        elif tok:
            patterns.append(re.escape(str(tok)))
    if not patterns:
        sys.exit(f"guide {guide_id!r} has no pattern/token success signals to key on")
    return patterns, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", required=True, help="comma-separated run ids of the same guide")
    ap.add_argument("--guide", help="guide id to derive internal-artifact patterns from")
    ap.add_argument(
        "--patterns",
        action="append",
        default=[],
        help="explicit regex for an internal artifact (repeatable); overrides --guide",
    )
    ap.add_argument(
        "--control-marker",
        help="substring identifying the fake-module probe turn (e.g. cell_encryption)",
    )
    ap.add_argument("--k", type=int, default=5, help="shingle size in words (default 5)")
    ap.add_argument(
        "--min-consistency",
        type=float,
        default=0.5,
        help="consistency threshold for a 'real' verdict (default 0.5)",
    )
    ap.add_argument("--db-url", default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"))
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args()

    from advtest_ext.core.authenticity import authenticity_report_for_runs
    from advtest_ext.storage import make_engine, make_session_factory

    run_ids = [r.strip() for r in args.runs.split(",") if r.strip()]
    if len(run_ids) < 2:
        print("warning: authenticity needs >= 2 independent runs to be meaningful", file=sys.stderr)

    patterns = list(args.patterns)
    if not patterns and args.guide:
        patterns, _ = _patterns_from_guide(args.guide)
    if not patterns:
        sys.exit("pass --patterns ... or --guide <id> to supply internal-artifact patterns")

    factory = make_session_factory(make_engine(args.db_url))
    report = authenticity_report_for_runs(
        factory,
        run_ids,
        artifact_patterns=patterns,
        control_marker=args.control_marker,
        k=args.k,
        min_consistency=args.min_consistency,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return

    verdict_label = {
        "real": "REAL — internals recur across runs; likely a genuine leak",
        "suspect": "SUSPECT — likely hallucinated, do not report as a finding",
        "inconclusive": "INCONCLUSIVE — insufficient evidence",
    }[report.verdict]
    print(f"\nAuthenticity: {verdict_label}")
    print(f"  runs analysed     : {report.n_runs}")
    print(f"  consistency       : {report.consistency:.2f}  (>= {args.min_consistency} => real)")
    print(f"  negative control  : {report.control}")
    if report.stable_artifacts:
        print(f"  stable artifacts  : {', '.join(report.stable_artifacts)}")
    if report.volatile_artifacts:
        print(f"  volatile (drift)  : {', '.join(report.volatile_artifacts)}")
    print(f"  why               : {report.reasoning}\n")


if __name__ == "__main__":
    main()
