"""Tiny BibTeX parser for report citations (formerly the /references page).

Pulls just enough out of ``references.bib`` to render a table:
citation key, title, author, year, optional URL / note. We
deliberately don't depend on a heavyweight bibtex library — the
fields we need are uniform across the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Repo root holds references.bib next to FOUNDATIONS.md / README.md.
_DEFAULT_BIB_PATH = Path(__file__).resolve().parents[2] / "references.bib"


@dataclass(frozen=True)
class BibEntry:
    """One bibtex record stripped to the dashboard-relevant fields."""

    citation_key: str
    entry_type: str
    title: str
    author: str = ""
    year: str = ""
    url: str = ""
    note: str = ""


_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.DOTALL)
_FIELD_RE = re.compile(r"(\w+)\s*=\s*[\"\{](.*?)[\"\}]\s*[,\n]", re.DOTALL)


def parse_bibfile(path: Path | None = None) -> list[BibEntry]:
    """Return every @-record in ``path`` (or the project's
    references.bib by default), one ``BibEntry`` each. Multi-line
    fields and brace-balanced titles are normalised by collapsing
    whitespace runs."""
    target = path or _DEFAULT_BIB_PATH
    if not target.exists():
        return []
    raw = target.read_text(encoding="utf-8")
    entries: list[BibEntry] = []
    for match in _ENTRY_RE.finditer(raw):
        entry_type, key, body = match.group(1), match.group(2), match.group(3)
        # Trailing-comma tolerance: append a sentinel newline so the
        # last field's closing comma/newline isn't required.
        body = body + "\n"
        fields: dict[str, str] = {}
        for fm in _FIELD_RE.finditer(body):
            name = fm.group(1).lower()
            value = re.sub(r"\s+", " ", fm.group(2).strip())
            fields[name] = value
        entries.append(
            BibEntry(
                citation_key=key,
                entry_type=entry_type,
                title=fields.get("title", "(untitled)"),
                author=fields.get("author", ""),
                year=fields.get("year", ""),
                url=fields.get("url", ""),
                note=fields.get("note", ""),
            )
        )
    entries.sort(key=lambda e: e.citation_key)
    return entries


__all__ = ["BibEntry", "parse_bibfile"]
