"""Code-level registries surfaced on the dashboard.

The CLI's run-time registry (``advtest_ext.cli``) is keyed by
friendly names. The dashboard wants more than just names — it shows
each attack's citations, its ``attack_class`` (single_turn /
multi_turn / composed), and each target's ``DocumentCapabilities``
flags. We avoid a hard import of every plugin module by having
each subpackage's ``__citations__`` / ``__all__`` declare what's
shippable; the helpers below instantiate just enough to surface
metadata.

Listing is best-effort: a plugin that fails to construct (missing
constructor arg) is skipped gracefully. The dashboard renders
whatever did import; broken entries don't 500 the page.
"""

from __future__ import annotations

from dataclasses import dataclass

from advtest_ext.attacks.base import AttackPlugin
from advtest_ext.attacks.indirect_injection import IndirectInjectionAttack
from advtest_ext.attacks.pyrit_wrappers import PapAttack
from advtest_ext.attacks.static import StaticPayloadAttack
from advtest_ext.attacks.template import TemplateAttack
from advtest_ext.document import StubDocumentTarget
from advtest_ext.document.target import DocumentTarget


@dataclass(frozen=True)
class AttackMetadata:
    """Surface attributes the /attacks page renders. ``construct``
    is the plugin's no-arg constructor — pinned to the same callable
    the CLI registry uses so the dashboard reflects what the
    operator can run, not just everything that imports."""

    name: str
    description: str
    references: tuple[str, ...]
    attack_class: str
    version: str

    @classmethod
    def of(cls, plugin: AttackPlugin) -> AttackMetadata:
        return cls(
            name=plugin.name,
            description=plugin.description,
            references=tuple(plugin.references),
            attack_class=str(plugin.attack_class),
            version=plugin.version,
        )


@dataclass(frozen=True)
class AdapterMetadata:
    """Surface attributes the /adapters page renders. ``capabilities``
    is the ``DocumentCapabilities`` model dump, so the table can show
    e.g. ``multi_turn=True`` / ``tool_use=False`` directly."""

    name: str
    capabilities: dict[str, object]
    supported_techniques: tuple[str, ...]

    @classmethod
    def of(cls, target: DocumentTarget) -> AdapterMetadata:
        caps = target.capabilities()
        return cls(
            name=type(target).__name__,
            capabilities=caps.model_dump(mode="json"),
            supported_techniques=tuple(sorted(target.supported_techniques())),
        )


# ----------------------------------------------------------------------
# Default registries — mirror the CLI's no-arg constructible set.
# ----------------------------------------------------------------------


def _safe_construct_attacks() -> list[AttackPlugin]:
    """Each plugin in turn; a constructor failure is logged but does
    not break the page. Excludes attacks that need an attacker
    Provider (PAIR / Crescendo / TAP) — the dashboard would rather
    show an honest "needs config" placeholder than crash."""
    out: list[AttackPlugin] = []
    for ctor in (
        StaticPayloadAttack,
        TemplateAttack,
        IndirectInjectionAttack,
        PapAttack,
    ):
        try:
            out.append(ctor())
        except Exception:  # pragma: no cover - defensive boundary
            continue
    return out


def _safe_construct_adapters() -> list[DocumentTarget]:
    out: list[DocumentTarget] = []
    for ctor in (StubDocumentTarget,):
        try:
            out.append(ctor())
        except Exception:  # pragma: no cover - defensive
            continue
    return out


def list_attacks() -> list[AttackMetadata]:
    return [AttackMetadata.of(p) for p in _safe_construct_attacks()]


def list_adapters() -> list[AdapterMetadata]:
    return [AdapterMetadata.of(t) for t in _safe_construct_adapters()]


__all__ = ["AdapterMetadata", "AttackMetadata", "list_adapters", "list_attacks"]
