"""Seed the strategy library from the guides corpus.

Projects each guide in ``guides/`` into a PIMiner-format strategy file so
the router has a real library to route over and the digester has prior
strategies to widen. Idempotent: existing files are skipped unless
``--overwrite``.

Usage:
  # Seed only the gold (confirmed) guides into the packaged library:
  poetry run python scripts/seed_strategy_library.py --only-gold

  # Seed everything into a scratch dir to inspect first:
  poetry run python scripts/seed_strategy_library.py \
      --out /tmp/strategy_library --all
"""

from __future__ import annotations

import argparse
from pathlib import Path

from advtest_ext.guides.corpus import load_guides
from advtest_ext.strategy.seed import seed_library

_DEFAULT_LIBRARY = Path("advtest_ext/strategy/library")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--guides-dir", default="guides")
    p.add_argument("--out", default=str(_DEFAULT_LIBRARY), help="library dir to write")
    p.add_argument("--only-gold", action="store_true", help="skip discovery-maturity guides")
    p.add_argument("--all", action="store_true", help="include discovery guides (default)")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    guides = load_guides(args.guides_dir)
    written = seed_library(
        guides, args.out, overwrite=args.overwrite, only_gold=args.only_gold
    )
    print(f"Loaded {len(guides)} guide(s); wrote {len(written)} strategy file(s) to {args.out}")
    for path in written:
        print(f"  + {path.name}")
    if not written:
        print("  (nothing written — files exist? use --overwrite, or check --only-gold)")


if __name__ == "__main__":
    main()
