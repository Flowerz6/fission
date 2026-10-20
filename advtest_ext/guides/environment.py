"""Per-node environment alignment.

When a guide's branches mutate the *pretext* (qa-evaluation → incident-response →
internal-audit …), the host artifact the target reads should change with it, so
the attacker's framing and what's actually on screen stay consistent. This
module re-seeds the already-staged host artifact (the sheet/doc created in
staging) with the content that matches a node's pretext cell, via the Workspace
APIs (now authorized for every scope).

It is OPT-IN and backward compatible: a guide with no ``environment_variants`` never
builds a seeder, and the run is byte-for-byte unchanged. Surfaces are added by
extending ``_WRITERS``; sheets and docs ship here, others fall through to a no-op
with a logged warning rather than failing a run.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _coerce_rows(raw: Any) -> list[list[str]]:
    if isinstance(raw, list):
        out: list[list[str]] = []
        for row in raw:
            out.append(
                [("" if c is None else str(c)) for c in row]
                if isinstance(row, list)
                else [str(row)]
            )
        return out
    if isinstance(raw, str) and raw.strip():
        return [[c.strip() for c in line.split(",")] for line in raw.splitlines() if line.strip()]
    return []


def _write_sheet(artifact_id: str, content: dict[str, Any]) -> None:
    """Clear the grid and write the variant's rows (Sheets API)."""
    from advtest_ext.adapters._auth import SHEETS_SCOPES, get_service

    rows = _coerce_rows(
        content.get("rows") if content.get("rows") is not None else content.get("data")
    )
    sheets = get_service("sheets", "v4", SHEETS_SCOPES)
    sheets.spreadsheets().values().clear(
        spreadsheetId=artifact_id, range="A1:ZZ5000", body={}
    ).execute()
    if rows:
        sheets.spreadsheets().values().update(
            spreadsheetId=artifact_id,
            range="A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()


def _write_doc(artifact_id: str, content: dict[str, Any]) -> None:
    """Replace the document body with the variant's text (Docs API)."""
    from advtest_ext.adapters._auth import DOCS_SCOPES, get_service

    body_text = str(content.get("body") or content.get("data") or "")
    docs = get_service("docs", "v1", DOCS_SCOPES)
    doc = docs.documents().get(documentId=artifact_id).execute()
    end = 1
    for el in doc.get("body", {}).get("content", []):
        if "endIndex" in el:
            end = max(end, el["endIndex"])
    requests: list[dict[str, Any]] = []
    if end > 2:  # there is existing body text to clear (keep the final newline)
        requests.append({"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}})
    if body_text:
        requests.append({"insertText": {"location": {"index": 1}, "text": body_text}})
    if requests:
        docs.documents().batchUpdate(documentId=artifact_id, body={"requests": requests}).execute()


# surface (short App name) -> synchronous writer(artifact_id, content)
_WRITERS = {
    "sheets": _write_sheet,
    "google_sheets": _write_sheet,
    "docs": _write_doc,
    "google_docs": _write_doc,
}


class EnvironmentSeeder:
    """Re-seeds a staged host artifact to match a node's pretext cell.

    ``variants`` maps a pretext cell value (e.g. ``"incident-response"``) to a
    content dict (``{"rows": [...]}`` for sheets, ``{"body": "..."}`` for docs).
    """

    def __init__(self, *, app: str, artifact_id: str, variants: dict[str, dict[str, Any]]) -> None:
        self._app = app
        self._artifact_id = artifact_id
        self._variants = variants or {}
        self._writer = _WRITERS.get(app)
        self._last_cell: str | None = None

    def has_variant(self, cell_value: str | None) -> bool:
        return bool(cell_value) and cell_value in self._variants

    async def seed_for_cell(self, cell_value: str | None) -> bool:
        """Write the environment for ``cell_value`` if a variant exists and it
        changed since the last node. Returns True when a re-seed happened.
        Best-effort: a write failure logs and returns False, never raises into
        the search loop."""
        if not cell_value or cell_value not in self._variants or cell_value == self._last_cell:
            return False
        if self._writer is None:
            logger.warning("environment.no_writer", app=self._app, cell=cell_value)
            return False
        content = self._variants[cell_value]
        try:
            await asyncio.to_thread(self._writer, self._artifact_id, content)
            self._last_cell = cell_value
            logger.info("environment.reseeded", app=self._app, cell=cell_value)
            return True
        except Exception as exc:
            logger.warning(
                "environment.reseed_failed", app=self._app, cell=cell_value, err=str(exc)[:120]
            )
            return False


__all__ = ["EnvironmentSeeder"]
