"""Multi-turn Google Calendar Gemini side-panel driver (ui_mode).

The Gemini side panel is the SAME component across Workspace apps, so this
package is a focused port of ``sheets.ui_mode``: it reuses all the shared
``_ui_mode_common`` infrastructure and the proven side-panel drive, targeting
the Calendar host (``calendar.google.com``) instead of Sheets and keeping the
Docs-only canvas body-reading + "Resolve Gemini suggestions" gate dropped (a
Calendar day-summary conversation reads the panel reply, not an event body).

* ``selectors`` — the side-panel locator registry (seeded from the Docs /
  Sheets values, which target the shared ``appsElementsSidekick…`` panel
  classes; re-validate against live Calendar — the entry button and the
  event-open / calendar-ready gate are the likely divergences).
* ``invoker`` — ``ui_mode_invoke``: drive one conversational turn.
* ``multi_turn_target`` — ``GeminiCalendarMultiTurnTarget``: the multi-turn
  ``DocumentTarget`` tap_crescendo runs against (registered as
  ``google_calendar_multiturn``).
"""

from advtest_ext.adapters.calendar.ui_mode.invoker import ui_mode_invoke
from advtest_ext.adapters.calendar.ui_mode.multi_turn_target import (
    GeminiCalendarMultiTurnTarget,
)
from advtest_ext.adapters.calendar.ui_mode.selectors import SELECTORS

__all__ = ["SELECTORS", "GeminiCalendarMultiTurnTarget", "ui_mode_invoke"]
