"""Export persisted strategy-loop runs into a pilot PilotDataset JSON.

Reads strategy_runs/strategy_attempts from storage (SQLite default, or
ADVTEST_DB_URL / --db-url) and writes the 8-field PilotDataset the Google
pilot consumes — the same schema as scripts/export_pilot_dataset.py, but
sourced from the PIMiner strategy loop.

Usage:
  poetry run python scripts/export_strategy_dataset.py --out out/strategy_pilot.json
  poetry run python scripts/export_strategy_dataset.py --run-id strat-abc123 --db-url sqlite:///x.db
"""

from __future__ import annotations

import argparse
from pathlib import Path

from advtest_ext.storage.database import make_engine, make_session_factory, session_scope
from advtest_ext.strategy.export import build_dataset_from_strategy_runs


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, help="output JSON path")
    p.add_argument("--db-url", default=None, help="override ADVTEST_DB_URL")
    p.add_argument("--run-id", action="append", help="limit to these run ids (repeatable)")
    args = p.parse_args()

    factory = make_session_factory(make_engine(args.db_url))
    with session_scope(factory) as session:
        dataset = build_dataset_from_strategy_runs(session, run_ids=args.run_id)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
    c = dataset.counts
    print(
        f"wrote {args.out}: {c.successful} successful, "
        f"{c.plausible_unsuccessful} plausible-unsuccessful; "
        f"by_harm={c.by_harm_type}; by_journey={c.by_journey}"
    )


if __name__ == "__main__":
    main()
