"""Check that every guide is *complete for our system*, not just schema-valid.

Reports per-guide errors (the system cannot run/verify it) and warnings (runs but
degraded). Exit code is non-zero if any guide has an error, so this doubles as a
CI gate.

Usage::

    poetry run python scripts/check_guides.py                 # whole corpus
    poetry run python scripts/check_guides.py guides/x.yaml    # specific files
    poetry run python scripts/check_guides.py --errors-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from advtest_ext.guides.completeness import check_guide
from advtest_ext.guides.corpus import load_guide, load_guides

_GUIDES_DIR = Path(__file__).resolve().parents[1] / "guides"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", type=Path, help="Specific guide files (default: all).")
    ap.add_argument("--errors-only", action="store_true", help="Hide warnings.")
    args = ap.parse_args()

    guides = [load_guide(p) for p in args.paths] if args.paths else load_guides(_GUIDES_DIR)

    n_err = n_warn = n_clean = 0
    for guide in sorted(guides, key=lambda g: g.id):
        issues = check_guide(guide)
        errors = [i for i in issues if i.level == "error"]
        warnings = [i for i in issues if i.level == "warning"]
        n_err += len(errors)
        n_warn += len(warnings)
        if not errors and not warnings:
            n_clean += 1
            print(f"  OK    {guide.id}")
            continue
        marker = "FAIL " if errors else "warn "
        print(f"  {marker} {guide.id}")
        for i in errors:
            print(f"        error  [{i.code}] {i.message}")
        if not args.errors_only:
            for i in warnings:
                print(f"        warn   [{i.code}] {i.message}")

    print(
        f"\n{len(guides)} guides: {n_clean} clean, "
        f"{n_err} errors, {n_warn} warnings."
    )
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    main()
