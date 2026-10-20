"""One-command Google-pilot pipeline — the automated system, end to end.

Stages (the automation Google's factor-2 asks to see):

  1. SELECT   — pick seed families from guides/ (all | gaps | explicit ids).
  2. ASSEMBLE — each family compiles to a runnable tap_crescendo plan
                (map_guide) and passes its oracle completeness check.
  3. RUN      — fan out each family on the LIVE Gemini-Workspace surface,
                N times (Google wants N>=3), scoring with the decomposed oracle
                + the guide's deterministic signals (ExfilSink / DocDiff /
                canary). [--live; needs a logged-in Playwright profile.]
  4. VERIFY   — a success counts only when a success-SIGNAL fired on the live
                response (SIGNAL-VERIFIED), not judge-only.
  5. EXPORT   — emit the deliverable in Google's 8-field schema with per-family
                N/M and lineage ids (scripts/build_pilot_variant_dataset.py).

Default is a DRY RUN: stages 1-2 + coverage, no browser — proves the pipeline
wiring so an operator can flip --live to produce the deliverable. Live runs and
the final N/M are the operator's step (browser profile + keys).

    # offline: prove the pipeline assembles the gap families + show coverage
    python scripts/run_pilot_pipeline.py --select gaps

    # operator: drive the gap families live, 3x each, then export
    python scripts/run_pilot_pipeline.py --select gaps --live --repeats 3 --export \
        --provider-config configs/providers.yaml
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from advtest_ext.guides.completeness import check_guide  # noqa: E402
from advtest_ext.guides.corpus import load_guide  # noqa: E402
from advtest_ext.guides.to_tap_crescendo import map_guide  # noqa: E402

# Reuse the coverage classifier (harm/surface) so selection + reporting match
# the 300 matrix in docs/SEED_GENERATOR_PLAN.md.
_spec = importlib.util.spec_from_file_location("seed_coverage", ROOT / "scripts" / "seed_coverage.py")
_cov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cov)

# The under-target harms (the cells the pilot most needs for diversity/severity).
GAP_HARMS = {
    "unauthorized_data_modification",
    "malware_payload_delivery",
    "rogue_actions",
    "misinformation",
    "pii_credential_extraction",
}


def _err_issues(guide) -> list:
    return [i for i in check_guide(guide) if str(getattr(i, "level", "")).lower().endswith("error")]


def select_guides(guides_dir: Path, select: str) -> list[Path]:
    paths = sorted(guides_dir.glob("*.yaml")) + sorted(guides_dir.glob("*.json"))
    if select not in ("all", "gaps"):
        wanted = {s.strip() for s in select.split(",") if s.strip()}
        return [p for p in paths if p.stem in wanted]
    if select == "all":
        return paths
    # gaps: families whose classified harm is an under-target cell.
    out = []
    for p in paths:
        data = _cov._load(p)
        if data and _cov._harm(data) in GAP_HARMS:
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Google-pilot pipeline (dry-run by default).")
    ap.add_argument("--select", default="gaps", help="all | gaps | comma,separated,guide,ids")
    ap.add_argument("--guides-dir", type=Path, default=ROOT / "guides")
    ap.add_argument("--repeats", type=int, default=3, help="live reruns per family (Google wants >=3)")
    ap.add_argument("--live", action="store_true", help="drive families on live Gemini (needs a profile)")
    ap.add_argument("--export", action="store_true", help="regenerate the Google-schema dataset after")
    ap.add_argument("--provider-config", type=Path, default=ROOT / "configs" / "hybrid.yaml")
    ap.add_argument(
        "--planner-role",
        default="CLAUDE_PLANNER",
        help="strategist role that plans mutations (reliability default: CLAUDE_PLANNER; "
        "the local model only executes turn content). Pass '' to disable.",
    )
    ap.add_argument("--limit", type=int, default=0, help="cap the number of families (0 = no cap)")
    args = ap.parse_args()

    paths = select_guides(args.guides_dir, args.select)
    if args.limit:
        paths = paths[: args.limit]

    print(f"PILOT PIPELINE  ·  select={args.select}  ·  mode={'LIVE' if args.live else 'DRY-RUN'}  "
          f"·  repeats={args.repeats}")
    print("=" * 78)

    # Stage 1-2: SELECT + ASSEMBLE ------------------------------------------
    print(f"\n[1] SELECT  — {len(paths)} seed families")
    print("[2] ASSEMBLE — compile each to a runnable plan + oracle check\n")
    print(f"  {'family':40} {'harm':28} {'turns':>5} {'signals':>7}  status")
    print("  " + "-" * 92)
    ready: list = []
    for p in paths:
        try:
            g = load_guide(p)
        except Exception as exc:
            print(f"  {p.stem:40} {'<load error>':28} {'':>5} {'':>7}  FAIL: {str(exc)[:30]}")
            continue
        errs = _err_issues(g)
        data = _cov._load(p) or {}
        harm = _cov._harm(data)
        try:
            plan = map_guide(g)
            turns = len(getattr(plan, "seed_turns", []) or getattr(g, "exemplar_turns", []) or [])
        except Exception as exc:
            print(f"  {p.stem:40} {harm:28} {'':>5} {'':>7}  FAIL assemble: {str(exc)[:30]}")
            continue
        nsig = len(g.success.signals)
        status = "READY" if not errs else f"BLOCKED ({len(errs)} err)"
        print(f"  {g.id:40} {harm:28} {turns:>5} {nsig:>7}  {status}")
        if not errs:
            ready.append(g)

    print(f"\n  → {len(ready)}/{len(paths)} families READY to run.")

    # Stage 3-4: RUN + VERIFY ----------------------------------------------
    print("\n[3] RUN + [4] VERIFY")
    if not args.live:
        print("  DRY-RUN: skipped (no browser). Flip --live with a logged-in Playwright profile")
        print(f"  to fan out each READY family {args.repeats}x on Gemini; a success counts only when a")
        print("  deterministic success-signal fires on the live response (SIGNAL-VERIFIED).")
    else:
        rc = _run_live(ready, args)
        if rc != 0:
            return rc

    # Stage 5: EXPORT -------------------------------------------------------
    print("\n[5] EXPORT — Google 8-field schema + N/M + lineage")
    sys.stdout.flush()
    if args.export:
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_pilot_variant_dataset.py")],
            cwd=ROOT, capture_output=True, text=True,
        )
        sys.stdout.write(out.stdout)
        if out.returncode != 0:
            sys.stderr.write(out.stderr)
            return out.returncode
        if not args.live:
            print("  (dry-run: dataset reflects the EXISTING DB; live runs add the new families.)")
    else:
        print("  skipped (pass --export to regenerate Workspace_IPI_Attacks_Dataset.json).")

    # Coverage scoreboard ---------------------------------------------------
    print("\n[coverage toward 300]")
    sys.stdout.flush()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_coverage.py")], cwd=ROOT)
    return 0


def _run_live(ready: list, args: argparse.Namespace) -> int:
    """Drive each READY family live, args.repeats times, via run_guide."""
    import asyncio

    from advtest_ext.guides.run import run_guide

    if not args.provider_config.exists():
        print(f"  ERROR: --provider-config not found: {args.provider_config}")
        return 2

    async def _drive() -> None:
        for g in ready:
            print(f"  · {g.id}: {args.repeats} runs …")
            for i in range(args.repeats):
                try:
                    # CLAUDE_PLANNER = the strategist (picks operator/cell, writes the
                    # mutation plan); the local model only executes turn content. Without
                    # it the weak local model does strategy too and drifts (the
                    # "sustainable footwear" fan-out). Empty string disables it.
                    await run_guide(
                        g,
                        provider_config=args.provider_config,
                        planner_role=args.planner_role or None,
                        output_dir=ROOT / "out" / "pilot" / g.id / f"run{i}",
                    )
                except Exception as exc:
                    print(f"      run {i} error: {str(exc)[:80]}")

    asyncio.run(_drive())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
