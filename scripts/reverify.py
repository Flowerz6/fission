#!/usr/bin/env python
"""Re-verify oracle-only ("suspect") wins by reproduction across fresh sessions.

Workflow: run a guide once (the original run), then run it a few more times as
fresh sessions/accounts (the re-runs). This finds the suspect wins in the original
(success, no hard signal), saves their evidence, and keeps only the ones that
reproduce in the re-runs.

    poetry run python scripts/reverify.py --guide sheets_skill_leak \
        --original-runs sk-1 --rerun-runs sk-2,sk-3 --mode content --promote

Modes:
  content  — leaks: the extracted artifact must recur across re-runs (default).
  winagain — other attacks: the re-runs must win again (feed a stricter judge's
             verdicts; here a re-run counts as a win if it has any success trace).

--promote banks each reproduced suspect as a verified harvested seed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _artifact_patterns(guide) -> list[str]:  # type: ignore[no-untyped-def]
    patterns: list[str] = []
    for sig in guide.success.signals:
        pat = sig.get("pattern")
        tok = sig.get("token") or sig.get("canary")
        if pat:
            patterns.append(str(pat))
        elif tok:
            patterns.append(re.escape(str(tok)))
    return patterns


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--guide", required=True)
    ap.add_argument("--original-runs", required=True, help="run id(s) that produced the suspects")
    ap.add_argument("--rerun-runs", required=True, help="fresh-session run id(s) to check against")
    ap.add_argument("--mode", choices=["content", "winagain"], default="content")
    ap.add_argument("--min-consistency", type=float, default=0.5)
    ap.add_argument("--min-winrate", type=float, default=0.5)
    ap.add_argument("--promote", action="store_true", help="bank reproduced suspects as seeds")
    ap.add_argument("--db-url", default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from advtest_ext.guides.corpus import load_guides
    from advtest_ext.guides.reverify import (
        find_suspect_wins,
        promote_reproduced,
        reverify_content,
        reverify_winagain,
    )
    from advtest_ext.storage import (
        list_traces_for_run,
        make_engine,
        make_session_factory,
        session_scope,
    )

    guide = next((g for g in load_guides(_REPO_ROOT / "guides") if g.id == args.guide), None)
    if guide is None:
        sys.exit(f"guide {args.guide!r} not found")
    original = [r.strip() for r in args.original_runs.split(",") if r.strip()]
    reruns = [r.strip() for r in args.rerun_runs.split(",") if r.strip()]
    factory = make_session_factory(make_engine(args.db_url))

    suspects = find_suspect_wins(factory, original, artifact_patterns=_artifact_patterns(guide))
    if not suspects:
        print("no suspect (oracle-only) wins in the original run(s).")
        return

    # Gather the re-run evidence once.
    rerun_artifacts: list[str] = []
    rerun_successes: list[bool] = []
    with session_scope(factory) as s:
        for run_id in reruns:
            traces = list_traces_for_run(s, run_id)
            wins = [t for t in traces if (t.verdict or "").lower() == "success"]
            rerun_artifacts.append("\n".join(t.raw_response or "" for t in wins))
            rerun_successes.append(bool(wins))

    results = []
    for sus in suspects:
        if args.mode == "content":
            res = reverify_content(sus, rerun_artifacts, min_consistency=args.min_consistency)
        else:
            res = reverify_winagain(sus, rerun_successes, min_winrate=args.min_winrate)
        promoted = (
            promote_reproduced(factory, sus, res, guide_id=guide.id) if args.promote else False
        )
        results.append((sus, res, promoted))

    if args.json:
        print(
            json.dumps(
                [
                    {**res.to_dict(), "suspect": sus.to_dict(), "promoted": promoted}
                    for sus, res, promoted in results
                ],
                indent=2,
            )
        )
        return

    print(f"\nRe-verification ({args.mode}) for {guide.id}: {len(suspects)} suspect(s)\n")
    for sus, res, promoted in results:
        mark = "REPRODUCED" if res.reproduced else "not reproduced"
        tag = "  -> promoted" if promoted else ""
        print(f"  [{mark}] trace {sus.trace_id}  score {res.score:.2f}{tag}")
        print(f"      {res.reasoning}")
        if sus.artifact_tokens:
            print(f"      saved evidence tokens: {', '.join(sus.artifact_tokens)}")
    n_repro = sum(1 for _, r, _ in results if r.reproduced)
    print(f"\n  {n_repro}/{len(results)} suspects reproduced.\n")


if __name__ == "__main__":
    main()
