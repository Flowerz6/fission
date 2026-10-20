"""Drive the PIMiner-style strategy loop (advtest_ext/strategy/).

Runs route -> iterative attack -> rollout over a JSONL of samples, with a
real Claude attacker/router loaded from a provider config. The rollout
backend is pluggable; today ``--target offline`` uses the deterministic
marker reward (no live target, no cost) so the loop can be exercised with
a real attacker before the AgentDojo / live-Workspace backends land.

Prerequisites:
  export ANTHROPIC_API_KEY=...   # or put it in .env (auto-loaded below)

Usage:
  # Real Claude attacker, cold-start, offline reward, one sample:
  poetry run python scripts/run_strategy_loop.py \
      --samples tests/data/strategy_samples.jsonl --limit 1

  # With the library router over the packaged strategy library:
  poetry run python scripts/run_strategy_loop.py \
      --samples <path>.jsonl --route --library-dir advtest_ext/strategy/library

Notes:
  * --attacker-role / --router-role name roles in the provider config
    (default configs/dev.yaml): CLAUDE_ATTACKER, CLAUDE_PLANNER, etc.
  * This drives real LLMs and is NOT part of the test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from advtest_ext.strategy.factory import attacker_from_config, router_from_config
from advtest_ext.strategy.orchestrator import IterativeAttack, RunResult
from advtest_ext.strategy.samples import load_samples
from advtest_ext.strategy.targets import OfflineMarkerTarget


def _build_target(args: argparse.Namespace):
    if args.target == "offline":
        return OfflineMarkerTarget()
    if args.target == "agentdojo":
        from advtest_ext.strategy.backends.agentdojo_backend import AgentDojoTarget

        return AgentDojoTarget(
            args.target_model,
            endpoint=args.target_endpoint,
            api_key_env=args.target_api_key_env,
            threat_model=args.threat_model,
        )
    if args.target == "workspace":
        from advtest_ext.providers.factory import load_provider_from_yaml
        from advtest_ext.strategy.backends.workspace_backend import WorkspaceTarget

        return WorkspaceTarget(
            product=args.ws_product,
            trigger_prompt=args.trigger_prompt,
            judge_provider=load_provider_from_yaml(args.config, args.judge_role),
            output_dir=args.ws_output_dir,
            threat_model=args.threat_model,
        )
    raise SystemExit(f"unknown --target {args.target!r}. Wired: offline, agentdojo, workspace.")


def _print_report(result: RunResult) -> None:
    print("\n=== strategy-loop run ===")
    for o in result.outcomes:
        where = f"iter {o.hit_iter}" if o.status == "hit" else f"{len(o.attempts)} iters"
        print(f"  {o.status.upper():5s} {o.sample_key}  via {o.strategy_ids}  ({where})")
    print(f"\nASR: {result.hits}/{len(result.outcomes)} = {result.asr:.2f}\n")


async def _main_async(args: argparse.Namespace) -> RunResult:
    samples = load_samples(args.samples, limit=args.limit)
    if not samples:
        raise SystemExit(f"no samples loaded from {args.samples}")

    attacker = attacker_from_config(
        args.config, args.attacker_role, max_tokens=args.attacker_max_tokens
    )
    router = (
        router_from_config(args.config, args.router_role) if args.route else None
    )
    target = _build_target(args)
    loop = IterativeAttack(
        target=target,
        attacker=attacker,
        router=router,
        library_dir=args.library_dir,
        n_max=args.n_max,
        threat_model=args.threat_model,
    )
    result = await loop.run(samples)

    if args.digest:
        if not args.library_dir:
            raise SystemExit("--digest requires --library-dir")
        from advtest_ext.providers.factory import load_provider_from_yaml
        from advtest_ext.strategy.digester import Digester, ProviderDecider

        decider = ProviderDecider(load_provider_from_yaml(args.config, args.digest_role))
        report = await Digester(decider).digest(
            result, samples, library_dir=args.library_dir
        )
        print(
            f"\n=== digest ===\n  hits={report.n_hits} "
            f"appended={report.appended} widened={report.widened} "
            f"created={report.created}\n  audit: {report.audit_path}"
        )

    if args.persist:
        from advtest_ext.strategy.persist import persist_run

        run_id = persist_run(
            result,
            samples=samples,
            db_url=args.db_url,
            target=args.target,
            target_model=args.target_model,
            threat_model=args.threat_model,
            library_dir=args.library_dir,
            config={
                "attacker_role": args.attacker_role,
                "router": bool(args.route),
                "n_max": args.n_max,
                "samples": args.samples,
            },
        )
        print(f"\n=== persisted ===\n  strategy_run id: {run_id}")

        if args.replay > 0:
            from advtest_ext.strategy.replay import persist_replays, replay_run

            stats = await replay_run(
                result,
                target,
                samples,
                n_trials=args.replay,
                target_model=args.target_model,
            )
            persist_replays(stats, run_id, db_url=args.db_url)
            print(
                "\n=== replay (repeated-trial M/N) ===\n  "
                + (
                    "; ".join(
                        f"{s.sample_key} {s.successes_m}/{s.trials_n}" for s in stats
                    )
                    or "(no winning candidates to replay)"
                )
            )

    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--samples", required=True, help="JSONL of samples")
    p.add_argument("--config", default="configs/dev.yaml", help="provider config YAML")
    p.add_argument("--attacker-role", default="CLAUDE_ATTACKER")
    p.add_argument(
        "--attacker-max-tokens",
        type=int,
        default=4096,
        help="override attacker max_tokens (5.x thinking needs headroom; "
        "the role config's 512 intermittently returns empty/truncated)",
    )
    p.add_argument("--router-role", default="CLAUDE_ATTACKER")
    p.add_argument("--route", action="store_true", help="route over a library")
    p.add_argument("--library-dir", default=None)
    p.add_argument("--digest", action="store_true", help="digest hits into the library")
    p.add_argument("--digest-role", default="CLAUDE_DIGESTER")
    p.add_argument("--target", default="offline", choices=["offline", "agentdojo", "workspace"])
    p.add_argument("--target-model", default="gpt-4o-mini", help="agentdojo target model")
    p.add_argument("--target-endpoint", default=None, help="OpenAI-compatible base URL")
    p.add_argument("--target-api-key-env", default=None, help="env var for the target key")
    # workspace target
    p.add_argument("--ws-product", default="google_docs", help="workspace product")
    p.add_argument("--ws-output-dir", default="out/workspace_rollout")
    p.add_argument("--judge-role", default="CLAUDE_JUDGE", help="oracle judge provider role")
    p.add_argument("--trigger-prompt", default=None, help="override the journey trigger prompt")
    p.add_argument("--n-max", type=int, default=10)
    p.add_argument("--threat-model", default="black_box", choices=["black_box", "white_box"])
    p.add_argument("--limit", type=int, default=None, help="max samples to run")
    p.add_argument("--out", default=None, help="write outcomes JSON here")
    p.add_argument("--persist", action="store_true", help="persist the run to storage")
    p.add_argument("--db-url", default=None, help="override ADVTEST_DB_URL for --persist")
    p.add_argument(
        "--replay",
        type=int,
        default=0,
        help="repeated-trial replay: re-run each winning injection N times "
        "for true M/N (requires --persist)",
    )
    args = p.parse_args()

    result = asyncio.run(_main_async(args))
    _print_report(result)

    if args.out:
        payload = [
            {
                "sample_key": o.sample_key,
                "status": o.status,
                "hit_iter": o.hit_iter,
                "strategy_ids": o.strategy_ids,
                "n_attempts": len(o.attempts),
                "attempts": [
                    {
                        "iter": a.iter,
                        "analysis": a.analysis,
                        "injection": a.injection,
                        "security": a.security,
                        "utility": a.utility,
                    }
                    for a in o.attempts
                ],
            }
            for o in result.outcomes
        ]
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
