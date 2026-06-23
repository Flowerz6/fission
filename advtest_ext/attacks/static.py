"""``StaticPayloadAttack`` — yields hand-written payloads from
``attacks/payloads/static.yaml``.

The payload list is small by design: it's a sanity-floor baseline
("does direct prompt injection still work without any cleverness?")
that more sophisticated plugins (PAIR, BoN, MSJ) compose against and
must beat.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from advtest_ext.attacks.base import AttackAttempt, AttackPlugin, AttackPluginClass
from advtest_ext.core.models import Payload

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from advtest_ext.core.models import Behavior, Budget
    from advtest_ext.document.target import DocumentTarget


_DEFAULT_PATH = Path(__file__).resolve().parent / "payloads" / "static.yaml"


class StaticPayloadAttack(AttackPlugin):
    name = "static_payload"
    version = "0.1.0"
    description = "Hand-written payloads from payloads/static.yaml."
    references = [  # noqa: RUF012
        "greshake2023ipi",
        "promptware_nassi2025",
        "geminijack_2025",
    ]
    required_capabilities: list[str] = []  # noqa: RUF012
    attack_class: AttackPluginClass = "single_turn"

    def __init__(self, *, payloads_path: Path | None = None) -> None:
        self._path = payloads_path or _DEFAULT_PATH
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Behavior,
        target: DocumentTarget,
        budget: Budget,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        # Static payloads are deliberately behaviour-agnostic — they're a
        # baseline that doesn't adapt. Caller is expected to bound
        # iteration; this generator yields the full file every time.
        del behavior, target, budget, context
        raw = yaml.safe_load(self._path.read_text())
        if not isinstance(raw, list):
            raise ValueError(f"{self._path} must be a YAML list")
        for entry in raw:
            text = entry["text"]
            yield AttackAttempt(
                payload=Payload(
                    id=entry["id"],
                    text=text,
                    modality="text",
                    generator_name=self.name,
                    source_citation=entry.get("source_citation"),
                ),
                user_prompt=entry.get("user_prompt", text),
                metadata={"static_id": entry["id"]},
            )


__all__ = ["StaticPayloadAttack"]
