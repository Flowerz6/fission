"""Attack plugin abstraction.

Subsumes PyRIT's Orchestrator + AttackStrategy split into one thin
abstraction with composition baked in: any two plugins can be
``a.compose_with(b)`` to form a :class:`ComposedAttack`. The canonical
example is a payload generator (PAIR / BoN / MSJ) composed with a
delivery mechanism (indirect injection via a hiding technique) — the
cross-product of payloads and delivery variants.

Cascading three-stage composition is one of the directions
bhagwatkar2025firewalls flags for stronger benchmarks; we lay the
groundwork here. See FOUNDATIONS.md ("Attack techniques").

Design note on ``AttackAttempt.metadata``: open ``dict[str, Any]`` by
intent. PAIR will want to record ``rounds_completed``; BoN will want
the augmentation chain; Crescendo will want a turn count; future
plugins will invent more. Pinning a schema now would force every new
plugin into a contortion or a migration, so we don't.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from advtest_ext.core.models import HidingTechnique, Payload
from advtest_ext.document import AbstractDocument
from advtest_ext.document.target import Response
from advtest_ext.providers.base import ChatMessage

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from advtest_ext.core.models import Behavior, Budget
    from advtest_ext.document.target import DocumentTarget


AttackPluginClass = Literal[
    "single_turn",
    "multi_turn",
    "document_inject",
    "composed",
]
"""Structural shape of an attack plugin's flow. Distinct from
``advtest_ext.core.models.AttackClass``, which describes hiding
mechanism (zero_width / white_text / etc.). The two are orthogonal:
a single-turn attack can use any hiding class, and so on."""


class AttackAttempt(BaseModel):
    """One concrete attempt produced by a plugin's ``generate`` loop.

    All non-metadata fields are typed; ``metadata`` is intentionally an
    open ``dict[str, Any]`` (see module docstring).
    """

    model_config = ConfigDict(extra="forbid")

    payload: Payload
    hiding_technique: HidingTechnique | None = None
    document: AbstractDocument | None = None
    user_prompt: str = ""
    conversation_history: list[ChatMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    precomputed_response: Response | None = None
    """Set when an integrated attack plugin (e.g.
    :class:`PairIndirectAttack`) has already performed the target
    interaction in its own loop — typically because the refinement
    signal needed the response in-band. The orchestrator skips its
    own ``render_document``/``invoke`` when this is set and uses
    the cached response directly, avoiding a duplicate per-round
    target dance (critical for ``ui_mode`` Google Docs where each
    dance is a ~45s Playwright session)."""


class AttackPlugin(ABC):
    """Abstract attack plugin.

    Concrete subclasses set the six metadata attributes as class-level
    constants and implement :meth:`generate`. ``references`` must be
    non-empty (enforced at instantiation) — every attack we ship cites
    the paper / disclosure that motivates it; this is the same
    contract the seed Behaviour catalogue and the
    ``__citations__`` tests already enforce module-wide.
    """

    # Class-level metadata. Subclasses MUST override `name` and
    # `references`; the rest have sane defaults but most should be
    # overridden too.
    #
    # Not annotated as ClassVar even though concrete plugins set them
    # as class-level constants: ComposedAttack synthesises instance-
    # level values (since "name(static_payload, stub_delivery)" is per-
    # composition), and ClassVar disallows that. The defaults are
    # sentinels validated by ``__init__``.
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    references: list[str] = []  # noqa: RUF012 — sentinel; non-empty enforced in __init__
    required_capabilities: list[str] = []  # noqa: RUF012 — sentinel
    attack_class: AttackPluginClass = "single_turn"

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set a non-empty `name`")
        if not self.references:
            raise ValueError(f"{type(self).__name__} must declare ≥1 citation key in `references`")

    @abstractmethod
    def generate(
        self,
        *,
        behavior: Behavior,
        target: DocumentTarget,
        budget: Budget,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        """Yield ``AttackAttempt``s for this ``behavior`` against
        ``target``. Plugins are responsible for honouring ``budget``
        (queries, time, $) — the orchestrator only consumes what's
        yielded. ``context`` is an optional dict carrying state from
        an outer plugin (e.g. :class:`ComposedAttack` passes the
        upstream payload-generating attempt as ``input_attempt``)."""

    def compose_with(self, other: AttackPlugin) -> ComposedAttack:
        """``self`` is the payload generator, ``other`` is the
        delivery mechanism. The pairing is asymmetric — see
        :class:`ComposedAttack`."""
        return ComposedAttack(payload_generator=self, delivery=other)


class ComposedAttack(AttackPlugin):
    """Cross-product composition: for each payload-generating attempt,
    iterate the delivery plugin (with the upstream attempt available
    as ``context["input_attempt"]``) and merge.

    The merge takes ``payload`` from the payload generator and
    ``hiding_technique`` / ``document`` / ``user_prompt`` /
    ``conversation_history`` from the delivery side, with payload-side
    fallbacks where the delivery side leaves them empty. Metadata is
    namespaced under ``payload_metadata`` and ``delivery_metadata`` so
    nothing clobbers.

    This is the foundation for the canonical "PAIR generates payload,
    indirect-injection delivers it" pattern flagged in FOUNDATIONS.md
    (see also ``bhagwatkar2025firewalls`` on cascading composition).
    """

    attack_class: AttackPluginClass = "composed"
    description: str = "Cross-product of a payload generator and a delivery mechanism."

    def __init__(self, *, payload_generator: AttackPlugin, delivery: AttackPlugin) -> None:
        self._payload_generator = payload_generator
        self._delivery = delivery

        # Compose metadata from the two components as INSTANCE attrs,
        # not class attrs — different composed instances must have
        # distinct names/references.
        self.name = f"composed({payload_generator.name},{delivery.name})"
        self.version = f"{payload_generator.version}+{delivery.version}"
        # Union the citation keys, preserving order so the test
        # hygiene check stays meaningful and stable.
        seen: set[str] = set()
        merged: list[str] = []
        for key in (*payload_generator.references, *delivery.references):
            if key not in seen:
                seen.add(key)
                merged.append(key)
        self.references = merged
        self.required_capabilities = sorted(
            set(payload_generator.required_capabilities) | set(delivery.required_capabilities)
        )
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Behavior,
        target: DocumentTarget,
        budget: Budget,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        async for upstream in self._payload_generator.generate(
            behavior=behavior, target=target, budget=budget, context=context
        ):
            sub_context: dict[str, Any] = {**(context or {}), "input_attempt": upstream}
            async for downstream in self._delivery.generate(
                behavior=behavior, target=target, budget=budget, context=sub_context
            ):
                yield _merge(upstream, downstream)


def _merge(payload_attempt: AttackAttempt, delivery_attempt: AttackAttempt) -> AttackAttempt:
    """Combine an upstream payload-generating attempt with a
    downstream delivery-mechanism attempt. See
    :class:`ComposedAttack` docstring for which side wins on each
    field."""
    return AttackAttempt(
        payload=payload_attempt.payload,
        hiding_technique=delivery_attempt.hiding_technique or payload_attempt.hiding_technique,
        document=delivery_attempt.document or payload_attempt.document,
        user_prompt=delivery_attempt.user_prompt or payload_attempt.user_prompt,
        conversation_history=(
            delivery_attempt.conversation_history or payload_attempt.conversation_history
        ),
        metadata={
            "payload_metadata": payload_attempt.metadata,
            "delivery_metadata": delivery_attempt.metadata,
        },
    )


__all__ = [
    "AttackAttempt",
    "AttackPlugin",
    "AttackPluginClass",
    "ComposedAttack",
]
