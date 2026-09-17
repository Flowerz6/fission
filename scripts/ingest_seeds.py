"""Ingest the normalised Seed corpus (``seeds_corpus/*.json``) into the
dashboard database.

Both seed kinds compile to the same normalised :class:`Seed` shape (report
Figure 3): expert-authored chains (``human_chat``, e.g. Bikash's real
Google Sheets side-panel session) and public-corpus entries
(``dataset_entry``, e.g. AgentDojo's Workspace suite). This script loads
each normalised file, validates it against the Seed model, and upserts it
into the ``seeds`` table so the ``/seeds`` view can show them side by side.

Pure DB writes — no network, no LLM, no browser. Idempotent on ``seed.id``
(re-running is a no-op for already-ingested seeds).

Usage::

    poetry run python scripts/ingest_seeds.py                 # -> advtest.db
    poetry run python scripts/ingest_seeds.py --dry-run
    poetry run python scripts/ingest_seeds.py --db-url sqlite:///./other.db
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from advtest_ext.core.seed import Seed
from advtest_ext.storage.database import make_engine, make_session_factory, session_scope
from advtest_ext.storage.repository import get_seed, insert_seed

_CORPUS_DIR = Path(__file__).resolve().parents[1] / "seeds_corpus"


def load_seed_corpus(corpus_dir: Path = _CORPUS_DIR) -> list[Seed]:
    """Parse every seed file (YAML or JSON) in the corpus dir into a validated Seed."""
    import yaml

    seeds: list[Seed] = []
    for pattern in ("*.json", "*.yml", "*.yaml"):
        for path in sorted(corpus_dir.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() in {".yaml", ".yml"}:
                seeds.append(Seed.model_validate(yaml.safe_load(text)))
            else:
                seeds.append(Seed.model_validate_json(text))
    return seeds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db-url",
        default=os.environ.get("ADVTEST_DB_URL", "sqlite:///./advtest.db"),
        help="SQLAlchemy DB URL (default: $ADVTEST_DB_URL or sqlite:///./advtest.db)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Parse + report, write nothing."
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite seeds already present (re-ingest edited corpus content).",
    )
    args = ap.parse_args()

    seeds = load_seed_corpus()
    print(f"Loaded {len(seeds)} normalised seed(s) from {_CORPUS_DIR}:")
    for s in seeds:
        n = len(s.payload.get("extracted_turns", []))
        print(f"  [{s.kind:13s}] {s.id:34s} {s.title}  ({n} turns)")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    factory = make_session_factory(make_engine(args.db_url))
    inserted = updated = skipped = 0
    with session_scope(factory) as session:
        for s in seeds:
            existing = get_seed(session, s.id)
            if existing is not None:
                if not args.force:
                    skipped += 1
                    continue
                session.delete(existing)
                session.flush()
                updated += 1
            else:
                inserted += 1
            insert_seed(session, seed=s)
    print(
        f"\nDone: {inserted} inserted, {updated} overwritten, "
        f"{skipped} unchanged -> {args.db_url}"
    )


if __name__ == "__main__":
    main()
