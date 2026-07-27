"""Backfill trace ``raw_response`` with the COMPLETE response assembled from
each trace's intercepted Gemini stream.

ui_mode captures the reply two ways: Playwright's ``inner_text`` read of the
side panel (stored as ``raw_response``) — which is read WHILE the response is
still streaming and is therefore frequently truncated — and the intercepted
``streamGenerate`` bodies (the full server-side response). Newer runs already
persist the assembled full response (see the ui_mode invokers); this script
repairs OLDER traces so every dashboard view (tree, transcript, node detail)
shows the complete answer.

Idempotent: only rewrites a trace whose intercepted assembly is longer than
what's stored, and stashes the original UI read in
``raw_response_data['ui_response']``.

    poetry run python scripts/backfill_full_responses.py --dry-run   # preview
    poetry run python scripts/backfill_full_responses.py             # apply
"""

from __future__ import annotations

import argparse

from advtest_ext.adapters._ui_mode_common.response_assembly import assemble_full_response
from advtest_ext.storage import (
    Trace,
    make_engine,
    make_session_factory,
    session_scope,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None, help="override ADVTEST_DB_URL")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    factory = make_session_factory(make_engine(args.db_url))
    n_scanned = n_updated = chars_recovered = 0

    with session_scope(factory) as s:
        for t in s.query(Trace).all():
            data = dict(t.raw_response_data or {})
            path = data.get("intercepted_traffic_path")
            if not path:
                continue
            n_scanned += 1
            full = assemble_full_response(path)
            current = t.raw_response or ""
            if not full or len(full) <= len(current):
                continue
            chars_recovered += len(full) - len(current)
            n_updated += 1
            if not args.dry_run:
                data.setdefault("ui_response", current)
                t.raw_response = full
                t.raw_response_data = data  # reassign so SQLAlchemy tracks the JSON change
        if args.dry_run:
            s.rollback()

    verb = "would update" if args.dry_run else "updated"
    print(
        f"scanned {n_scanned} intercepted trace(s); {verb} {n_updated} "
        f"(+{chars_recovered:,} chars of previously-truncated response recovered)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
