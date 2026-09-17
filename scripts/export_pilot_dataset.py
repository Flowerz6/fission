"""Export persisted runs into the Workspace IPI pilot delivery JSON.

Folds traces into per-attack records carrying the brief's required fields
(poisoned payload, verbatim attack string, harm type, ASR as N/M over trials,
user-journey reference, lineage id) plus standards-aligned metadata, split into
successful vs. plausible-unsuccessful sets. See ``docs/PILOT_DELIVERY_FORMAT.md``.

Examples:
    # every run in the DB
    poetry run python scripts/export_pilot_dataset.py --all-runs --out pilot_dataset.json

    # specific runs
    poetry run python scripts/export_pilot_dataset.py \
        --run run-a95d60639e65 --run run-c4c6066f7b27 --out pilot_dataset.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from advtest_ext.delivery.export import DEFAULT_PLAUSIBILITY_FLOOR, build_dataset_for_runs
from advtest_ext.storage.database import make_engine, make_session_factory, session_scope
from advtest_ext.storage.repository import list_runs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", action="append", default=[], help="run id (repeatable)")
    p.add_argument("--all-runs", action="store_true", help="export every run in the DB")
    p.add_argument("--db-url", default=None, help="override ADVTEST_DB_URL")
    p.add_argument("--guides-dir", default="guides", help="guide corpus dir for enrichment")
    p.add_argument("--out", default="pilot_dataset.json", help="output JSON path")
    p.add_argument(
        "--plausibility-floor",
        type=float,
        default=DEFAULT_PLAUSIBILITY_FLOOR,
        help="min best-judge-score for a no-success group to count as plausible-unsuccessful",
    )
    p.add_argument(
        "--include-hard-refusals",
        action="store_true",
        help="also emit pure hard-refusal groups (normally dropped)",
    )
    args = p.parse_args(argv)

    factory = make_session_factory(make_engine(args.db_url))
    with session_scope(factory) as s:
        run_ids = list(args.run)
        if args.all_runs:
            run_ids = [r.id for r in list_runs(s, limit=100_000)]
        if not run_ids:
            p.error("no runs selected: pass --run <id> (repeatable) or --all-runs")

        dataset = build_dataset_for_runs(
            s,
            run_ids,
            guides_dir=args.guides_dir,
            plausibility_floor=args.plausibility_floor,
            include_hard_refusals=args.include_hard_refusals,
        )

    out = Path(args.out)
    out.write_text(json.dumps(dataset.model_dump(mode="json"), indent=2), encoding="utf-8")

    c = dataset.counts
    print(f"wrote {out}")
    print(f"  runs:                    {len(dataset.generated_from_run_ids)}")
    print(f"  successful attacks:      {c.successful}")
    print(f"  plausible-unsuccessful:  {c.plausible_unsuccessful}")
    print(f"  harm types (successful): {dict(sorted(c.by_harm_type.items()))}")
    print(f"  severity   (successful): {dict(sorted(c.by_severity.items()))}")
    print(f"  journeys   (successful): {dict(sorted(c.by_journey.items()))}")
    unspecified = c.by_harm_type.get("unspecified", 0)
    if unspecified:
        print(f"  NOTE: {unspecified} successful attacks are harm_type=unspecified (need manual tag)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
