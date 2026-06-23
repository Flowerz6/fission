"""Block variants and shared sub-models for AbstractDocument.

Every block is a Pydantic v2 model with a literal ``type`` field; the
exported ``Block`` alias is a discriminated union so that
``AbstractDocument.body_blocks`` round-trips through JSON without
losing subclass identity.

``HiddenContent`` can be attached to any block (or sub-element such
as a comment, footnote, suggested edit) to mark a payload that the
target should "see but a human reader should not." The marker is the
contract that ties a rendered document back to the technique +
payload that produced it, which is what the replay-bundle layer
relies on. See FOUNDATIONS.md ("Hiding techniques") for the catalogue
of techniques and their citations; ``HiddenContent.references`` should
carry the matching keys from ``references.bib``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class HiddenContent(BaseModel):
    """Marker placed on the carrier of a hidden payload.

    ``technique_id`` names the hiding technique (e.g.
    ``"zero_width_unicode"``, ``"white_text"``,
    ``"markdown_image_exfil"``). ``payload_id`` lets the replay layer
    reconstruct the exact bytes injected. ``references`` lists bib
    keys (from ``references.bib``) that justify the technique — every
    hiding technique must record at least one citation per the
    research-grounding rules in FOUNDATIONS.md.
    """

    model_config = ConfigDict(extra="forbid")

    technique_id: str
    payload_id: str
    references: list[str] = Field(default_factory=list)


class Position(BaseModel):
    """Layout coordinates used by ``Formatting.position`` and
    ``TextBoxBlock``. ``off_page`` flags content positioned outside
    the visible page area — a common hiding technique on Docs."""

    model_config = ConfigDict(extra="forbid")

    x: float = 0.0
    y: float = 0.0
    page: int = 0
    off_page: bool = False


class Formatting(BaseModel):
    """Per-block visual formatting. Hidden-by-formatting payloads
    (white text, tiny font, off-page placement) are expressed by
    setting these fields rather than by mutating the text — this lets
    a single rendering pass apply the technique consistently across
    block types."""

    model_config = ConfigDict(extra="forbid")

    color: str | None = None
    font_size: float | None = None
    font_family: str | None = None
    hidden: bool = False
    position: Position | None = None
    language: str | None = None


class _BlockBase(BaseModel):
    """Fields shared by every block variant. Not exported on its own."""

    model_config = ConfigDict(extra="forbid")

    block_id: str | None = None
    formatting: Formatting | None = None
    hidden: HiddenContent | None = None


class TextBlock(_BlockBase):
    type: Literal["text"] = "text"
    text: str


class HeadingBlock(_BlockBase):
    type: Literal["heading"] = "heading"
    text: str
    level: int = 1


class TableCell(BaseModel):
    """A cell in a ``TableBlock``. Cells can carry their own
    ``HiddenContent`` so that techniques targeting cell contents
    (e.g. zero-width chars in a single Sheets cell) survive a
    round-trip."""

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    formatting: Formatting | None = None
    hidden: HiddenContent | None = None


class TableBlock(_BlockBase):
    type: Literal["table"] = "table"
    rows: list[list[TableCell]] = Field(default_factory=list)


class ImageBlock(_BlockBase):
    """Image with alt text and caption — the alt-text channel is the
    common Workspace exfiltration vector (Slack-AI / GeminiJack
    chain)."""

    type: Literal["image"] = "image"
    src: str | None = None
    alt_text: str = ""
    caption: str | None = None


class TextBoxBlock(_BlockBase):
    """Free-floating text box, often used for off-page hiding."""

    type: Literal["text_box"] = "text_box"
    text: str
    position: Position | None = None


class CitationBlock(_BlockBase):
    type: Literal["citation"] = "citation"
    text: str
    source: str | None = None


Block = Annotated[
    TextBlock | HeadingBlock | TableBlock | ImageBlock | TextBoxBlock | CitationBlock,
    Field(discriminator="type"),
]
"""Discriminated union over the concrete block types. Use this as the
type annotation anywhere a generic block is accepted, so that JSON
deserialisation restores the correct subclass."""


__all__ = [
    "Block",
    "CitationBlock",
    "Formatting",
    "HeadingBlock",
    "HiddenContent",
    "ImageBlock",
    "Position",
    "TableBlock",
    "TableCell",
    "TextBlock",
    "TextBoxBlock",
]
