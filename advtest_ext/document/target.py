"""``DocumentTarget`` ABC and a stub implementation for tests.

Threat model
------------
PyRIT's ``PromptTarget`` is prompt-string-centric: a string in, a
response string out. Indirect prompt injection (IPI) against
productivity AI doesn't fit that shape. The attack surface is a
*document* with a primary content channel (visible text) and several
side channels (comments, footnotes, suggested edits, hidden formatting,
metadata, attached links) that the AI also reads. The threat is that
content from these channels is treated as *instructions* rather than
*data* — the data/instruction confusion described in Greshake et al.
2023 (greshake2023ipi), the founding paper of the IPI threat class.

``DocumentTarget`` is the document-shaped peer of ``PromptTarget``.
Concrete subclasses render an ``AbstractDocument`` to whatever native
form the target product accepts (a Google Doc, a Gmail draft, a Sheet),
hand it to the AI, and capture the response. They may *wrap* a
PyRIT ``PromptTarget`` for the actual model call — that's the
expected composition pattern — but the surface here is intentionally
document-centric, not prompt-centric.

References
----------
* Greshake et al. 2023 — IPI threat class (greshake2023ipi).
* InjecAgent (zhan2024injecagent) — direct-harm vs. data-exfil
  Behavior taxonomy that ``Response`` should eventually carry.
"""

from __future__ import annotations

import random
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from advtest_ext.document.document import AbstractDocument
from advtest_ext.providers.base import ChatMessage, Provider

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    from pyrit.prompt_target import PromptTarget


class DocumentCapabilities(BaseModel):
    """What an individual ``DocumentTarget`` can faithfully render.

    The orchestrator uses this to filter techniques: a target that
    can't render comments shouldn't be asked to run a
    comment-injection technique."""

    model_config = ConfigDict(extra="forbid")

    block_types: set[str] = Field(default_factory=set)
    formatting: bool = False
    comments: bool = False
    multimodal: bool = False
    multi_turn: bool = False
    tool_use: bool = False


class NativeContext(BaseModel):
    """Opaque handle returned by ``render_document``. Concrete
    targets subclass this to carry whatever bookkeeping they need to
    later invoke and tear down (e.g. a Google Doc ID + revision
    pointer). The base class stores the rendered native form so a
    Stub target can introspect it without subclassing."""

    model_config = ConfigDict(extra="allow")

    context_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rendered: Any = None


class Response(BaseModel):
    """Captured AI response to a document + user prompt invocation."""

    model_config = ConfigDict(extra="forbid")

    text: str
    complied: bool | None = None
    """Whether the target acted on a hidden injection. ``None`` means
    "judge layer hasn't decided yet"; stub / signal-bearing targets
    (e.g. a target that can introspect its own outputs) populate it
    directly so tests can assert on it."""
    raw: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)


class DocumentTarget(ABC):
    """Document-centric peer of PyRIT's ``PromptTarget``.

    Concrete subclasses implement the full lifecycle of a
    document-attack run: ``setup`` once, ``render_document`` to
    convert an ``AbstractDocument`` into the target's native format,
    ``invoke`` to trigger the AI and capture its output, and
    ``teardown`` to clean up any side effects (delete the doc, close
    the session). They may compose a PyRIT ``PromptTarget`` for the
    actual model call.
    """

    def __init__(self, *, prompt_target: PromptTarget | None = None) -> None:
        self.prompt_target = prompt_target

    @abstractmethod
    async def setup(self) -> None: ...

    @abstractmethod
    async def render_document(self, doc: AbstractDocument) -> NativeContext: ...

    @abstractmethod
    async def invoke(self, context: NativeContext, user_prompt: str) -> Response: ...

    @abstractmethod
    async def teardown(self, context: NativeContext) -> None: ...

    def reset(self) -> None:
        """Start a fresh conversation on the next ``invoke``.

        Single-turn targets are stateless across conversations, so the
        default is a no-op. Multi-turn targets (which accumulate persona
        across turns) override this so multi-conversation attacks
        (TapChainAttack / TapCrescendoAttack) can clear state between
        tree nodes while keeping the warm session."""
        return None

    @abstractmethod
    def capabilities(self) -> DocumentCapabilities: ...

    @abstractmethod
    def supported_techniques(self) -> set[str]:
        """Technique IDs (matching ``HiddenContent.technique_id``) this
        target can faithfully render. ``{"*"}`` means "claims to render
        anything" (used by stubs)."""


# ----------------------------------------------------------------------
# Stub implementation
# ----------------------------------------------------------------------


def _walk_collect_hidden(obj: Any, out: set[str]) -> None:
    """Recursively gather ``technique_id`` values from any
    ``HiddenContent`` markers found in a serialised document."""
    if isinstance(obj, dict):
        h = obj.get("hidden")
        if isinstance(h, dict):
            tid = h.get("technique_id")
            if isinstance(tid, str):
                out.add(tid)
        for v in obj.values():
            _walk_collect_hidden(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_collect_hidden(v, out)


def _linearize_text(rendered: dict[str, Any]) -> str:
    """Best-effort flatten of visible text for realism-mode prompts.
    Only walks ``body_blocks`` — sub-elements (comments, footnotes)
    are intentionally excluded so the realism prompt mirrors what a
    user "sees"; hidden payloads in body blocks remain because
    ``HiddenContent`` doesn't strip the text it tags."""
    parts: list[str] = []
    for block in rendered.get("body_blocks", []) or []:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


class StubDocumentTarget(DocumentTarget):
    """In-memory ``DocumentTarget`` for unit tests and dry runs.

    Two modes:

    * **Pure stub** (default): render returns the document's serialised
      form; invoke inspects it for ``HiddenContent`` markers and
      flips a coin (weighted by ``compliance_probability``) to decide
      whether to "comply" with the hidden instruction. Comply means
      the response text references which technique IDs were honoured;
      this gives test code an inspectable signal without needing an
      LLM.

    * **Realism** (``provider`` set): the stub still records the
      compliance signal, but the response text comes from a real LLM
      call routed through the advtest ``Provider`` abstraction
      (``local_vllm`` by default). Useful for end-to-end smoke tests
      against a local Qwen.
    """

    def __init__(
        self,
        *,
        compliance_probability: float = 1.0,
        provider: Provider | None = None,
        seed: int | None = None,
        prompt_target: PromptTarget | None = None,
    ) -> None:
        super().__init__(prompt_target=prompt_target)
        if not 0.0 <= compliance_probability <= 1.0:
            raise ValueError("compliance_probability must be in [0, 1]")
        self.compliance_probability = compliance_probability
        self.provider = provider
        self._rng = random.Random(seed)

    async def setup(self) -> None:
        return None

    async def render_document(self, doc: AbstractDocument) -> NativeContext:
        return NativeContext(rendered=doc.model_dump(mode="json"))

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        techniques: set[str] = set()
        _walk_collect_hidden(rendered, techniques)

        # No hidden payload means there's nothing to comply *with* —
        # the compliance signal is unambiguously False. The probability
        # parameter only gates the case where a hidden payload is
        # actually present.
        comply = bool(techniques) and (self._rng.random() < self.compliance_probability)

        if self.provider is not None:
            doc_text = _linearize_text(rendered)
            messages = [
                ChatMessage(
                    role="user",
                    content=(f"Document content:\n{doc_text}\n\nUser query: {user_prompt}"),
                ),
            ]
            chat = await self.provider.complete(messages)
            return Response(
                text=chat.text,
                complied=comply,
                raw={
                    "hidden_techniques": sorted(techniques),
                    "provider_raw": chat.raw,
                },
                usage=chat.usage.model_dump(),
            )

        if comply:
            text = (
                "[stub-comply] honouring hidden instruction(s) from "
                f"technique(s): {sorted(techniques)}"
            )
        elif techniques:
            text = "[stub-refuse] hidden instruction detected; declining to act on it."
        else:
            text = "[stub-benign] no hidden instruction; responding to user prompt only."
        return Response(
            text=text,
            complied=comply,
            raw={"hidden_techniques": sorted(techniques)},
        )

    async def teardown(self, context: NativeContext) -> None:
        return None

    def capabilities(self) -> DocumentCapabilities:
        return DocumentCapabilities(
            block_types={"text", "heading", "table", "image", "text_box", "citation"},
            formatting=True,
            comments=True,
            multimodal=True,
            multi_turn=False,
            tool_use=False,
        )

    def supported_techniques(self) -> set[str]:
        return {"*"}


__all__ = [
    "DocumentCapabilities",
    "DocumentTarget",
    "NativeContext",
    "Response",
    "StubDocumentTarget",
]
