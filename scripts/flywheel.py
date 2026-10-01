#!/usr/bin/env python
"""Run the whole flywheel for one guide in a single command.

Chains the four stages end to end:

  1. (optional) GENERATE new mutation cells for the guide with an LLM.
  2. RUN the guide K times as fresh sessions (live browser).
     Signal-verified wins are harvested per node automatically as proven variants.
  3. GATE: score every cell by how it performed and optionally prune the duds.
  4. REVERIFY the oracle-only ("suspect") wins across the fresh runs and promote
     the ones that reproduce to verified variants.

Then it prints a summary: variants banked, cell keep/prune, suspects reproduced.

    poetry run python scripts/flywheel.py --guide sheets_skill_leak --runs 3

Live path: stages 1-2 need ANTHROPIC_API_KEY (and, for the runs, the signed-in
Chrome profile) in the environment. Run with `set -a; . ./.env; set +a` first.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _launch_run(guide_id: str, branching_factor: int, db_url: str) -> str | None:
    """Launch one fresh run of the guide via run_guide.py (same path the dashboard
    uses). Returns the run id, or None if it failed."""
    run_id = f"fw-{uuid.uuid4().hex[:12]}"
    print(f"  running {run_id} …", flush=True)
    argv = [
        sys.executable,
        str(_REPO_ROOT / "scripts" / "run_guide.py"),
        "--guide",
        guide_id,
        "--browser-mint",
        "--branching-factor",
        str(branching_factor),
        "--run-id",
        run_id,
        "--db-url",
        db_url,
    ]
    proc = subprocess.run(argv, cwd=str(_REPO_ROOT), env=os.environ.copy(), check=False)
    if proc.returncode != 0:
        print(f"  run {run_id} failed (exit {proc.returncode}) — skipping", flush=True)
        return None
    return run_id


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
    ap.add_argument("--runs", type=int, default=3, help="fresh runs of the guide (>=2 recommended)")
    ap.add_argument("--branching-factor", type=int, default=1)
    ap.add_argument(
        "--generate-cells",
        type=int,
        default=0,
        metavar="N",
        help="first, generate + apply N new cells per operator (needs a compliant proposer)",
    )
    ap.add_argument("--proposer-role", default="ATTACKER_MODEL", help="role for cell generation")
    ap.add_argument(
        "--provider-config",
        default=os.environ.get("ADVTEST_PROVIDER_CONFIG", str(_REPO_ROOT / "configs" / "dev.yaml")),
    )
    ap.add_argument("--prune", action="store_true", help="prune proven-underperformer cells")
    ap.add_argument("--no-reverify", action="store_true", help="skip re-verifying oracle-only wins")
    ap.add_argument("--min-consistency", type=float, default=0.5)
    ap.add_argument("--db-url", default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"))
    args = ap.parse_args()

    import asyncio
    import json

    from advtest_ext.guides.cell_performance import cell_performance_for_runs, prune_from_guide_dict
    from advtest_ext.guides.corpus import load_guides
    from advtest_ext.guides.reverify import (
        find_suspect_wins,
        promote_reproduced,
        reverify_content,
    )
    from advtest_ext.storage import (
        list_guide_variants,
        make_engine,
        make_session_factory,
        session_scope,
    )

    guide = next((g for g in load_guides(_REPO_ROOT / "guides") if g.id == args.guide), None)
    if guide is None:
        sys.exit(f"guide {args.guide!r} not found")
    guide_path = _REPO_ROOT / "guides" / f"{args.guide}.json"
    factory = make_session_factory(make_engine(args.db_url))

    # 1. GENERATE (optional) --------------------------------------------------
    if args.generate_cells:
        print(f"\n[1/4] generating {args.generate_cells} cells/operator …")
        from advtest_ext.guides.cell_generator import apply_to_guide_dict, generate
        from advtest_ext.providers.base import ChatMessage
        from advtest_ext.providers.factory import load_provider_from_yaml

        provider = load_provider_from_yaml(args.provider_config, args.proposer_role)

        async def _complete(prompt: str) -> str:
            resp = await provider.complete([ChatMessage(role="user", content=prompt)])
            return resp.text

        proposals = asyncio.run(generate(_complete, guide, n_per_operator=args.generate_cells))
        raw = json.loads(guide_path.read_text(encoding="utf-8"))
        apply_to_guide_dict(raw, proposals)
        guide_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        added = sum(len(p.accepted) for p in proposals)
        print(f"      added {added} new cells; reloading guide")
        guide = next(g for g in load_guides(_REPO_ROOT / "guides") if g.id == args.guide)
    else:
        print("\n[1/4] generate: skipped")

    # 2. RUN (live) -----------------------------------------------------------
    print(f"\n[2/4] running {args.guide} x{args.runs} (harvesting proven variants per node) …")
    run_ids = [
        rid
        for _ in range(args.runs)
        if (rid := _launch_run(args.guide, args.branching_factor, args.db_url))
    ]
    if not run_ids:
        sys.exit("no runs completed — nothing to gate or re-verify")

    # 3. GATE -----------------------------------------------------------------
    print("\n[3/4] scoring cells …")
    report = cell_performance_for_runs(factory, guide, run_ids)
    print(
        f"      keep {len(report.promote)}   prune {len(report.prune)}   of {len(report.stats)} cells"
    )
    if args.prune and report.prune:
        raw = json.loads(guide_path.read_text(encoding="utf-8"))
        prune_from_guide_dict(raw, report)
        guide_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"      pruned {len(report.prune)} underperformer cell(s)")

    # 4. REVERIFY -------------------------------------------------------------
    reproduced = 0
    if not args.no_reverify and len(run_ids) >= 2:
        print("\n[4/4] re-verifying oracle-only wins across the fresh runs …")
        suspects = find_suspect_wins(
            factory, run_ids[:1], artifact_patterns=_artifact_patterns(guide)
        )
        with session_scope(factory) as s:
            from advtest_ext.storage import list_traces_for_run

            rerun_artifacts = [
                "\n".join(
                    t.raw_response or ""
                    for t in list_traces_for_run(s, rid, limit=100_000)
                    if (t.verdict or "").lower() == "success"
                )
                for rid in run_ids[1:]
            ]
        for sus in suspects:
            res = reverify_content(sus, rerun_artifacts, min_consistency=args.min_consistency)
            if promote_reproduced(factory, sus, res, guide_id=guide.id):
                reproduced += 1
        print(f"      {reproduced}/{len(suspects)} suspect(s) reproduced and promoted")
    else:
        print("\n[4/4] reverify: skipped (need >= 2 runs)")

    # Summary -----------------------------------------------------------------
    with session_scope(factory) as s:
        variants = list_guide_variants(s, guide_id=guide.id)
    print(
        f"\nDONE. {len(run_ids)} run(s). guide '{guide.id}' now has {len(variants)} proven "
        f"variant(s) ({reproduced} newly reproduced). See /variants on the dashboard.\n"
    )


if __name__ == "__main__":
    main()
