"""Document sub-elements: comments, footnotes, headers/footers,
suggested edits, attached links.

These mirror the parts of a Workspace document that are *not* in the
main body but still get fed to the AI as context. Promptware
(promptware_nassi2025) and GeminiJack (geminijack_2025) demonstrate
that hiding payloads in any of these sub-elements is a viable
indirect-prompt-injection vector, so each element here can carry a
``HiddenContent`` marker.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from advtest_ext.document.blocks import Block, HiddenContent


class Reply(BaseModel):
    """A single reply within a ``Comment`` thread. Kept separate from
    ``Comment`` so the schema is one level deep — replies-of-replies
    are not modelled (Workspace doesn't really do them either)."""

    model_config = ConfigDict(extra="forbid")

    author: str
    body: str
    hidden: HiddenContent | None = None


class Comment(BaseModel):
    """Inline comment with author, anchor (typically a ``block_id`` or
    a text range expressed by the rendering layer), body, and a flat
    thread of replies."""

    model_config = ConfigDict(extra="forbid")

    comment_id: str | None = None
    author: str
    anchor: str
    body: str
    replies: list[Reply] = Field(default_factory=list)
    hidden: HiddenContent | None = None


class Footnote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    footnote_id: str | None = None
    anchor: str
    body: str
    hidden: HiddenContent | None = None


class HeaderFooter(BaseModel):
    """Page headers and footers. Each is a sequence of blocks so the
    same Block discriminated union covers them."""

    model_config = ConfigDict(extra="forbid")

    header_blocks: list[Block] = Field(default_factory=list)
    footer_blocks: list[Block] = Field(default_factory=list)


class SuggestedEdit(BaseModel):
    """A "suggested edit" / tracked change. ``accepted`` is None while
    pending; True once accepted, False once rejected."""

    model_config = ConfigDict(extra="forbid")

    edit_id: str | None = None
    author: str
    anchor: str
    original_text: str
    replacement_text: str
    accepted: bool | None = None
    hidden: HiddenContent | None = None


class Link(BaseModel):
    """An attached link. ``link_type`` distinguishes external URLs from
    cross-document links (Promptware-style) and image references (the
    Slack-AI exfil chain)."""

    model_config = ConfigDict(extra="forbid")

    url: str
    anchor_text: str = ""
    link_type: str = "external"
    hidden: HiddenContent | None = None


__all__ = [
    "Comment",
    "Footnote",
    "HeaderFooter",
    "Link",
    "Reply",
    "SuggestedEdit",
]
