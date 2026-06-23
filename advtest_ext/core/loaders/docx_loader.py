"""Loader for Word ``.docx`` red-team reports.

Mirrors the shape of the Google Red Team Report
(``Google red Team report.docx``) — the canonical artefact this
loader was built against on 2026-05-23. The report uses a
predictable section structure repeated per variant::

    V1 - <title>
    OVERVIEW
    <paragraphs>
    METHODOLOGY
    <paragraphs>
    FINDINGS
    <paragraphs>
    RISKS
    <paragraphs>
    PRIMARY RECOMMENDATION
    <paragraphs>
    POC
    <paragraphs + figure captions>

    V2 - <title>
    ...

The loader produces one :class:`Seed` per variant ("V1 - ...",
"V2 - ...", etc.). Each Seed's ``payload`` carries the variant's
sections as a string-keyed dict so the Phase-6 Extractor can
read whichever it needs without re-parsing the docx.

No external dependencies. ``.docx`` files are ZIP archives
containing ``word/document.xml``; we walk that XML with the
standard library and pull paragraph text via the
``w:p`` / ``w:t`` element pattern.

Image attachments under ``word/media/`` are listed by relative
path in ``payload["attachments"]`` so the Extractor can
reference them. The figures-to-captions mapping requires
parsing ``word/_rels/document.xml.rels`` which we skip for v1 —
the captions name the figures explicitly ("Fig 5: ..." inline
in the text), so the Extractor recovers the mapping from
prose without needing the rel graph.
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from advtest_ext.core.seed import Seed

# Word's XML namespace. Hard-coded — every .docx written in the
# last decade uses this URL. Future Office versions may switch
# (none have so far) but the namespace bump would invalidate
# every parser written against the older value, so a flag-day
# update is the right cost shape.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# Section header keywords. The Google Red Team Report uses these
# exact strings (uppercase, one-word or two-word) as section
# titles within each variant. We match case-insensitively to
# survive minor casing drift across future reports.
_KNOWN_SECTIONS: tuple[str, ...] = (
    "OVERVIEW",
    "METHODOLOGY",
    "FINDINGS",
    "RISKS",
    "PRIMARY RECOMMENDATION",
    "POC",
)


# Variant-header pattern. Matches lines like:
#   "V1 - Subscriber to Engineer: Hijacking Identity..."
#   "V12 — The Self-Falsified Defense..."
# Hyphen or em-dash separator; case-insensitive on the V; the
# rest is captured as the title.
_VARIANT_HEADER_RE = re.compile(r"^V(\d+)\s*[-—]\s*(.+)$")


def load(path: Path) -> list[Seed]:
    """Read a red-team docx and return one :class:`Seed` per
    variant section it contains.

    Raises :class:`FileNotFoundError` if the path doesn't exist,
    :class:`ValueError` if the file isn't a valid docx or has no
    extractable variants. Never returns an empty list — an empty
    docx is treated as a malformed input.
    """
    docx_path = Path(path)
    if not docx_path.exists():
        raise FileNotFoundError(f"docx not found: {docx_path}")
    if not docx_path.is_file():
        raise ValueError(f"docx path is not a file: {docx_path}")

    paragraphs = _extract_paragraphs(docx_path)
    attachments = _list_media_attachments(docx_path)
    variants = _split_into_variants(paragraphs)

    if not variants:
        raise ValueError(
            f"no variant sections found in {docx_path}; "
            "expected at least one header matching 'V<number> - <title>'"
        )

    seeds: list[Seed] = []
    submitted_at = datetime.now(UTC)
    for variant_number, title, sections in variants:
        seed_id = f"seed-{docx_path.stem}-v{variant_number}-{uuid.uuid4().hex[:6]}"
        # Strip any path prefix from the title for a clean dashboard
        # label (the dashboard shows ``title``; the variant number
        # is recoverable from the payload).
        seeds.append(
            Seed(
                id=seed_id,
                kind="human_chat",
                title=f"V{variant_number} - {title}",
                source_format="docx_report",
                raw_artefact_path=str(docx_path.resolve()),
                taxonomy_hints=_infer_taxonomy_hints(title, sections),
                submitted_by=f"ingest_job:docx_loader@{submitted_at.isoformat()}",
                submitted_at=submitted_at,
                payload={
                    "variant_number": variant_number,
                    "variant_title": title,
                    "sections": sections,
                    "attachments": attachments,
                },
            )
        )
    return seeds


# ----------------------------------------------------------------------
# Paragraph extraction
# ----------------------------------------------------------------------


def _extract_paragraphs(docx_path: Path) -> list[str]:
    """Pull every non-empty paragraph from ``word/document.xml``.

    Each ``<w:p>`` paragraph element is rendered by concatenating
    its descendant ``<w:t>`` text runs. Blank paragraphs are
    dropped; the leading/trailing whitespace within each paragraph
    is stripped.
    """
    try:
        with zipfile.ZipFile(docx_path) as zf:
            with zf.open("word/document.xml") as fh:
                tree = ET.parse(fh)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"{docx_path} is not a valid .docx (no word/document.xml): {exc}") from exc

    paragraphs: list[str] = []
    for para in tree.getroot().iter(f"{{{_W_NS}}}p"):
        text_runs = [t.text or "" for t in para.iter(f"{{{_W_NS}}}t")]
        line = "".join(text_runs).strip()
        if line:
            paragraphs.append(line)
    return paragraphs


def _list_media_attachments(docx_path: Path) -> list[str]:
    """List image / media files under ``word/media/`` in the docx.

    Returns relative paths within the archive (the operator can
    extract them separately for review). The Extractor doesn't
    decode the images — figure captions in the prose name each
    one explicitly, so the rel-graph lookup isn't needed for v1.
    """
    try:
        with zipfile.ZipFile(docx_path) as zf:
            names = [n for n in zf.namelist() if n.startswith("word/media/")]
    except zipfile.BadZipFile:
        return []
    return sorted(names)


# ----------------------------------------------------------------------
# Variant splitting
# ----------------------------------------------------------------------


def _split_into_variants(
    paragraphs: list[str],
) -> list[tuple[int, str, dict[str, list[str]]]]:
    """Group paragraphs into variant sections.

    Returns a list of ``(variant_number, variant_title,
    sections_dict)`` tuples. ``sections_dict`` keys are the
    section names (``"overview"``, ``"methodology"``, etc. —
    lowercased) and values are the lists of paragraphs in each.

    Paragraphs before the first variant header are discarded
    (cover page, table of contents). Paragraphs in a variant
    that fall before the first known section header are bucketed
    under ``"preamble"`` so they're not lost.
    """
    variants: list[tuple[int, str, dict[str, list[str]]]] = []
    current_number: int | None = None
    current_title: str | None = None
    current_sections: dict[str, list[str]] = {}
    current_section_key: str = "preamble"

    def _flush() -> None:
        if current_number is not None and current_title is not None:
            variants.append((current_number, current_title, current_sections))

    for para in paragraphs:
        # Variant header? Start a new variant bucket.
        variant_match = _VARIANT_HEADER_RE.match(para)
        if variant_match:
            _flush()
            current_number = int(variant_match.group(1))
            current_title = variant_match.group(2).strip()
            current_sections = {}
            current_section_key = "preamble"
            continue

        # Inside a variant — is this a known section header?
        # Match against the uppercase form so headers like
        # ``OVERVIEW`` flip the bucket but a paragraph that
        # merely *contains* the word doesn't.
        section_key = _section_key_for(para)
        if section_key is not None and current_number is not None:
            current_section_key = section_key
            current_sections.setdefault(section_key, [])
            continue

        # Regular content paragraph — append to current section.
        if current_number is not None:
            current_sections.setdefault(current_section_key, []).append(para)

    _flush()
    return variants


def _section_key_for(paragraph: str) -> str | None:
    """Return the lowercase key for ``paragraph`` if it exactly
    matches a known section header. None otherwise.

    The Google Red Team Report's section headers stand alone on
    a line (no leading numbering, no surrounding punctuation).
    Exact-match on the uppercase form catches them without
    false-positiving on prose that happens to mention the word.
    """
    upper = paragraph.strip().upper()
    if upper in _KNOWN_SECTIONS:
        return upper.lower().replace(" ", "_")
    return None


# ----------------------------------------------------------------------
# Taxonomy hints
# ----------------------------------------------------------------------


# Hint patterns. Each entry is ``(taxonomy_tag, list of substrings
# that mark this tag in title/methodology text)``. Matched
# case-insensitively. Used only as a prior for the Extractor —
# wrong / missed hints don't break anything, they just give the
# Extractor a slightly weaker starting point.
_TAXONOMY_HINT_PATTERNS: list[tuple[str, list[str]]] = [
    ("persona_forgery", ["persona forgery", "roleplay", "identity hijack"]),
    ("indirect_prompt_injection", ["indirect prompt injection", "ipi", "inbox grounding"]),
    ("tool_disclosure", ["tool registry", "tool manifest", "schema disclosure"]),
    ("reasoning_leak", ["reasoning disclosure", "system prompt", "chain of thought"]),
    ("architecture_disclosure", ["pipeline component", "instruction refinement", "architecture"]),
    ("handshake_token", ["handshake", "compound prompt"]),
    ("cross_account", ["cross-account", "two accounts", "account b"]),
]


def _infer_taxonomy_hints(
    title: str,
    sections: dict[str, list[str]],
) -> list[str]:
    """Best-effort taxonomy-tag inference from the variant title +
    OVERVIEW text. Returns a stable-ordered list of distinct tags.

    Conservative — only emits a tag when at least one of its
    patterns is a substring of the searched text. The Extractor
    re-derives final taxonomy from the full prose; these hints
    just bias it toward common patterns the loader can recognise
    mechanically.
    """
    haystack = title.lower()
    for paragraph in sections.get("overview", []):
        haystack += " " + paragraph.lower()
    found: list[str] = []
    for tag, patterns in _TAXONOMY_HINT_PATTERNS:
        if any(p in haystack for p in patterns):
            found.append(tag)
    return found
