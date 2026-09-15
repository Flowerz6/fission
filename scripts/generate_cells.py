#!/usr/bin/env python
"""Grow a guide's mutation diversity palette with an LLM.

Each mutation operator's ``cell_values`` are the concrete alternatives the TAP
tree branches over; more (distinct) cells => a wider, non-duplicate search. This
proposes new cells per operator, keeps only the novel ones (novelty gate), and
optionally writes them back into the guide file (YAML or JSON, whichever is the
authoritative on-disk source for that id).

Usage:
    # preview proposals (no write), using the CLAUDE_ATTACKER role from the config
    poetry run python scripts/generate_cells.py --guide sheets_skill_leak --n 6

    # write the accepted cells back into guides/sheets_skill_leak.yaml (or .json)
    poetry run python scripts/generate_cells.py --guide sheets_skill_leak --n 6 --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--guide", required=True, help="guide id (file guides/<id>.yaml or .json)")
    ap.add_argument("--n", type=int, default=5, help="new cells to request per operator")
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="novelty cutoff: reject a candidate whose word-overlap with any existing "
        "cell is >= this (0..1, higher = more permissive)",
    )
    ap.add_argument("--role", default="CLAUDE_ATTACKER", help="provider role in the config YAML")
    ap.add_argument(
        "--provider-config",
        default=os.environ.get("ADVTEST_PROVIDER_CONFIG", str(_REPO_ROOT / "configs" / "dev.yaml")),
    )
    ap.add_argument("--write", action="store_true", help="write accepted cells into the guide JSON")
    ap.add_argument("--json", action="store_true", help="emit proposals as JSON")
    args = ap.parse_args()

    from advtest_ext.guides.cell_generator import apply_to_guide_dict, generate
    from advtest_ext.guides.corpus import load_guides, resolve_guide_path
    from advtest_ext.providers.base import ChatMessage
    from advtest_ext.providers.factory import load_provider_from_yaml

    guide_path = resolve_guide_path(_REPO_ROOT / "guides", args.guide)
    if guide_path is None:
        sys.exit(f"guide file not found for id {args.guide!r} in guides/")
    guide = next((g for g in load_guides(_REPO_ROOT / "guides") if g.id == args.guide), None)
    if guide is None:
        sys.exit(f"guide {args.guide!r} could not be loaded")
    if not guide.mutation_operators:
        sys.exit(f"guide {args.guide!r} has no typed mutation_operators to extend")

    provider = load_provider_from_yaml(args.provider_config, args.role)

    async def complete(prompt: str) -> str:
        resp = await provider.complete([ChatMessage(role="user", content=prompt)])
        return resp.text

    proposals = asyncio.run(
        generate(complete, guide, n_per_operator=args.n, threshold=args.threshold)
    )

    if args.json:
        print(json.dumps([p.to_dict() for p in proposals], indent=2))
    else:
        total_new = 0
        for p in proposals:
            total_new += len(p.accepted)
            print(f"\n{p.operator_name}  [{p.dimension}]  ({len(p.existing)} existing)")
            if p.accepted:
                for value in p.accepted:
                    print(f"  + {value}")
            else:
                print("  (no novel cells proposed)")
        print(f"\n{total_new} new cells across {len(proposals)} operators.")

    if args.write:
        text = guide_path.read_text(encoding="utf-8")
        if guide_path.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            raw = yaml.safe_load(text)
            apply_to_guide_dict(raw, proposals)
            out = yaml.safe_dump(
                raw, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
            )
        else:
            raw = json.loads(text)
            apply_to_guide_dict(raw, proposals)
            out = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
        guide_path.write_text(out, encoding="utf-8")
        print(f"\nwrote accepted cells into {guide_path}")
    else:
        print("\n(preview only — pass --write to save these into the guide)")


if __name__ == "__main__":
    main()
