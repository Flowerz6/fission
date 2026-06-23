"""``TemplateAttack`` — fills behaviour-aware payload templates from
``attacks/payloads/templates.yaml``.

Each template is a ``str.format``-style string with
``{behavior_description}`` and ``{behavior_id}`` placeholders. One
``Behavior`` produces ``len(templates)`` ``AttackAttempt``s — useful
as a slightly-better-than-static baseline that adapts to the target
behaviour.
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


_DEFAULT_PATH = Path(__file__).resolve().parent / "payloads" / "templates.yaml"


class TemplateAttack(AttackPlugin):
    name = "template_payload"
    version = "0.1.0"
    description = "Behaviour-substituted templates from payloads/templates.yaml."
    references = [  # noqa: RUF012
        "greshake2023ipi",
        "zhang2025asb",
        "zeng2024pap",
    ]
    required_capabilities: list[str] = []  # noqa: RUF012
    attack_class: AttackPluginClass = "single_turn"

    def __init__(self, *, templates_path: Path | None = None) -> None:
        self._path = templates_path or _DEFAULT_PATH
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Behavior,
        target: DocumentTarget,
        budget: Budget,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        del target, budget, context
        raw = yaml.safe_load(self._path.read_text())
        if not isinstance(raw, list):
            raise ValueError(f"{self._path} must be a YAML list")
        for tpl in raw:
            text = tpl["template"].format(
                behavior_id=behavior.id,
                behavior_description=behavior.description,
            )
            yield AttackAttempt(
                payload=Payload(
                    id=f"{tpl['id']}::{behavior.id}",
                    text=text,
                    modality="text",
                    generator_name=self.name,
                    source_citation=tpl.get("source_citation"),
                ),
                user_prompt=tpl.get("user_prompt", text),
                metadata={"template_id": tpl["id"], "behavior_id": behavior.id},
            )


__all__ = ["TemplateAttack"]
