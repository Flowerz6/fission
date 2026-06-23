"""Tests for the AbstractDocument model: structure, defaults, and
lossless JSON round-trip incl. discriminated-union restoration.

The round-trip contract is what the replay-bundle layer relies on, so
this test set is deliberately strict (== equality on the re-validated
instance, plus runtime isinstance checks on every block variant)."""

from __future__ import annotations

from advtest_ext.document import (
    AbstractDocument,
    CitationBlock,
    Comment,
    Footnote,
    Formatting,
    HeaderFooter,
    HeadingBlock,
    HiddenContent,
    ImageBlock,
    Link,
    Position,
    Reply,
    SuggestedEdit,
    TableBlock,
    TableCell,
    TextBlock,
    TextBoxBlock,
)


def _full_document() -> AbstractDocument:
    """A document that exercises every block type, every sub-element,
    and a HiddenContent marker on each carrier site (block, cell,
    comment, reply, footnote, suggested edit, link)."""
    return AbstractDocument(
        id="doc-001",
        title="Mixed-content fixture",
        body_blocks=[
            HeadingBlock(text="Quarterly review", level=1),
            TextBlock(
                text="Visible body text.",
                formatting=Formatting(color="#000000", language="en"),
                hidden=HiddenContent(
                    technique_id="zero_width_unicode",
                    payload_id="p-001",
                    references=["boucher2022badchars"],
                ),
            ),
            TableBlock(
                rows=[
                    [
                        TableCell(text="A1"),
                        TableCell(
                            text="A2",
                            hidden=HiddenContent(
                                technique_id="cell_zwsp",
                                payload_id="p-002",
                                references=["boucher2022badchars"],
                            ),
                        ),
                    ],
                    [TableCell(text="B1"), TableCell(text="B2")],
                ],
            ),
            ImageBlock(
                src="https://example.invalid/x.png",
                alt_text="benign-looking alt",
                caption="caption",
                hidden=HiddenContent(
                    technique_id="markdown_image_exfil",
                    payload_id="p-003",
                    references=["slackai_promptarmor_2024", "geminijack_2025"],
                ),
            ),
            TextBoxBlock(
                text="Off-page payload",
                position=Position(x=-1000.0, y=-1000.0, page=1, off_page=True),
                formatting=Formatting(hidden=True),
                hidden=HiddenContent(
                    technique_id="off_page_text_box",
                    payload_id="p-004",
                    references=["geminijack_2025"],
                ),
            ),
            CitationBlock(text="Greshake et al. 2023", source="arXiv:2302.12173"),
        ],
        metadata={"author": "alice", "version": 3},
        comments=[
            Comment(
                comment_id="c1",
                author="alice",
                anchor="block:b1",
                body="Top-level comment",
                replies=[
                    Reply(
                        author="bob",
                        body="Reply with hidden payload",
                        hidden=HiddenContent(
                            technique_id="comment_injection",
                            payload_id="p-005",
                            references=["promptware_nassi2025"],
                        ),
                    ),
                ],
            ),
        ],
        footnotes=[
            Footnote(
                anchor="block:b2",
                body="footnote body",
                hidden=HiddenContent(
                    technique_id="footnote_injection",
                    payload_id="p-006",
                    references=["promptware_nassi2025"],
                ),
            ),
        ],
        headers_footers=HeaderFooter(
            header_blocks=[TextBlock(text="confidential")],
            footer_blocks=[TextBlock(text="page 1")],
        ),
        suggested_edits=[
            SuggestedEdit(
                author="mallory",
                anchor="block:b1",
                original_text="Visible body text.",
                replacement_text="Visible body text. <hidden>",
                hidden=HiddenContent(
                    technique_id="suggested_edit_injection",
                    payload_id="p-007",
                    references=["promptware_nassi2025"],
                ),
            ),
        ],
        attached_links=[
            Link(
                url="https://example.invalid/exfil?x=",
                anchor_text="report",
                link_type="external",
                hidden=HiddenContent(
                    technique_id="link_exfil",
                    payload_id="p-008",
                    references=["slackai_promptarmor_2024"],
                ),
            ),
        ],
    )


def test_minimal_document_defaults_are_empty() -> None:
    doc = AbstractDocument(id="d", title="t")
    assert doc.body_blocks == []
    assert doc.comments == []
    assert doc.footnotes == []
    assert doc.suggested_edits == []
    assert doc.attached_links == []
    assert doc.headers_footers.header_blocks == []
    assert doc.headers_footers.footer_blocks == []
    assert doc.metadata == {}


def test_full_document_json_roundtrip() -> None:
    doc = _full_document()
    payload = doc.model_dump_json()
    restored = AbstractDocument.model_validate_json(payload)
    assert restored == doc


def test_block_discriminator_restores_subclass_identity() -> None:
    """``body_blocks`` is a discriminated union; after JSON round-trip
    each block must be an instance of its specific subclass, not the
    generic ``BaseModel``."""
    doc = _full_document()
    restored = AbstractDocument.model_validate_json(doc.model_dump_json())
    expected_types = [
        HeadingBlock,
        TextBlock,
        TableBlock,
        ImageBlock,
        TextBoxBlock,
        CitationBlock,
    ]
    for block, cls in zip(restored.body_blocks, expected_types, strict=True):
        assert isinstance(block, cls), f"expected {cls.__name__}, got {type(block).__name__}"


def test_hidden_markers_survive_roundtrip() -> None:
    doc = _full_document()
    restored = AbstractDocument.model_validate_json(doc.model_dump_json())

    text_block = restored.body_blocks[1]
    assert isinstance(text_block, TextBlock)
    assert text_block.hidden is not None
    assert text_block.hidden.technique_id == "zero_width_unicode"
    assert text_block.hidden.references == ["boucher2022badchars"]

    table_block = restored.body_blocks[2]
    assert isinstance(table_block, TableBlock)
    cell = table_block.rows[0][1]
    assert cell.hidden is not None
    assert cell.hidden.technique_id == "cell_zwsp"

    reply = restored.comments[0].replies[0]
    assert reply.hidden is not None
    assert reply.hidden.technique_id == "comment_injection"


def test_extra_fields_rejected_on_document() -> None:
    """``extra="forbid"`` keeps schema drift loud."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AbstractDocument.model_validate({"id": "d", "title": "t", "bogus": 1})


def test_invalid_compliance_probability() -> None:
    """Just here to keep the import surface honest — the value lives
    on StubDocumentTarget; checked properly there."""
    import pytest

    from advtest_ext.document import StubDocumentTarget

    with pytest.raises(ValueError, match="compliance_probability"):
        StubDocumentTarget(compliance_probability=1.5)
