"""Batch-run TAP/Docs against a set of Discord-derived seed prompts.

Reads ``seeds/index.json`` (from ``materialize_seeds.py``) and,
for each seed, materialises a per-seed RunConfig YAML cloned from
``configs/run_discord_seeds_tap_docs.yaml`` with the seed path and
output dir patched in. Then invokes ``advtest run --config <path>``
as a subprocess per seed.

This script is the operator-facing surface for "I have N Discord
seeds, run a small TAP tree from each against Docs". It does NOT
itself call any LLM — but the subprocesses it spawns DO (the TAP
attacker / judge providers, plus the live Gemini target). Treat
``--dry-run`` as the default reading and only drop the flag once
you're happy with the per-seed configs.

Usage::

    # Dry run — print what would happen, don't execute anything:
    poetry run python scripts/discord_to_seeds/run_tap_batch.py \
        --index    out/discord_seeds/seeds/index.json \
        --base-config configs/run_discord_seeds_tap_docs.yaml \
        --output-root out/discord_seeds/runs \
        --dry-run

    # Real run — invokes ``advtest run`` per seed:
    poetry run python scripts/discord_to_seeds/run_tap_batch.py \
        --index    out/discord_seeds/seeds/index.json \
        --base-config configs/run_discord_seeds_tap_docs.yaml \
        --output-root out/discord_seeds/runs

    # Narrow to a subset:
    ... --behavior-id exfil_doc_via_image_url
    ... --limit 5
    ... --start 10 --limit 5     # process seeds 10..14
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--base-config", type=Path, required=True)
    p.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Per-seed run dirs are created under here.",
    )
    p.add_argument(
        "--per-seed-config-dir",
        type=Path,
        default=None,
        help=("Where to write the rendered per-seed YAMLs (default: <output-root>/_configs)."),
    )
    p.add_argument("--behavior-id", action="append", default=[])
    p.add_argument("--category", action="append", default=[])
    p.add_argument("--start", type=int, default=0, help="Skip the first N seeds.")
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Run at most N seeds (0 = no limit).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Render per-seed YAMLs but do NOT invoke advtest run.",
    )
    p.add_argument(
        "--advtest-cmd",
        default="poetry run advtest run",
        help="Command stem used to invoke advtest run (default: 'poetry run advtest run').",
    )
    return p.parse_args(argv)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required: poetry install") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _dump_yaml(data: dict[str, Any], path: Path) -> None:
    import yaml

    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _seed_passes(seed: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.behavior_id and (seed.get("behavior_id") not in args.behavior_id):
        return False
    if args.category and (seed.get("category") not in args.category):
        return False
    return True


def _per_seed_config(
    *,
    base: dict[str, Any],
    seed: dict[str, Any],
    seed_abs_path: Path,
    run_output_dir: Path,
) -> dict[str, Any]:
    """Clone the base config and patch in the seed-specific fields.

    Three patches happen here:

    1. ``attack_options.tap_indirect.seed_prompt_path`` — set to the
       absolute path of THIS seed's .txt. Absolute so the per-seed
       YAML is portable across whatever CWD the runner uses.
    2. ``output_dir`` — per-seed run dir, lets the dashboard scope
       traces back to a single Discord-derived starting point.
    3. ``behaviors`` — narrowed to the classifier-matched
       behavior_id when one exists. When the classifier left
       ``behavior_id`` null we keep the base list (typically empty
       = whole catalogue), so unmatched seeds still get scored.
    """
    cfg = deepcopy(base)
    cfg.setdefault("attack_options", {}).setdefault("tap_indirect", {})["seed_prompt_path"] = str(seed_abs_path)
    cfg["output_dir"] = str(run_output_dir)
    behavior_id = seed.get("behavior_id")
    if behavior_id:
        cfg["behaviors"] = [behavior_id]
    return cfg


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not args.index.exists():
        print(f"error: --index not found: {args.index}", file=sys.stderr)
        return 2
    if not args.base_config.exists():
        print(f"error: --base-config not found: {args.base_config}", file=sys.stderr)
        return 2

    base = _load_yaml(args.base_config)
    index_data = json.loads(args.index.read_text(encoding="utf-8"))
    seeds = index_data.get("seeds", [])

    per_seed_cfg_dir = args.per_seed_config_dir or (args.output_root / "_configs")
    per_seed_cfg_dir.mkdir(parents=True, exist_ok=True)

    # The seed_path in the index is relative to ``index.json``'s
    # parent's parent (we wrote ``seeds/<file>.txt`` and indexed it
    # as ``seeds/<file>.txt`` relative to ``out/discord_seeds``).
    # Resolve everything from the index file's dir for portability.
    index_root = args.index.parent.parent.resolve()

    filtered: list[tuple[int, dict[str, Any]]] = [(i, s) for i, s in enumerate(seeds) if _seed_passes(s, args)]
    sliced = filtered[args.start :]
    if args.limit:
        sliced = sliced[: args.limit]

    print(
        f"plan: {len(sliced)} of {len(seeds)} seeds will run "
        f"(start={args.start}, limit={args.limit or 'all'}); "
        f"dry-run={args.dry_run}",
        file=sys.stderr,
    )

    n_ok = 0
    n_fail = 0
    t_start = time.time()
    for plan_idx, (orig_idx, seed) in enumerate(sliced):
        seed_abs = (index_root / seed["seed_path"]).resolve()
        if not seed_abs.exists():
            print(
                f"[skip] seed {orig_idx}: file not found {seed_abs}",
                file=sys.stderr,
            )
            n_fail += 1
            continue

        behavior_slug = seed.get("behavior_id") or "unmatched"
        run_dir = (args.output_root / f"{orig_idx:04d}_{behavior_slug}").resolve()
        cfg_path = per_seed_cfg_dir / f"{orig_idx:04d}_{behavior_slug}.yaml"
        cfg = _per_seed_config(
            base=base,
            seed=seed,
            seed_abs_path=seed_abs,
            run_output_dir=run_dir,
        )
        _dump_yaml(cfg, cfg_path)

        cmd = f"{args.advtest_cmd} --config {cfg_path}"
        print(
            f"\n[{plan_idx + 1}/{len(sliced)}] seed#{orig_idx} "
            f"behavior={behavior_slug} cat={seed.get('category')} → {cmd}",
            file=sys.stderr,
        )
        if args.dry_run:
            n_ok += 1
            continue

        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            # We deliberately stream the child's output rather than
            # capturing — TAP runs are long and the operator wants
            # to see progress.
            result = subprocess.run(cmd, shell=True, check=False)
            if result.returncode == 0:
                n_ok += 1
            else:
                n_fail += 1
                print(
                    f"  → advtest run exited {result.returncode}",
                    file=sys.stderr,
                )
        except KeyboardInterrupt:
            print("interrupted — aborting batch", file=sys.stderr)
            return 130

    elapsed = time.time() - t_start
    print(
        f"\nbatch complete: ok={n_ok} fail={n_fail} elapsed={elapsed:.0f}s output_root={args.output_root}",
        file=sys.stderr,
    )
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
