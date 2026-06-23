"""Hiding-technique implementations for indirect injection.

This is the most load-bearing module in advtest. The ``HidingTechniqueImpl``
ABC + 25 concrete techniques in :mod:`techniques` are the
"document-format-specific techniques" pitch (FOUNDATIONS.md "open
problems" #3) and the wedge of the demo heatmap (#2). Every technique
must:

* Carry the right citation key(s) (Greshake 2023, Boucher 2022,
  Promptware, GeminiJack, BoN, InjecAgent, Slack-AI / PromptArmor)
  so the Prompt-4 PDF builder can cite each cell of the heatmap
  faithfully.
* Set ``attack_class`` to one of the six values in
  :data:`advtest_ext.core.models.AttackClass` (zero_width / white_text
  / structural / encoded / role_mimicry / rendering_exfil) so the
  pitch deck can group results by mechanism.
* Set ``requires_capability`` so the orchestrator skips techniques
  the target can't render (e.g. a target with ``tool_use=False``
  shouldn't be asked to run ``tool_format_mimicry``).
* Embed a ``HiddenContent`` marker on the carrier element so the
  replay layer can reconstruct the attempt from the rendered doc
  alone.
"""

from __future__ import annotations

import base64
import codecs
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from advtest_ext.core.models import AttackClass, HidingTechnique
from advtest_ext.document.blocks import HiddenContent

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from advtest_ext.core.models import Payload
    from advtest_ext.document import AbstractDocument
    from advtest_ext.document.target import DocumentCapabilities


# ----------------------------------------------------------------------
# Encoder helpers — used by techniques that mangle the payload text.
# ----------------------------------------------------------------------

ZWSP = "​"  # ZERO WIDTH SPACE
ZWNJ = "‌"  # ZERO WIDTH NON-JOINER
ZWJ = "‍"  # ZERO WIDTH JOINER
TAG_BLOCK_START = 0xE0000  # Unicode Tags block (E0000-E007F)


def encode_zero_width(text: str) -> str:
    """Surround each char of ``text`` with ZWSP, so a casual reader
    sees only the bare characters but a copy-paste / model token
    stream reveals the alternation. Demonstrative-grade hiding;
    Boucher 2022 has stronger constructions."""
    return "".join(f"{ZWSP}{ch}{ZWSP}" for ch in text)


def encode_unicode_tag(text: str) -> str:
    """Encode ASCII chars as their Unicode tag-block equivalents
    (U+E0000 to U+E007F). Renders to nothing on most surfaces; tokenisers
    pass them through. Boucher 2022."""
    return "".join(chr(TAG_BLOCK_START + ord(ch)) for ch in text if ord(ch) < 0x80)


def encode_base64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def encode_rot13(text: str) -> str:
    return codecs.encode(text, "rot_13")


def encode_leetspeak(text: str) -> str:
    table = str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7"})
    return text.translate(table)


# Cyrillic homoglyphs for Latin chars commonly seen in attacker URLs.
# Confusable letters are intentional - the whole point of the
# technique. Using \u escapes for the values both to bypass ruff's
# ambiguous-character warnings and to make the codepoints obvious.
_HOMOGLYPHS = {
    "a": "а",  # noqa: RUF001 — Cyrillic A intentional
    "c": "с",  # noqa: RUF001 — Cyrillic ES intentional
    "e": "е",  # noqa: RUF001 — Cyrillic IE intentional
    "o": "о",  # noqa: RUF001 — Cyrillic O intentional
    "p": "р",  # noqa: RUF001 — Cyrillic ER intentional
    "x": "х",  # noqa: RUF001 — Cyrillic HA intentional
}


def encode_homoglyph(text: str) -> str:
    """Substitute Latin chars with visually-identical Cyrillic
    counterparts; URL filters that don't IDN-normalise will miss the
    swap. Boucher 2022."""
    return "".join(_HOMOGLYPHS.get(ch, ch) for ch in text)


# ----------------------------------------------------------------------
# Capability gating
# ----------------------------------------------------------------------


def technique_supported(
    technique: HidingTechniqueImpl,
    capabilities: DocumentCapabilities,
) -> bool:
    """Does ``capabilities`` cover everything ``technique`` requires?

    Capability tags supported:

    * Bare flag names (``"formatting"``, ``"comments"``, ``"multimodal"``,
      ``"multi_turn"``, ``"tool_use"``) map to the boolean fields on
      :class:`DocumentCapabilities`.
    * ``"block:<type>"`` requires ``<type>`` to be in
      :attr:`DocumentCapabilities.block_types`.

    Unknown tags fail closed: if the orchestrator can't tell whether
    a capability is met, it skips the technique rather than
    pretending. Future-proofing for new capability dimensions.
    """
    bool_caps: dict[str, bool] = {
        "formatting": capabilities.formatting,
        "comments": capabilities.comments,
        "multimodal": capabilities.multimodal,
        "multi_turn": capabilities.multi_turn,
        "tool_use": capabilities.tool_use,
    }
    for req in technique.requires_capability:
        if req.startswith("block:"):
            if req[len("block:") :] not in capabilities.block_types:
                return False
            continue
        flag = bool_caps.get(req)
        if flag is None:
            return False  # unknown cap → fail closed
        if not flag:
            return False
    return True


# ----------------------------------------------------------------------
# Abstract base
# ----------------------------------------------------------------------


class HidingTechniqueImpl(ABC):
    """One indirect-injection hiding technique.

    Concrete subclasses set the seven class-level metadata attributes
    and implement :meth:`apply`. ``references`` may be empty for
    synthesised contributions ("no published citation"); when
    non-empty, every key must resolve in ``references.bib`` (test
    ``test_indirect_injection.py::test_technique_references_resolve``
    enforces this).
    """

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    references: ClassVar[list[str]] = []
    attack_class: ClassVar[AttackClass] = "structural"
    applies_to: ClassVar[list[str]] = []
    requires_capability: ClassVar[list[str]] = []

    def __init__(self) -> None:
        if not self.id:
            raise ValueError(f"{type(self).__name__} must set a non-empty `id`")
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set a non-empty `name`")

    @abstractmethod
    def apply(
        self,
        document: AbstractDocument,
        payload: Payload,
    ) -> AbstractDocument:
        """Embed ``payload`` into a deep-copy of ``document`` using
        this technique. Must place a :class:`HiddenContent` marker on
        whichever carrier element it touches so the replay layer can
        reconstruct the attempt from the rendered doc alone."""

    def descriptor(self) -> HidingTechnique:
        """Return the Pydantic descriptor for this technique. Used by
        :class:`AttackAttempt.hiding_technique`."""
        return HidingTechnique(
            id=self.id,
            name=self.name,
            description=self.description,
            references=list(self.references),
            attack_class=self.attack_class,
            applies_to=list(self.applies_to),
            requires_capability=list(self.requires_capability),
        )

    def _marker(self, payload: Payload) -> HiddenContent:
        """Build the standard ``HiddenContent`` marker for this
        technique + payload pair."""
        return HiddenContent(
            technique_id=self.id,
            payload_id=payload.id,
            references=list(self.references),
        )


__all__ = [
    "TAG_BLOCK_START",
    "ZWJ",
    "ZWNJ",
    "ZWSP",
    "HidingTechniqueImpl",
    "encode_base64",
    "encode_homoglyph",
    "encode_leetspeak",
    "encode_rot13",
    "encode_unicode_tag",
    "encode_zero_width",
    "technique_supported",
]
