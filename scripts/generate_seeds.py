#!/usr/bin/env python
"""Grow the seed corpus with Claude — new attack CLASSES, not mutations.

Drives ``advtest_ext.guides.seed_generator`` (which encodes the hand-design
process as the system prompt) with a Claude model behind an Anthropic key, so the
corpus gains diverse, distinct-class candidate seeds with the same quality bar as
the hand-authored batch. Every generated seed is fail-closed validated (builds as a
Guide + passes completeness + names only registered ops) before it is written.

A generated seed is a CANDIDATE — run it against live Gemini and keep the ones that
fire their oracle and reproduce.

Usage:
    # preview 5 seeds (no write), using the CLAUDE_SEEDER role (Anthropic / your model)
    poetry run python scripts/generate_seeds.py --n 5

    # write accepted seeds into guides/
    poetry run python scripts/generate_seeds.py --n 8 --write

    # steer a specific class / surface
    poetry run python scripts/generate_seeds.py --n 3 --class "memory poisoning" --surface google_docs --write
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUIDES_DIR = _REPO_ROOT / "guides"


def _load_dotenv() -> None:
    """Load repo-root .env into the environment (keys not already set), so the
    Anthropic key resolves without the caller exporting it — same convenience the
    CLI path gives run_guide."""
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=5, help="how many seeds to generate")
    ap.add_argument("--role", default="CLAUDE_SEEDER", help="provider role in the config YAML")
    ap.add_argument(
        "--provider-config",
        default=os.environ.get("ADVTEST_PROVIDER_CONFIG", str(_REPO_ROOT / "configs" / "hybrid.yaml")),
    )
    ap.add_argument("--class", dest="klass", help="steer the vulnerability class for every seed")
    ap.add_argument("--surface", help="steer the target app/surface for every seed")
    ap.add_argument("--max-repair", type=int, default=2, help="validation-repair attempts per seed")
    ap.add_argument("--write", action="store_true", help="write accepted seeds into guides/")
    args = ap.parse_args()
    _load_dotenv()

    from advtest_ext.guides.corpus import load_guides
    from advtest_ext.guides.seed_generator import generate_seed
    from advtest_ext.providers.base import ChatMessage
    from advtest_ext.providers.factory import load_provider_from_yaml

    corpus = load_guides(_GUIDES_DIR)
    existing_ids = [g.id for g in corpus]
    covered = [c.strip() for g in corpus if g.threat_class for c in str(g.threat_class).split(",")]

    provider = load_provider_from_yaml(args.provider_config, args.role)

    async def complete(prompt: str) -> str:
        resp = await provider.complete([ChatMessage(role="user", content=prompt)])
        return resp.text

    async def run() -> None:
        accepted = 0
        for i in range(args.n):
            result = await generate_seed(
                complete,
                class_hint=args.klass,
                surface_hint=args.surface,
                existing_ids=existing_ids,
                covered_classes=covered,
                max_repair=args.max_repair,
            )
            seed = result.seed or {}
            sid = (result.guide_dict or {}).get("id") or seed.get("id") or f"seed_{i}"
            if not result.valid:
                print(f"  [{i + 1}/{args.n}] REJECTED {sid}: {result.errors[:1]}")
                continue
            accepted += 1
            klass = (result.guide_dict or {}).get("threat_class", "?")
            app = ((result.guide_dict or {}).get("target") or {}).get("app", "?")
            print(f"  [{i + 1}/{args.n}] OK {sid:34} {app:15} {klass}")
            # keep successive seeds diverse within this run
            existing_ids.append(sid)
            covered.extend(str(klass).split(","))
            if args.write:
                path = _GUIDES_DIR / f"{sid}.yaml"
                if path.exists():
                    print(f"        (skip write — {path.name} already exists)")
                    continue
                path.write_text(
                    yaml.safe_dump(result.guide_dict, sort_keys=False, allow_unicode=True, width=100),
                    encoding="utf-8",
                )
                print(f"        wrote {path.relative_to(_REPO_ROOT)}")
        print(f"\n{accepted}/{args.n} valid candidate seeds"
              + (" written to guides/" if args.write else " (preview only — pass --write)"))
        if accepted and not args.write:
            print("Validate the winners: run_guide.py --guide <id> … then run_rollup.py")

    asyncio.run(run())


if __name__ == "__main__":
    main()
