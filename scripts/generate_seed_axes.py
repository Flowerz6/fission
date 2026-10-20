#!/usr/bin/env python
"""Give a *thin* guide a seed-specific mutation cell palette.

A guide with a ``mutation_surface`` but no explicit ``mutation_operators`` still
branches at runtime — ``typed_operators`` derives free-form operators from the
surface prose — but with no ``cell_values`` the strategist re-proposes values
each time, which duplicates at a wide branching factor. This script materialises
those runtime-derived operators into the guide (same dimensions ``infer_dimension``
picks, ``mutable_fields`` wired for carrier axes) and has the configured
``CLAUDE_ATTACKER`` role (your hybrid.yaml Qwen) generate a seed-specific cell
palette for each, then previews or writes them back.

Usage:
    poetry run python scripts/generate_seed_axes.py --guide docs_indirect_summary_leak --n 6
    poetry run python scripts/generate_seed_axes.py --guide docs_indirect_summary_leak --n 6 --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _slug(text: str, dimension: str, index: int) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"the", "a", "an", "of", "in", "on", "to", "and", "or", "for", "that", "which", "used"}
    keep = [w for w in words if w not in stop][:3]
    return "_".join(keep) if keep else f"{dimension}_{index}"


def _carrier_mutable_fields(guide: object) -> list[str]:
    """Where a carrier-placement operator's cells apply: the guide's plant step,
    else its carrier_payload. Empty when the guide plants nothing (the operator
    then behaves free-form, as the runtime-derived legacy one already does)."""
    for step in getattr(guide, "setup_steps", []) or []:
        sid = getattr(step, "id", "") or ""
        if any(tok in sid.lower() for tok in ("plant", "carrier", "email", "doc", "seed")):
            return [f"setup_steps.{sid}"]
    if getattr(guide, "carrier_payload", None):
        return ["carrier_payload"]
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--guide", required=True, help="guide id")
    ap.add_argument("--n", type=int, default=6, help="cells to request per axis")
    ap.add_argument("--threshold", type=float, default=0.6, help="novelty cutoff (0..1)")
    ap.add_argument("--role", default="CLAUDE_ATTACKER")
    ap.add_argument(
        "--provider-config",
        default=os.environ.get("ADVTEST_PROVIDER_CONFIG", str(_REPO_ROOT / "configs" / "dev.yaml")),
    )
    ap.add_argument("--write", action="store_true", help="write operators + cells into the guide")
    args = ap.parse_args()

    from advtest_ext.attacks.tap_search import infer_dimension
    from advtest_ext.guides.cell_generator import generate
    from advtest_ext.guides.corpus import load_guides, resolve_guide_path
    from advtest_ext.guides.models import GuideMutationOperator
    from advtest_ext.providers.base import ChatMessage
    from advtest_ext.providers.factory import load_provider_from_yaml

    guide_path = resolve_guide_path(_REPO_ROOT / "guides", args.guide)
    guide = next((g for g in load_guides(_REPO_ROOT / "guides") if g.id == args.guide), None)
    if guide is None or guide_path is None:
        sys.exit(f"guide {args.guide!r} not found")

    existing = {op.name for op in (guide.mutation_operators or [])}
    new_ops: list[GuideMutationOperator] = []
    for i, axis in enumerate(guide.mutation_surface or []):
        dim = infer_dimension(axis).value
        mutable = _carrier_mutable_fields(guide) if dim == "carrier_placement" else []
        # A carrier_placement operator must name a concrete field to rewrite.
        # A seed-only guide plants its carrier inside the exemplar turns, not a
        # staged artifact, so there is nothing to bind — downgrade to the axis's
        # real intent (a "pretext"/"framing" axis is framing, else it's the
        # wording of the hidden instruction) so it still gets a cell palette
        # instead of failing validation.
        if dim == "carrier_placement" and not mutable:
            dim = "framing" if re.search(r"pretext|framing", axis, re.IGNORECASE) else "wording"
        name = _slug(axis, dim, i)
        if name in existing or any(o.name == name for o in new_ops):
            name = f"{name}_{i}"
        new_ops.append(
            GuideMutationOperator(
                name=name,
                dimension=dim,  # type: ignore[arg-type]
                description=axis,
                mutable_fields=mutable,
                cell_values=[],
            )
        )
    if not new_ops:
        sys.exit(f"guide {args.guide!r} has no mutation_surface to build axes from")

    # Generate cells against the materialised operators (keep any real existing ones).
    guide.mutation_operators = [*(guide.mutation_operators or []), *new_ops]
    provider = load_provider_from_yaml(args.provider_config, args.role)

    async def complete(prompt: str) -> str:
        resp = await provider.complete([ChatMessage(role="user", content=prompt)])
        return resp.text

    proposals = asyncio.run(generate(complete, guide, n_per_operator=args.n, threshold=args.threshold))
    accepted = {p.operator_name: p.accepted for p in proposals}

    print(f"\n=== {args.guide}  ({len(new_ops)} axes) ===")
    op_dicts = []
    for op in new_ops:
        cells = accepted.get(op.name, [])
        mf = f"  mutable_fields={op.mutable_fields}" if op.mutable_fields else ""
        print(f"\n{op.name}  [{op.dimension}]{mf}")
        print(f"  axis: {op.description}")
        for c in cells:
            print(f"  + {c}")
        if not cells:
            print("  (no novel cells)")
        d = op.model_dump(mode="json")
        d["cell_values"] = cells
        op_dicts.append(d)

    if not args.write:
        print("\n(preview only — pass --write to save these into the guide)")
        return

    text = guide_path.read_text(encoding="utf-8")
    if guide_path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(text)
        raw["mutation_operators"] = [*(raw.get("mutation_operators") or []), *op_dicts]
        out = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
    else:
        raw = json.loads(text)
        raw["mutation_operators"] = [*(raw.get("mutation_operators") or []), *op_dicts]
        out = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    guide_path.write_text(out, encoding="utf-8")
    print(f"\nwrote {len(op_dicts)} operators into {guide_path}")


if __name__ == "__main__":
    main()
