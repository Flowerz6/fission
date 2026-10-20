"""Google Sheets operation handlers for the Recipe Runner.

One handler registers here:

- ``("sheets", "create_and_seed")`` — mint a host spreadsheet and populate its
  cells with real content, so a guide's conversation ("review this sheet",
  "summarise the Q3 data") aligns with what's actually on the grid instead of
  driving Gemini against a blank sheet. Returns the new ``spreadsheet_id`` so a
  guide's ``target`` binds to the seeded sheet (via ``bind_setup_target_options``,
  ``google_sheets -> spreadsheet_id/sheet_id``).

Uses the Sheets API (now that all scopes are authorized) for a deterministic,
fast seed; the UI-mode ``sheets_ui_create_and_seed`` remains a no-OAuth fallback.
"""

from __future__ import annotations

from typing import Any

from advtest_ext.adapters._auth import SHEETS_SCOPES, get_service
from advtest_ext.core.recipe_operations import OperationContext, OperationResult

_DAY_URL = "https://docs.google.com/spreadsheets/d/{sid}/edit"


def _coerce_rows(raw: Any) -> list[list[str]]:
    """Accept ``rows`` as a list-of-lists, or ``data`` as newline/comma text."""
    if isinstance(raw, list):
        out: list[list[str]] = []
        for row in raw:
            if isinstance(row, list):
                out.append([("" if c is None else str(c)) for c in row])
            else:
                out.append([str(row)])
        return out
    if isinstance(raw, str) and raw.strip():
        return [[c.strip() for c in line.split(",")] for line in raw.splitlines() if line.strip()]
    return []


async def create_and_seed(ctx: OperationContext) -> OperationResult:
    """Mint a spreadsheet and write cells. Step params::

        params:
          title: "Q3 Regional Revenue"     # optional sheet title
          rows:                              # a 2-D grid written from A1
            - ["Region", "Revenue", "Quarter"]
            - ["EMEA", "412000", "Q3"]
          # or, instead of rows:
          data: "Region,Revenue,Quarter\\nEMEA,412000,Q3"
          start_cell: "A1"                   # optional (default A1)

    Outputs::

        outputs:
          spreadsheet_id: <new sheet id>     # binds to the guide target
          sheet_id: <same id>
          title: <as-created>
          url: https://docs.google.com/spreadsheets/d/<id>/edit
    """
    title = ctx.params.get("title")
    if not isinstance(title, str) or not title:
        title = "Shared Working Sheet"
    rows = _coerce_rows(
        ctx.params.get("rows") if ctx.params.get("rows") is not None else ctx.params.get("data")
    )
    start_cell = ctx.params.get("start_cell")
    start_cell = start_cell if isinstance(start_cell, str) and start_cell else "A1"

    sheets = get_service("sheets", "v4", SHEETS_SCOPES)
    created = sheets.spreadsheets().create(body={"properties": {"title": title}}).execute()
    spreadsheet_id = created["spreadsheetId"]
    if rows:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=start_cell,
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()

    return OperationResult(
        outputs={
            "spreadsheet_id": spreadsheet_id,
            "sheet_id": spreadsheet_id,
            "title": created.get("properties", {}).get("title", title),
            "url": _DAY_URL.format(sid=spreadsheet_id),
        },
        raw={
            "operation": "sheets.create_and_seed",
            # Expose the seeded content so the Recipe Runner can populate
            # SignalContext.document_body without scraping the grid.
            "document_body": "\n".join("\t".join(r) for r in rows),
        },
    )
