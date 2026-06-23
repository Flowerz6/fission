"""``AbstractDocument`` — the target-format-independent representation
that flows through advtest's pipeline.

The same instance can be rendered to a Google Doc, a Gmail message, or
a Sheets file by different ``DocumentTarget`` implementations. Hiding
techniques attach ``HiddenContent`` markers to blocks or sub-elements;
the rendering layer is responsible for translating those markers into
the format-native trick (zero-width chars, tracked changes, white
text, etc.).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from advtest_ext.document.blocks import Block
from advtest_ext.document.elements import (
    Comment,
    Footnote,
    HeaderFooter,
    Link,
    SuggestedEdit,
)


class AbstractDocument(BaseModel):
    """Target-format-independent document model.

    All fields default to empty so a minimal valid document just sets
    ``id`` and ``title``. JSON serialisation/deserialisation must be
    lossless — the discriminated ``Block`` union and the
    ``HiddenContent`` markers all round-trip; this is the contract the
    replay-bundle infrastructure relies on.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    body_blocks: list[Block] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    comments: list[Comment] = Field(default_factory=list)
    footnotes: list[Footnote] = Field(default_factory=list)
    headers_footers: HeaderFooter = Field(default_factory=HeaderFooter)
    suggested_edits: list[SuggestedEdit] = Field(default_factory=list)
    attached_links: list[Link] = Field(default_factory=list)


__all__ = ["AbstractDocument"]
