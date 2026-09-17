"""Convert seed guides to YAML — the trackable authoring format for seeds.

YAML is the format an initial seed is authored and tracked in (a structured,
human-diffable file). This converter takes an already-validated JSON ``Guide``
(e.g. Bikash's ``seed_data/POC v3/formatted_guides/*.json``) and emits the
equivalent ``guides/<id>.yaml``.

The conversion is guaranteed lossless: the JSON is loaded through the same
``load_guide`` chokepoint the runner uses, dumped to YAML, then re-loaded, and
the two are compared by :func:`content_hash` (which is format-agnostic because it
hashes ``model_dump(mode="json")``). If the hashes differ the YAML is NOT
written and the script exits non-zero, so a broken conversion can never enter the
corpus.

A "FISSION SEED CAPTURE" ``.md`` (the human record next to the payload) is the
provenance/transcript for a guide, not a second source of truth: its structured
fields (id, author, attack_class, ...) were already folded into the JSON guide
by hand. This script therefore converts from the JSON. Pass ``--capture`` only to
cross-check that the capture's ``id:`` matches the guide id.

Usage::

    # one guide
    poetry run python scripts/seed_capture_to_yaml.py \
        "seed_data/POC v3/formatted_guides/gmail_00000_configuration_footer.json"

    # every Bikash-authored JSON guide in guides/ -> guides/<id>.yaml
    poetry run python scripts/seed_capture_to_yaml.py --all

    # verify existing YAML round-trips, write nothing
    poetry run python scripts/seed_capture_to_yaml.py --all --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from advtest_ext.guides.corpus import load_guide
from advtest_ext.guides.models import Guide, content_hash

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUIDES_DIR = _REPO_ROOT / "guides"


def guide_to_yaml(guide: Guide) -> str:
    """Serialize a Guide to YAML in the model's field order.

    ``model_dump(mode="json")`` preserves field declaration order and uses only
    JSON-native scalars, so ``yaml.safe_dump`` needs no custom representers.
    ``sort_keys=False`` keeps the readable authored order; ``allow_unicode`` keeps
    payload text intact rather than escaping it."""
    payload = guide.model_dump(mode="json")
    body = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    header = (
        f"# Seed guide (YAML authoring format). id: {guide.id}\n"
        f"# Converted from JSON via scripts/seed_capture_to_yaml.py — do not hand-edit\n"
        f"# both formats for one id; YAML wins in load_guides().\n"
    )
    return header + body


def _capture_id(capture_path: Path) -> str | None:
    """Pull the ``id:`` line out of a FISSION SEED CAPTURE .md, if present."""
    for raw in capture_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("id:"):
            return line.split(":", 1)[1].strip()
    return None


def convert_one(
    json_path: Path,
    out_dir: Path,
    *,
    check: bool,
    capture: Path | None = None,
) -> tuple[str, bool]:
    """Convert one JSON guide to YAML. Returns (id, changed). Raises on mismatch."""
    guide = load_guide(json_path)

    if capture is not None:
        cap_id = _capture_id(capture)
        if cap_id and cap_id != guide.id:
            raise ValueError(
                f"capture id {cap_id!r} != guide id {guide.id!r} ({capture})"
            )

    text = guide_to_yaml(guide)

    # Round-trip guarantee: reload the YAML we just produced and require identical
    # attack content. Written to a temp string first, never trusting the on-disk
    # file until it verifies.
    reloaded = Guide.model_validate(yaml.safe_load(text))
    if content_hash(reloaded) != content_hash(guide):
        raise RuntimeError(
            f"round-trip content hash mismatch for {guide.id} — YAML not written"
        )

    out_path = out_dir / f"{guide.id}.yaml"
    existing = out_path.read_text(encoding="utf-8") if out_path.is_file() else None
    changed = existing != text

    if check:
        # In check mode, verify an existing YAML also round-trips, but write nothing.
        if existing is not None:
            on_disk = Guide.model_validate(yaml.safe_load(existing))
            if content_hash(on_disk) != content_hash(guide):
                raise RuntimeError(
                    f"on-disk {out_path.name} diverged from source {json_path.name}"
                )
        return guide.id, changed

    if changed:
        out_path.write_text(text, encoding="utf-8")
    return guide.id, changed


def _bikash_guides(guides_dir: Path) -> list[Path]:
    """Guides in the corpus (YAML or JSON) whose provenance names Bikash.

    Globs both formats so ``--all`` still finds the seeds after they migrate to
    YAML: in ``--check`` mode this re-verifies each YAML re-dumps to itself
    (idempotent), and if a stray JSON twin reappears it gets reconverted."""
    paths: list[Path] = []
    for pattern in ("*.json", "*.yml", "*.yaml"):
        for p in sorted(guides_dir.glob(pattern)):
            guide = load_guide(p)
            source = (guide.provenance.source if guide.provenance else "") or ""
            if "bikash" in source.lower():
                paths.append(p)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "json", nargs="*", type=Path, help="JSON guide file(s) to convert to YAML."
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Convert every Bikash-authored JSON guide in guides/.",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=_GUIDES_DIR,
        help="Directory to write <id>.yaml into (default: guides/).",
    )
    ap.add_argument(
        "--capture",
        type=Path,
        default=None,
        help="Optional FISSION SEED CAPTURE .md to cross-check the id against.",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="Verify round-trip only; write nothing. Exit non-zero on mismatch.",
    )
    args = ap.parse_args()

    sources: list[Path] = list(args.json)
    if args.all:
        sources.extend(_bikash_guides(_GUIDES_DIR))
    if not sources:
        ap.error("pass one or more JSON guide paths, or --all")

    # De-dup while preserving order.
    seen: set[Path] = set()
    ordered: list[Path] = []
    for p in sources:
        if p not in seen:
            seen.add(p)
            ordered.append(p)

    exit_code = 0
    for path in ordered:
        try:
            guide_id, changed = convert_one(
                path, args.out_dir, check=args.check, capture=args.capture
            )
        except (ValueError, RuntimeError) as exc:
            print(f"  FAIL  {path.name}: {exc}")
            exit_code = 1
            continue
        if args.check:
            state = "would change" if changed else "up to date"
        else:
            state = "wrote" if changed else "unchanged"
        print(f"  ok    {guide_id:38s} {state}")

    if exit_code:
        print("\nOne or more guides failed to convert.", file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
