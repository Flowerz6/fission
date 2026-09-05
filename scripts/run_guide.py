"""Run an attack Guide end-to-end against a live target (slice 3 of Guides).

This is the manual driver for :func:`advtest_ext.guides.run.run_guide`: it loads
a normalized Guide from the ``guides/`` corpus (or an explicit file), maps it to
a configured ``tap_crescendo`` run, and drives it against the **real Gemini
Workspace side panel through a logged-in Playwright browser** — the multi-turn
UI targets ``GeminiSheetsMultiTurnTarget`` / ``GeminiDocsMultiTurnTarget``. The
model is reached through its product UI, never an API: there is **no
``GEMINI_API_KEY``** in this path (the side panel *is* the model). Run it
yourself with your own account — it drives a real browser + real LLMs and is not
part of the test suite.

Prerequisites (environment):
  export ANTHROPIC_API_KEY=...              # CLAUDE_ATTACKER + the confirm judge (our models)
  export ADVTEST_PLAYWRIGHT_PROFILE_DIR=... # a Chrome profile logged into the Workspace account
  # NO GEMINI_API_KEY — the target is the Gemini side panel in the browser.

Usage:
  # Bikash's Sheets skill-leak, driven through the Sheets side-panel UI:
  poetry run python scripts/run_guide.py --guide sheets_skill_leak

  # Mint a fresh blank host Sheet/Doc through the browser (no Docs/Sheets API):
  poetry run python scripts/run_guide.py --guide sheets_skill_leak --browser-mint

  # Ground each tree node on a pre-existing payload doc you prepared:
  poetry run python scripts/run_guide.py --guide sheets_skill_leak --doc-id <id>

  # List what's in the corpus:
  poetry run python scripts/run_guide.py --list

Notes:
  * tap_crescendo is multi-turn, so the target holds ONE side-panel conversation
    across turns. The guide's app picks the default UI target (google_sheets ->
    google_sheets_multiturn, google_docs -> google_docs_multiturn); override with
    --target-name when needed.
  * All runs share one Chrome profile, so ui runs serialize — a wide tree just
    takes longer; --branching-factor 1 gives a clean linear crescendo.
  * --output-dir writes traces + a replay bundle per finding; omit for a dry log.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from advtest_ext.guides import Guide, load_guide, load_guides
from advtest_ext.guides.models import display_title
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
        "--dry-run",
        action="store_true",
        help=(
            "print the mapping (target, seed turns, success signals, staging "
            "steps) and exit — no browser, no LLM calls, no writes"
        ),
    )
    p.add_argument(
        "--provider-config",
        type=Path,
        default=_REPO_ROOT / "configs" / "dev.yaml",
        help="providers YAML with CLAUDE_ATTACKER + GEMINI_TARGET roles (default: configs/dev.yaml)",
    )
    p.add_argument("--target-name", help="override the target adapter (e.g. google_docs_multiturn)")
    p.add_argument("--output-dir", type=Path, help="write traces + replay bundles here")
    p.add_argument(
        "--db-url",
        help="SQLAlchemy URL to persist runs (default: ADVTEST_DB_URL or sqlite:///./advtest.db). "
        "This is what the dashboard reads; pass --no-db to skip persistence.",
    )
    p.add_argument("--no-db", action="store_true", help="don't persist to a database")
    p.add_argument("--run-id", help="use a caller-provided run id")
    p.add_argument(
        "--review-sample-budget",
        type=int,
        default=0,
        help="enqueue up to N traces for expert review (feeds the gold set → PPI). "
        "0 (default) = off. Requires DB persistence.",
    )
    p.add_argument("--max-depth", type=int, help="tap_crescendo tree depth override")
    p.add_argument("--branching-factor", type=int, help="tap_crescendo branching override")
    p.add_argument("--node-max-turns", type=int, help="max turns per Crescendo node")
    p.add_argument(
        "--no-confirm",
        action="store_true",
        help="disable the LLM confirm-judge (heuristic-only convergence)",
    )
    # Multi-turn UI-target options (google_docs_multiturn): keep the run fully
    # UI/no-OAuth by minting the host doc through the browser, or ground on an
    # existing doc you prepared.
    p.add_argument(
        "--browser-mint",
        action="store_true",
        help="mint the host doc through the logged-in browser (no Docs API / OAuth)",
    )
    p.add_argument("--doc-id", help="ground on an existing Doc/Sheet id (no API create)")
    p.add_argument(
        "--setup-var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="template variable for setup_steps; repeat as needed",
    )
    p.add_argument(
        "--skip-setup",
        action="store_true",
        help="do not execute the guide's setup_steps before the TAP run",
    )
    p.add_argument(
        "--setup-settle-seconds",
        type=float,
        default=5.0,
        help="wait after setup for Workspace indexing (default: 5)",
    )
    return p.parse_args()


def _print_dry_run(
    guide: Guide,
    target_name: str | None,
    overrides: dict[str, object],
    target_options: dict[str, object],
) -> None:
    """Print exactly what a live run would do — the seed data, mapped — without
    building the live target or calling any model. map_guide + default_target_name
    are pure, so this touches no browser, LLM, or DB."""
    from advtest_ext.guides.run import default_target_name
    from advtest_ext.guides.to_tap_crescendo import map_guide

    plan = map_guide(guide)
    b = plan.behavior
    tname = target_name or default_target_name(guide)
    print(f"DRY RUN — {display_title(guide)}  [{guide.id}]  (no browser, no LLM calls, no writes)")
    print(f"  target (Playwright side-panel UI): {tname}   surface: {guide.target.surface}")
    if guide.target.workspace:
        print(f"  target workspace: {guide.target.workspace}")
    print(f"  behaviour the attacker steers toward: {b.category} / {b.severity}")
    print(f"    objective: {b.description}")
    print(f"    success  : {b.success_criteria}")
    print("  seed chain (typed verbatim as the opening moves; the attacker then continues):")
    for i, turn in enumerate(guide.exemplar_turns, 1):
        print(f"    {i}. {turn}")
    print("  L0 success signals (deterministic, no LLM):")
    for spec in guide.success.signals:
        detail = spec.get("pattern") or spec.get("token") or spec.get("canary") or ""
        print(f"    - {spec['kind']} {spec['name']}: {detail}")
    if guide.setup_steps:
        print("  staging steps (run first, via real tool ops):")
        for st in guide.setup_steps:
            print(f"    - {st.id}: {st.region}:{st.operation}  [{st.account_role}]")
    if overrides:
        print(f"  attack overrides: {overrides}")
    if target_options:
        print(f"  target options: {target_options}")
    print("  -> drop --dry-run to launch this against the live Gemini side panel.")


def main() -> None:
    args = _parse_args()

    if args.list:
        for guide in load_guides(_GUIDES_DIR):
            print(
                f"{guide.id:36s} {display_title(guide):38s} {guide.target.app}/{guide.target.surface}"
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

    # Target adapter options (multi-turn UI targets). Only set what the operator
    # asked for so the factory defaults hold.
    target_options: dict[str, object] = {}
    if args.browser_mint:
        target_options["browser_mint"] = True
    if args.doc_id:
        target_options["doc_id"] = args.doc_id
    if args.output_dir:
        target_options["ui_output_dir"] = str(args.output_dir)

    if args.dry_run:
        _print_dry_run(guide, args.target_name, overrides, target_options)
        return

    setup_variables: dict[str, str] = {}
    for item in args.setup_var:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"invalid --setup-var {item!r}; expected KEY=VALUE")
        setup_variables[key] = value

    # DB persistence — what the dashboard reads and what the review queue /
    # PPI need. On by default; the review sampler is a no-op without it.
    session_factory = None
    db_url = None
    if not args.no_db:
        from advtest_ext.storage import make_engine, make_session_factory

        session_factory = make_session_factory(make_engine(args.db_url))
        db_url = args.db_url or "ADVTEST_DB_URL / sqlite:///./advtest.db"
    if args.review_sample_budget and session_factory is None:
        raise SystemExit("--review-sample-budget requires DB persistence (drop --no-db)")

    print(f"running guide {guide.id!r} against {args.target_name or guide.target.app} …")
    result = asyncio.run(
        run_guide(
            guide,
            provider_config=args.provider_config,
            target_name=args.target_name,
            target_options=target_options or None,
            confirm_with_llm_judge=not args.no_confirm,
            output_dir=args.output_dir,
            session_factory=session_factory,
            review_sample_budget=args.review_sample_budget,
            run_id=args.run_id,
            execute_setup=not args.skip_setup,
            setup_variables=setup_variables,
            setup_settle_s=args.setup_settle_seconds,
            **overrides,
        )
    )

    stats = result.summary_stats
    print(f"\nrun {result.run_id}: {len(result.traces)} traces")
    print(f"summary: {stats.model_dump() if hasattr(stats, 'model_dump') else stats}")
    if not result.traces:
        raise SystemExit("assessment stopped before producing evidence")
    if args.output_dir:
        print(f"traces + replay bundles under {args.output_dir}")
    if session_factory is not None:
        print(f"persisted to {db_url} — open the dashboard to view this run")
        if args.review_sample_budget:
            print(
                f"enqueued up to {args.review_sample_budget} traces for review — "
                "label them at /hitl to get a calibrated ASR ± CI (PPI)"
            )


if __name__ == "__main__":
    main()
