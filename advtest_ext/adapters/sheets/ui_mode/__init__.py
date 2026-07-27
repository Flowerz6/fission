"""Multi-turn Google Sheets Gemini side-panel driver (ui_mode).

The Gemini side panel is the SAME component across Workspace apps, so this
package is a focused port of ``google_docs.ui_mode``: it reuses all the
shared ``_ui_mode_common`` infrastructure and the proven side-panel drive,
targeting the Sheets host (``/spreadsheets/d/<id>``) instead of Docs and
dropping the Docs-only canvas body-reading + "Resolve Gemini suggestions"
gate (a Sheets skill-leak conversation reads the panel reply, not a doc body).

* ``selectors`` — the side-panel locator registry (seeded from the Docs
  values, which target the shared ``appsElementsSidekick…`` panel classes;
  re-validate against live Sheets — see ``docs/SHEETS_UI_MODE_RUNBOOK.md``).
* ``invoker`` — ``ui_mode_invoke``: drive one conversational turn.
* ``multi_turn_target`` — ``GeminiSheetsMultiTurnTarget``: the multi-turn
  ``DocumentTarget`` tap_crescendo runs against (registered as
  ``google_sheets_multiturn``).
"""

from advtest_ext.adapters.sheets.ui_mode.invoker import ui_mode_invoke
from advtest_ext.adapters.sheets.ui_mode.multi_turn_target import GeminiSheetsMultiTurnTarget
from advtest_ext.adapters.sheets.ui_mode.selectors import SELECTORS

__all__ = ["SELECTORS", "GeminiSheetsMultiTurnTarget", "ui_mode_invoke"]
