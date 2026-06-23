"""Smoke run: BoN ∘ MsjIndirectAttack — the headline composition.

Per Hughes 2024 Table 4, BoN over an MSJ inner gets ~28x sample
efficiency over MSJ alone on Claude 3.5 Sonnet. This script runs
a small instance of the composition against the dev backend.

Inner is :class:`MsjIndirectAttack`, NOT :class:`MsjAttack`.
:class:`MsjAttack` puts the demo transcript on
``attempt.conversation_history``, but
:meth:`ToolUseAgentTarget.invoke` ignores ``conversation_history``
(its threat model is "AI summarising a document," not chat) — so
``BoN ∘ MsjAttack`` silently drops the priming.
:class:`MsjIndirectAttack` instead embeds the transcript IN the
document via ``comment_thread`` / ``citation_block`` techniques,
so the priming survives into the agent invocation.

Output goes to ``tests/baselines/smoke_composed_<behavior_id>.txt``;
stdout carries only counters.

Usage:
    poetry run python scripts/composed_smoke.py [behavior_id]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from advtest_ext.adapters.tool_use_agent.target import ToolUseAgentTarget
from advtest_ext.attacks.bon import BonAttack
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
N_SHOTS = 4
N_VARIANTS = 3
SEED = 42
MAX_ATTEMPTS = 12


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


async def main() -> None:
    behavior_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BEHAVIOR_ID
    behavior = _resolve_behavior(behavior_id)

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BASELINE_DIR / f"smoke_composed_{behavior_id}.txt"
    buf: list[str] = []
    buf.append(f"=== mode=bon_msj, n_shots={N_SHOTS}, n_variants={N_VARIANTS}, seed={SEED} ===")
    buf.append(f"behavior: {behavior.id}")
    buf.append("")

    target_provider = load_provider_from_yaml(CONFIG, "TARGET_MODEL_DEV")
    target = ToolUseAgentTarget(provider=target_provider, max_rounds=3)
    await target.setup()

    seed_payload = _load_seed_payload(behavior.id)
    buf.append(f"seed_payload_id: {seed_payload.id}")
    buf.append("")
    inner = MsjIndirectAttack(
        n_shots=N_SHOTS,
        seed=SEED,
        target_query=seed_payload.text,
    )
    plugin = BonAttack(
        inner=inner,
        n_variants=N_VARIANTS,
        seed=SEED,
        # MsjIndirectAttack puts the priming TRANSCRIPT on payload.text
        # (and embeds it in the document via comment_thread /
        # citation_block); augmenting payload.text would garble that
        # transcript and lose the priming. Augment the seed query
        # (= upstream.user_prompt) instead.
        augment_field="user_prompt",
        # no judge => no per-variant target invocation inside BoN; we
        # invoke once below per yielded attempt
        stop_on_success=False,
    )

    findings: list[dict[str, Any]] = []
    fired = 0
    idx = 0
    async for attempt in plugin.generate(behavior=behavior, target=target, budget=Budget()):
        idx += 1
        bon_meta = attempt.metadata.get("bon", {})
        # MsjIndirect nests its own upstream (the MsjAttack leg) under
        # ``upstream_metadata.upstream_metadata.msj``; the indirect-
        # injection technique chosen for delivery sits under
        # ``upstream_metadata.indirect_injection``.
        upstream = attempt.metadata.get("upstream_metadata", {})
        inj_meta = upstream.get("indirect_injection", {})
        msj_meta = upstream.get("upstream_metadata", {}).get("msj", {})
        chain = ",".join(bon_meta.get("augmentation_chain") or [])
        buf.append(
            f"[#{idx}] msj_n_shots={msj_meta.get('n_shots')} "
            f"tech={inj_meta.get('technique_id', '-')} "
            f"bon[{bon_meta.get('variant_index')}]={chain}"
        )
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
                    "tool_calls": tool_calls,
                    "payload_id": attempt.payload.id,
                }
            )
        if idx >= MAX_ATTEMPTS:
            break

    buf.append("")
    buf.append(f"=== {fired} attempt(s) fired tool calls ===")
    for f in findings:
        buf.append(f"  payload_id={f['payload_id']}")
        for tc in f["tool_calls"]:
            buf.append(f"    -> {tc.get('tool_name')}({tc.get('args')})")

    out_path.write_text("\n".join(buf) + "\n")
    print(f"wrote {idx} attempts ({fired} fired tool_calls) to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
