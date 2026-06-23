"""Tests for the docx red-team report loader.

Builds tiny synthetic .docx fixtures inline rather than depending
on the real Google Red Team Report — keeps the test suite
self-contained and lets us assert structure without coupling to
specific report content.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from advtest_ext.core.loaders.docx_loader import load


def _make_docx(tmp_path: Path, paragraphs: list[str], filename: str = "report.docx") -> Path:
    """Build a minimal valid .docx containing the listed paragraphs.

    ``word/document.xml`` is the only required entry — Word will
    complain about a docx with just this file, but our loader
    only reads document.xml so the minimal shape works.
    """
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f'<w:p xmlns:w="{ns}"><w:r><w:t>{_xml_escape(p)}</w:t></w:r></w:p>' for p in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    )

    docx_path = tmp_path / filename
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document_xml)
    return docx_path


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_loads_single_variant(tmp_path: Path) -> None:
    docx = _make_docx(
        tmp_path,
        [
            "V1 - Persona Forgery via Inbox Grounding",
            "OVERVIEW",
            "The attack hijacks identity via persona forgery.",
            "More context follows.",
            "METHODOLOGY",
            "Two accounts created fresh.",
            "FINDINGS",
            "Injection succeeded 100% of trials.",
        ],
    )
    seeds = load(docx)
    assert len(seeds) == 1
    seed = seeds[0]
    assert seed.kind == "human_chat"
    assert seed.source_format == "docx_report"
    assert seed.title.startswith("V1 -")
    assert seed.payload["variant_number"] == 1
    assert seed.payload["variant_title"] == "Persona Forgery via Inbox Grounding"
    sections = seed.payload["sections"]
    assert "overview" in sections
    assert sections["overview"] == [
        "The attack hijacks identity via persona forgery.",
        "More context follows.",
    ]
    assert "methodology" in sections


def test_loads_multiple_variants(tmp_path: Path) -> None:
    docx = _make_docx(
        tmp_path,
        [
            "Front matter",
            "V1 - First",
            "OVERVIEW",
            "first overview",
            "V2 - Second",
            "OVERVIEW",
            "second overview",
            "V3 - Third",
            "METHODOLOGY",
            "third methodology",
        ],
    )
    seeds = load(docx)
    assert len(seeds) == 3
    titles = [s.payload["variant_title"] for s in seeds]
    assert titles == ["First", "Second", "Third"]
    assert seeds[0].payload["sections"]["overview"] == ["first overview"]
    assert seeds[1].payload["sections"]["overview"] == ["second overview"]
    assert seeds[2].payload["sections"]["methodology"] == ["third methodology"]


def test_em_dash_separator_accepted(tmp_path: Path) -> None:
    """Variant headers in real reports use em-dash sometimes."""
    docx = _make_docx(
        tmp_path,
        [
            "V12 — The Self-Falsified Defense",
            "OVERVIEW",
            "...",
        ],
    )
    seeds = load(docx)
    assert len(seeds) == 1
    assert seeds[0].payload["variant_number"] == 12
    assert "Self-Falsified" in seeds[0].title


def test_preamble_paragraphs_captured(tmp_path: Path) -> None:
    """Paragraphs inside a variant but BEFORE the first known
    section header get bucketed under 'preamble' so they're not
    silently dropped."""
    docx = _make_docx(
        tmp_path,
        [
            "V1 - Title",
            "this paragraph has no section header",
            "OVERVIEW",
            "in overview",
        ],
    )
    seeds = load(docx)
    assert seeds[0].payload["sections"]["preamble"] == ["this paragraph has no section header"]


def test_taxonomy_hints_inferred(tmp_path: Path) -> None:
    docx = _make_docx(
        tmp_path,
        [
            "V1 - Persona Forgery via Inbox Grounding",
            "OVERVIEW",
            "Uses indirect prompt injection and handshake tokens.",
        ],
    )
    seeds = load(docx)
    hints = seeds[0].taxonomy_hints
    assert "persona_forgery" in hints
    assert "indirect_prompt_injection" in hints
    assert "handshake_token" in hints


# ----------------------------------------------------------------------
# Error handling
# ----------------------------------------------------------------------


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "no_such_file.docx")


def test_non_zip_raises(tmp_path: Path) -> None:
    not_a_docx = tmp_path / "fake.docx"
    not_a_docx.write_text("this is not a zip")
    with pytest.raises(ValueError, match=r"not a valid \.docx"):
        load(not_a_docx)


def test_missing_document_xml_raises(tmp_path: Path) -> None:
    docx = tmp_path / "no_doc.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("something_else.xml", "irrelevant")
    with pytest.raises(ValueError, match=r"not a valid \.docx"):
        load(docx)


def test_no_variants_raises(tmp_path: Path) -> None:
    """A docx with no variant headers is treated as malformed input."""
    docx = _make_docx(
        tmp_path,
        [
            "Just some text",
            "with no variants",
        ],
    )
    with pytest.raises(ValueError, match="no variant sections"):
        load(docx)


def test_media_attachments_listed(tmp_path: Path) -> None:
    """Image files under word/media/ should be reported in
    payload['attachments']."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{ns}"><w:body>'
        f"<w:p><w:r><w:t>V1 - Test</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>OVERVIEW</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>x</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    docx_path = tmp_path / "with_media.docx"
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/media/image1.png", b"fake-png-bytes")
        zf.writestr("word/media/image2.jpg", b"fake-jpg-bytes")

    seeds = load(docx_path)
    assert seeds[0].payload["attachments"] == [
        "word/media/image1.png",
        "word/media/image2.jpg",
    ]
