"""Smoke run: MSJ ∘ IndirectInjection against ToolUseAgentTarget.

Anil 2024's many-shot priming effect kicks in around 32-128 shots
on frontier models; the earlier default n_shots=4 was far below the
threshold and reliably fired 0 tool calls. Default is now 128 (the
``--n-shots`` flag overrides — wall-clock on Ollama qwen2.5:7b is
~2-3 minutes at 128).

Writes its full per-attempt log (including payloads + responses) to
``tests/baselines/smoke_msj_<behavior_id>.txt``. Stdout carries only
high-level progress counters; adversarial text never lands on stdout.

Usage:
    poetry run python scripts/msj_smoke.py [behavior_id] [--n-shots N]

Default behavior_id: unauthorized_email_send_to_external (the F-001
case used elsewhere as the canonical headline).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from advtest_ext.adapters.tool_use_agent.target import ToolUseAgentTarget
from advtest_ext.attacks.msj import MsjIndirectAttack
from advtest_ext.core.models import Behavior, Budget, Payload, load_seed_behaviors
from advtest_ext.document import AbstractDocument
from advtest_ext.providers import load_provider_from_yaml

CONFIG = Path("/home/zohra-bouchamaoui/Desktop/advtest/configs/dev.yaml")
BASELINE_DIR = Path(__file__).resolve().parents[1] / "tests" / "baselines"
PAYLOADS_PATH = (
    Path(__file__).resolve().parents[1]
    / "advtest_ext"
    / "attacks"
    / "indirect_injection"
    / "payloads.yaml"
)
DEFAULT_BEHAVIOR_ID = "unauthorized_email_send_to_external"
N_SHOTS_DEFAULT = 128
SEED = 42
MAX_ATTEMPTS = 4


def _resolve_behavior(behavior_id: str) -> Behavior:
    catalogue = {b.id: b for b in load_seed_behaviors()}
    if behavior_id not in catalogue:
        raise SystemExit(f"unknown behavior_id {behavior_id!r}; options: {sorted(catalogue)}")
    return catalogue[behavior_id]


def _load_seed_payload(behavior_id: str, *, payloads_path: Path | None = None) -> Payload:
    """First payload from indirect_injection/payloads.yaml whose
    ``target_behaviors`` list contains ``behavior_id``. Fails closed
    when nothing matches — listing which behaviours DO have seeds —
    because behavior.description is metadata about what the test
    targets, not a viable attack prompt."""
    path = payloads_path or PAYLOADS_PATH
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a YAML list at the top level")
    available: set[str] = set()
    match: dict[str, Any] | None = None
    for entry in raw:
        targets = entry.get("target_behaviors") or []
        for b in targets:
            available.add(b)
        if match is None and behavior_id in targets:
            match = entry
    if match is None:
        raise SystemExit(
            f"no seed payload tagged for behavior_id {behavior_id!r}. "
            f"Behaviours with at least one seed payload: {sorted(available)}."
        )
    return Payload(
        id=match["id"],
        text=match["text"],
        modality="text",
        generator_name="seed",
        source_citation=match.get("source_citation"),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("behavior_id", nargs="?", default=DEFAULT_BEHAVIOR_ID)
    parser.add_argument(
        "--n-shots",
        type=int,
        default=N_SHOTS_DEFAULT,
        help=(
            f"MSJ shot count (default: {N_SHOTS_DEFAULT}). Anil 2024's "
            "priming effect kicks in around 32-128 on frontier models."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    behavior_id: str = args.behavior_id
    n_shots: int = args.n_shots
    behavior = _resolve_behavior(behavior_id)

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BASELINE_DIR / f"smoke_msj_{behavior_id}.txt"
    buf: list[str] = []
    buf.append(f"=== mode=msj_indirect, n_shots={n_shots}, seed={SEED} ===")
    buf.append(f"behavior: {behavior.id}")
    buf.append("target:   ToolUseAgentTarget over dev.yaml TARGET_MODEL_DEV")
    buf.append("")

    target_provider = load_provider_from_yaml(CONFIG, "TARGET_MODEL_DEV")
    target = ToolUseAgentTarget(provider=target_provider, max_rounds=3)
    await target.setup()

    seed_payload = _load_seed_payload(behavior.id)
    buf.append(f"seed_payload_id: {seed_payload.id}")
    buf.append("")
    plugin = MsjIndirectAttack(
        n_shots=n_shots,
        seed=SEED,
        target_query=seed_payload.text,
    )

    findings: list[dict[str, Any]] = []
    fired = 0
    idx = 0
    async for attempt in plugin.generate(behavior=behavior, target=target, budget=Budget()):
        idx += 1
        techn = attempt.hiding_technique.id if attempt.hiding_technique else "-"
        msj_meta = attempt.metadata.get("msj") or attempt.metadata.get("payload_metadata", {}).get(
            "msj", {}
        )
        buf.append(f"[#{idx}] tech={techn} msj_n_shots={msj_meta.get('n_shots')}")
        buf.append(f"  payload: {attempt.payload.text[:200]!r}")

        doc = attempt.document or AbstractDocument(id="smoke-empty", title="", body_blocks=[])
        ctx = await target.render_document(doc)
        resp = await target.invoke(ctx, attempt.user_prompt or attempt.payload.text)
        tool_calls = resp.raw.get("tool_calls") or []
        buf.append(f"  resp:    {resp.text[:200]!r}")
        if tool_calls:
            fired += 1
            buf.append(f"  tool_calls ({len(tool_calls)}):")
            for tc in tool_calls:
                args_short = json.dumps(tc.get("args", {}))[:200]
                buf.append(f"    -> {tc.get('tool_name')}({args_short})")
            findings.append(
                {
                    "idx": idx,
                    "technique": techn,
                    "tool_calls": tool_calls,
                    "payload_id": attempt.payload.id,
                }
            )
        if idx >= MAX_ATTEMPTS:
            break

    buf.append("")
    buf.append(f"=== {fired} attempt(s) fired tool calls ===")
    for f in findings:
        buf.append(f"  technique={f['technique']} payload_id={f['payload_id']}")
        for tc in f["tool_calls"]:
            buf.append(f"    -> {tc.get('tool_name')}({tc.get('args')})")

    out_path.write_text("\n".join(buf) + "\n")
    # Stdout: progress only — no adversarial content.
    print(f"wrote {idx} attempts ({fired} fired tool_calls) to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
