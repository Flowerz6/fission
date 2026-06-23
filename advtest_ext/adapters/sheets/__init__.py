"""Sheets adapter — :class:`SheetsTarget` is a ``DocumentTarget`` that
materialises an :class:`AbstractDocument` as a real Google Sheet,
asks Gemini-for-Workspace to summarise / find totals, and tears the
sheet down afterwards.

See ``TECHNIQUE_COVERAGE.md`` for the per-technique support matrix;
``TableBlock`` is the natural fit and ``Comment`` becomes a cell note,
but Sheets has no analogue for footnotes / suggested edits / etc.
"""

from advtest_ext.adapters.sheets.target import SheetsTarget

__all__ = ["SheetsTarget"]
