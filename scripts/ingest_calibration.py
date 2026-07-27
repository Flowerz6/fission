"""Ingest a downloaded benchmark dump into the oracle calibration set.

The calibration set is externally-labelled adversarial examples the
oracle is measured against *before* any in-house human labelling exists
(docs/FISSION_MEASUREMENT_SPINE.md §7). This script normalises a local
JSONL dump onto the ``CalibrationExample`` table via the vendored
registry mapping (configs/calibration_datasets.yaml).

The registry knows the field/label mapping per dataset — you only
supply the dataset slug and the path to the dump you downloaded.

List what's ingestable:
    poetry run python scripts/ingest_calibration.py --list

Ingest a Lakera gandalf-rct dump into the default SQLite DB:
    poetry run python scripts/ingest_calibration.py \
        --dataset lakera_gandalf_rct --path ~/data/gandalf.jsonl

Data is NOT vendored (licences + size) — download each dataset from the
URL in the registry first. Dynamic-env benches (AgentDojo, MCPTox) are
not ingestable here; drive them through a live adapter instead.
"""

from __future__ import annotations

import argparse
import sys

from advtest_ext.calibration import (
    ingest_records,
    load_dataset_registry,
    records_from_jsonl,
)
from advtest_ext.storage import (
    count_calibration_examples,
    make_engine,
    make_session_factory,
    session_scope,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", help="registry slug, e.g. lakera_gandalf_rct")
    parser.add_argument("--path", help="path to the downloaded JSONL dump")
    parser.add_argument("--db-url", default=None, help="override ADVTEST_DB_URL")
    parser.add_argument("--list", action="store_true", help="list registry datasets and exit")
    args = parser.parse_args(argv)

    registry = load_dataset_registry()

    if args.list:
        for name, src in sorted(registry.items()):
            flag = "ingestable" if src.ingestable else "dynamic — live adapter only"
            print(f"{name:28s} [{src.attack_type:12s}] {flag}")
        return 0

    if not args.dataset or not args.path:
        parser.error("--dataset and --path are required (or use --list)")

    src = registry.get(args.dataset)
    if src is None:
        print(f"unknown dataset {args.dataset!r}; use --list", file=sys.stderr)
        return 2
    if not src.ingestable:
        print(
            f"{args.dataset!r} is a dynamic-env bench with no static labels; "
            "drive it through a live adapter instead.",
            file=sys.stderr,
        )
        return 2

    records = records_from_jsonl(args.path, src)
    factory = make_session_factory(make_engine(args.db_url))
    with session_scope(factory) as s:
        inserted = ingest_records(s, records)
    with session_scope(factory) as s:
        total = count_calibration_examples(s, dataset=args.dataset)

    print(
        f"ingested {inserted} new / {len(records)} parsed rows from "
        f"{args.dataset} ({total} total in DB for this dataset)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
