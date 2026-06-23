"""Materialize classified Discord prompts as TAP seed-prompt files.

Reads ``classified.jsonl`` (from ``classify_prompts.py``) and emits:

  * One ``seeds/<idx>_<behavior_id>.txt`` per usable row — the raw
    prompt text, suitable for
    ``attack_options.tap_indirect.seed_prompt_path`` in an advtest
    RunConfig.
  * One ``seeds/index.json`` manifest mapping each seed file to
    its classifier metadata (behavior_id, category, attack_class,
    confidence, source_sha256). The batch runner consumes this.
  * A short ``seeds/SUMMARY.txt`` for the operator — counts per
    behavior_id / category so they can sanity-check the
    distribution before kicking off long TAP runs.

Filters:

  * Only rows with ``is_attack_prompt: true``.
  * Only rows whose confidence ≥ ``--min-confidence`` (default 0.5).
  * Optional ``--behavior-id`` / ``--category`` to narrow the set
    if the operator wants to run only one slice.

No LLM calls. Safe to run locally.

Usage::

    poetry run python scripts/discord_to_seeds/materialize_seeds.py \
        --in  out/discord_seeds/classified.jsonl \
        --out out/discord_seeds/seeds \
        --min-confidence 0.5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_SAFE_ID_RE = re.compile(r"[^a-z0-9_]+")


def _slug(value: str | None, fallback: str) -> str:
    """Make a value safe for a filename. Lowercase, underscores, no
    funky chars — works on Linux + macOS + Windows."""
    base = (value or fallback).lower()
    cleaned = _SAFE_ID_RE.sub("_", base).strip("_")
    return cleaned or fallback


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="in_path", type=Path, required=True)
    p.add_argument(
        "--out",
        dest="out_dir",
        type=Path,
        required=True,
        help="Output directory. seeds/ + index.json + SUMMARY.txt land here.",
    )
    p.add_argument("--min-confidence", type=float, default=0.5)
    p.add_argument(
        "--behavior-id",
        action="append",
        default=[],
        help="Keep only rows matching this behavior_id (repeatable).",
    )
    p.add_argument(
        "--category",
        action="append",
        default=[],
        help="Keep only rows matching this BehaviorCategory (repeatable).",
    )
    return p.parse_args(argv)


def _row_passes(row: dict[str, Any], args: argparse.Namespace) -> bool:
    if not row.get("is_attack_prompt"):
        return False
    if float(row.get("confidence", 0.0) or 0.0) < args.min_confidence:
        return False
    if args.behavior_id and (row.get("behavior_id") not in args.behavior_id):
        return False
    if args.category and (row.get("category") not in args.category):
        return False
    if not (row.get("prompt_text") or "").strip():
        return False
    return True


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not args.in_path.exists():
        print(f"error: --in not found: {args.in_path}", file=sys.stderr)
        return 2

    seeds_dir = args.out_dir
    seeds_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    per_behavior: Counter[str] = Counter()
    per_category: Counter[str] = Counter()
    n_in = 0
    n_kept = 0

    for raw_line in args.in_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        n_in += 1
        if not _row_passes(row, args):
            continue

        behavior_slug = _slug(row.get("behavior_id"), "unmatched")
        # Index gives every seed a stable position so a TAP batch
        # run can be sharded by `seed_idx % N`.
        idx = len(manifest)
        seed_filename = f"{idx:04d}_{behavior_slug}.txt"
        seed_path = seeds_dir / seed_filename
        seed_path.write_text((row["prompt_text"]).strip() + "\n", encoding="utf-8")

        manifest.append(
            {
                "seed_idx": idx,
                "seed_path": str(seed_path.relative_to(seeds_dir.parent)),
                "behavior_id": row.get("behavior_id"),
                "category": row.get("category"),
                "attack_class": row.get("attack_class"),
                "confidence": float(row.get("confidence", 0.0) or 0.0),
                "source_id": row.get("source_id", ""),
                "content_sha256": row.get("content_sha256", ""),
                "rationale": row.get("rationale", ""),
            }
        )
        per_behavior[row.get("behavior_id") or "(none)"] += 1
        per_category[row.get("category") or "(none)"] += 1
        n_kept += 1

    (seeds_dir / "index.json").write_text(json.dumps({"seeds": manifest}, indent=2), encoding="utf-8")

    summary_lines = [
        f"input rows:         {n_in}",
        f"materialised seeds: {n_kept}",
        f"min confidence:     {args.min_confidence}",
        "",
        "per behavior_id:",
    ]
    for k, v in per_behavior.most_common():
        summary_lines.append(f"  {v:4d}  {k}")
    summary_lines.append("")
    summary_lines.append("per category:")
    for k, v in per_category.most_common():
        summary_lines.append(f"  {v:4d}  {k}")
    (seeds_dir / "SUMMARY.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print("\n".join(summary_lines), file=sys.stderr)
    print(f"\nindex → {seeds_dir / 'index.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
