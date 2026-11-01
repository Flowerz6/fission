"""Backfill ``traces.failure_class`` for rows classified before the taxonomy
existed (Brief 1).

Walks every trace whose ``failure_class IS NULL``, loads the guide for its
``behavior_id`` from the corpus (``guides/``) to recover the success anchors,
and runs the SAME deterministic classifier the live scoring sites use
(:func:`advtest_ext.scoring.failure.classify_failure`). A ``verdict='error'``
row is a transport/provider failure, so it is passed ``transport_error=True``.
Success rows classify to ``None`` and are left NULL.

Idempotent: only touches NULL rows, so a second run is a no-op. ``--dry-run``
computes and prints the distribution but writes nothing.

    .venv/bin/python scripts/backfill_failure_class.py --dry-run   # preview, no writes
    .venv/bin/python scripts/backfill_failure_class.py             # apply
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from advtest_ext.guides.corpus import load_guide, resolve_guide_path
from advtest_ext.guides.mutation_runtime import anchors_for
from advtest_ext.scoring.failure import classify_failure
from advtest_ext.storage import (
    Trace,
    make_engine,
    make_session_factory,
    session_scope,
)

_VALID_VERDICTS = {"success", "partial", "refused", "error"}


def _anchors_resolver(guides_dir: Path):
    """behavior_id → success anchors, cached (one guide serves many traces)."""
    cache: dict[str, list[str]] = {}

    def resolve(behavior_id: str) -> list[str]:
        if behavior_id in cache:
            return cache[behavior_id]
        anchors: list[str] = []
        path = resolve_guide_path(guides_dir, behavior_id)
        if path is not None:
            try:
                anchors = anchors_for(load_guide(path))
            except Exception:  # a malformed guide just yields no anchors, not a crash
                anchors = []
        cache[behavior_id] = anchors
        return anchors

    return resolve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None, help="override ADVTEST_DB_URL")
    parser.add_argument(
        "--guides-dir", default="guides", help="corpus directory to resolve guides from"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args(argv)

    resolve_anchors = _anchors_resolver(Path(args.guides_dir))
    factory = make_session_factory(make_engine(args.db_url))

    n_scanned = n_updated = 0
    counts: Counter[str] = Counter()
    no_guide: set[str] = set()

    with session_scope(factory) as s:
        for t in s.query(Trace).filter(Trace.failure_class.is_(None)).all():
            n_scanned += 1
            verdict = t.verdict if t.verdict in _VALID_VERDICTS else "error"
            anchors = resolve_anchors(t.behavior_id)
            if not anchors and resolve_guide_path(Path(args.guides_dir), t.behavior_id) is None:
                no_guide.add(t.behavior_id)
            fc = classify_failure(
                verdict=verdict,  # type: ignore[arg-type]
                response=t.raw_response or "",
                payload_text=t.payload_text or "",
                anchors=anchors,
                transport_error=(verdict == "error"),
            )
            counts["(null: success)" if fc is None else fc] += 1
            if fc is not None:
                n_updated += 1
                if not args.dry_run:
                    t.failure_class = fc
        if args.dry_run:
            s.rollback()

    total_classified = sum(v for k, v in counts.items() if not k.startswith("("))
    print(f"{'failure_class':<20} {'count':>8} {'share':>8}")
    print("-" * 38)
    for cls, n in counts.most_common():
        share = (n / total_classified * 100) if total_classified and not cls.startswith("(") else 0.0
        print(f"{cls:<20} {n:>8} {share:>7.1f}%")
    verb = "would update" if args.dry_run else "updated"
    print("-" * 38)
    print(f"scanned {n_scanned} null row(s); {verb} {n_updated}")
    if no_guide:
        print(f"no guide file for behavior_id(s) (no anchors): {sorted(no_guide)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
