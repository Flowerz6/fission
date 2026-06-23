"""``PapAttack`` — Persuasive Adversarial Prompts (Zeng et al. 2024).

Cheap, deterministic payload-template plugin: for each behaviour we
yield one attempt per ``(strategy, template)`` cross-product, with the
behaviour's description filling the ``{objective}`` slot. Designed to
compose with :class:`IndirectInjectionAttack` so the persuasive
prompt becomes the doc-hidden payload (per FOUNDATIONS.md, PAP is one
of the "ship cheap, composes well" techniques).

The taxonomy lives in ``pap_taxonomy.yaml`` next to this module — its
header explains why the templates are envelope-shaped (no operational
instructions; harmful content arrives via the runtime-substituted
``{objective}``).

Reference: zeng2024pap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from advtest_ext.attacks.base import AttackAttempt, AttackPlugin, AttackPluginClass
from advtest_ext.core.models import Payload

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from advtest_ext.core.models import Behavior, Budget
    from advtest_ext.document.target import DocumentTarget


_DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parent / "pap_taxonomy.yaml"


@dataclass(frozen=True)
class PapTemplate:
    """One persuasion-templated payload skeleton."""

    strategy: str
    template_id: str
    template: str

    def render(self, *, objective: str) -> str:
        return self.template.format(objective=objective)


@dataclass(frozen=True)
class PapStrategy:
    """A persuasion strategy and its bundled templates."""

    name: str
    description: str
    templates: tuple[PapTemplate, ...]


def _load_taxonomy(path: Path) -> tuple[PapStrategy, ...]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a YAML list at the top level")
    out: list[PapStrategy] = []
    for entry in raw:
        if "strategy" not in entry or "templates" not in entry:
            raise ValueError(
                f"{path}: every taxonomy entry needs 'strategy' and 'templates' "
                f"(got keys {sorted(entry)})"
            )
        templates = tuple(
            PapTemplate(
                strategy=entry["strategy"],
                template_id=t["id"],
                template=t["template"],
            )
            for t in entry["templates"]
        )
        out.append(
            PapStrategy(
                name=entry["strategy"],
                description=entry.get("description", ""),
                templates=templates,
            )
        )
    if not out:
        raise ValueError(f"{path}: taxonomy must declare ≥1 strategy")
    return tuple(out)


class PapAttack(AttackPlugin):
    """Persuasive Adversarial Prompts (zeng2024pap).

    Yields one ``AttackAttempt`` per ``(strategy, template)`` pair from
    the loaded taxonomy. The behaviour's ``description`` fills the
    ``{objective}`` slot by default; the constructor's ``objective_fn``
    lets a caller override (e.g. use ``success_criteria`` instead of
    ``description``, or substitute an upstream payload's text in a
    composed setup).

    Filter the strategy set with ``strategies=`` (e.g.
    ``("authority", "scarcity")``) to ablate per-strategy ASR.
    """

    name = "pap"
    version = "0.1.0"
    description = (
        "Persuasive Adversarial Prompts (Zeng 2024). YAML-driven "
        "persuasion-strategy x template cross-product. Composes with "
        "IndirectInjectionAttack as a payload generator."
    )
    references = ["zeng2024pap"]  # noqa: RUF012
    attack_class: AttackPluginClass = "single_turn"
    required_capabilities: list[str] = []  # noqa: RUF012

    def __init__(
        self,
        *,
        taxonomy_path: Path | None = None,
        strategies: Sequence[str] | None = None,
        objective_fn: Any = None,
    ) -> None:
        path = taxonomy_path or _DEFAULT_TAXONOMY_PATH
        loaded = _load_taxonomy(path)
        if strategies is not None:
            requested = tuple(strategies)
            available = {s.name for s in loaded}
            unknown = [s for s in requested if s not in available]
            if unknown:
                raise ValueError(f"unknown strategies {unknown!r}; available: {sorted(available)}")
            loaded = tuple(s for s in loaded if s.name in requested)
            if not loaded:
                raise ValueError("strategies filter selected zero strategies")
        self._taxonomy: tuple[PapStrategy, ...] = loaded
        # objective_fn: Behavior -> str. Default uses behavior.description.
        self._objective_fn = objective_fn or (lambda b: b.description)
        super().__init__()

    @property
    def taxonomy(self) -> tuple[PapStrategy, ...]:
        return self._taxonomy

    async def generate(
        self,
        *,
        behavior: Behavior,
        target: DocumentTarget,
        budget: Budget,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        del target, budget  # taxonomy iteration is target/budget-agnostic
        upstream = (context or {}).get("input_attempt")
        # Composition: if an upstream attempt set context["input_attempt"]
        # (typical for ComposedAttack with PAP as the payload generator),
        # use its payload text as the {objective} so the upstream's
        # refined / augmented payload flows through the persuasion frame.
        if isinstance(upstream, AttackAttempt):
            objective = upstream.payload.text
        else:
            objective = self._objective_fn(behavior)

        for strategy in self._taxonomy:
            for template in strategy.templates:
                rendered = template.render(objective=objective)
                payload_id = f"pap-{behavior.id}-{strategy.name}-{template.template_id}"
                yield AttackAttempt(
                    payload=Payload(
                        id=payload_id,
                        text=rendered,
                        modality="text",
                        generator_name=self.name,
                        source_citation="zeng2024pap",
                    ),
                    user_prompt=rendered,
                    metadata={
                        "pap": {
                            "strategy": strategy.name,
                            "template_id": template.template_id,
                            "behavior_id": behavior.id,
                            "objective_source": (
                                "upstream_payload"
                                if isinstance(upstream, AttackAttempt)
                                else "behavior_description"
                            ),
                        },
                    },
                )


__all__ = ["PapAttack", "PapStrategy", "PapTemplate"]
