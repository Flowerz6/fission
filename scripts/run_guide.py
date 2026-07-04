"""Run an attack Guide end-to-end against a live target (slice 3 of Guides).

This is the manual driver for :func:`advtest_ext.guides.run.run_guide`: it loads
a normalized Guide from the ``guides/`` corpus (or an explicit file), maps it to
a configured ``tap_crescendo`` run, and executes it against a real Workspace
Gemini target. It drives a real browser + real LLMs, so run it yourself with
your own credentials — it is not part of the test suite.

Prerequisites (environment):
  export ANTHROPIC_API_KEY=...        # the CLAUDE_ATTACKER role in the provider YAML
  export GEMINI_API_KEY=...           # the GEMINI_TARGET role (the target)
  # A logged-in Playwright profile for the Workspace target (ui_mode), as usual.

Usage:
  # By id from the guides/ corpus:
  poetry run python scripts/run_guide.py --guide sheets_skill_leak

  # From an explicit file, overriding the target adapter + tree depth:
  poetry run python scripts/run_guide.py \\
      --guide-file guides/docs_language_override.json \\
      --target-name google_docs_multiturn \\
      --max-depth 2 --output-dir ./out

  # List what's in the corpus:
  poetry run python scripts/run_guide.py --list

Notes:
  * tap_crescendo is multi-turn, so the target must be multi-turn-capable. The
    guide's app picks a default adapter (google_docs -> google_docs_multiturn);
    override with --target-name when needed.
  * --output-dir writes traces + a replay bundle per finding; omit for a dry log.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from advtest_ext.guides import Guide, load_guide, load_guides
from advtest_ext.guides.run import run_guide

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUIDES_DIR = _REPO_ROOT / "guides"


def _find_guide(guide_id: str) -> Guide:
    """Load a guide by id: prefer ``guides/<id>.json``, else match on the
    ``id`` field across the corpus."""
    direct = _GUIDES_DIR / f"{guide_id}.json"
    if direct.exists():
        return load_guide(direct)
    for guide in load_guides(_GUIDES_DIR):
        if guide.id == guide_id:
            return guide
    raise SystemExit(
        f"no guide {guide_id!r} in {_GUIDES_DIR} (use --list to see the corpus, "
        "or --guide-file for a path outside it)"
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--guide", help="guide id in the guides/ corpus (e.g. sheets_skill_leak)")
    src.add_argument("--guide-file", type=Path, help="path to a guide JSON file")
    p.add_argument("--list", action="store_true", help="list the guides/ corpus and exit")
    p.add_argument(
        "--provider-config",
        type=Path,
        default=_REPO_ROOT / "configs" / "dev.yaml",
        help="providers YAML with CLAUDE_ATTACKER + GEMINI_TARGET roles (default: configs/dev.yaml)",
    )
    p.add_argument("--target-name", help="override the target adapter (e.g. google_docs_multiturn)")
    p.add_argument("--output-dir", type=Path, help="write traces + replay bundles here")
    p.add_argument("--max-depth", type=int, help="tap_crescendo tree depth override")
    p.add_argument("--branching-factor", type=int, help="tap_crescendo branching override")
    p.add_argument("--node-max-turns", type=int, help="max turns per Crescendo node")
    p.add_argument(
        "--no-confirm",
        action="store_true",
        help="disable the LLM confirm-judge (heuristic-only convergence)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list:
        for guide in load_guides(_GUIDES_DIR):
            print(
                f"{guide.id:40s} {guide.target.app}/{guide.target.surface}  {guide.objective[:60]}"
            )
        return

    if args.guide_file:
        guide = load_guide(args.guide_file)
    elif args.guide:
        guide = _find_guide(args.guide)
    else:
        raise SystemExit("pass --guide <id>, --guide-file <path>, or --list")

    # Only the overrides the operator actually set (so factory defaults hold).
    overrides = {
        k: v
        for k, v in {
            "max_depth": args.max_depth,
            "branching_factor": args.branching_factor,
            "node_max_turns": args.node_max_turns,
        }.items()
        if v is not None
    }

    print(f"running guide {guide.id!r} against {args.target_name or guide.target.app} …")
    result = asyncio.run(
        run_guide(
            guide,
            provider_config=args.provider_config,
            target_name=args.target_name,
            confirm_with_llm_judge=not args.no_confirm,
            output_dir=args.output_dir,
            **overrides,
        )
    )

    stats = result.summary_stats
    print(f"\nrun {result.run_id}: {len(result.traces)} traces")
    print(f"summary: {stats.model_dump() if hasattr(stats, 'model_dump') else stats}")
    if args.output_dir:
        print(f"traces + replay bundles under {args.output_dir}")


if __name__ == "__main__":
    main()
