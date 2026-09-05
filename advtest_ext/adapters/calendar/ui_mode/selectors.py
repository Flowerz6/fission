"""Side-panel CSS / role selectors for the Calendar Gemini side panel.

WARNING — LIVE VALIDATION REQUIRED.
==================================
These are SEEDED from the working Google Docs / Sheets registry because
the Gemini side panel is the same component across Workspace apps (the
``[role="complementary"][aria-label="Gemini"]`` region and the
``appsElementsSidekick…`` classes are shared). Most should carry over
verbatim; the ones most likely to differ on Calendar are:

  * ``side_panel_button`` — the toolbar entry-point that opens the panel.
    The label/toolbar differs from Docs/Sheets; this is the first to
    re-validate.
  * the event-open / calendar-ready gate — Calendar streams the grid in
    after load and (for the deep-link journey) opens an event detail
    popover. There is NO dedicated selector key for this readiness gate
    (the shared registry has none), so if the panel opens before the
    grid is ready you must heal ``side_panel_button`` /
    ``side_panel_container`` against live Calendar.
  * ``side_panel_container`` accessible name (if Calendar localises it).

**Before running against live Calendar:** open Calendar, DevTools-inspect
the Gemini panel, confirm each selector, and either edit the values here
or run ``scripts/heal_selectors.py`` (heals into the ``google_calendar``
overlay without a code edit). See ``docs/SHEETS_UI_MODE_RUNBOOK.md`` for
the equivalent Sheets procedure.

We prefer ARIA role / accessible-name selectors over raw CSS because they
survive minor markup changes.
"""

from __future__ import annotations

from typing import cast

from advtest_ext.adapters._ui_mode_common.selector_overlay import resolve_selectors
from advtest_ext.adapters.google_docs.ui_mode.selectors import (
    EXPECTED_KEYS,
    SidePanelSelectors,
)

# Seeded from the Docs registry — the shared Gemini-panel selectors. The panel
# container and every child selector are scoped under the Gemini complementary
# region, which is identical across apps; only the toolbar entry button is a
# likely Calendar-specific divergence (see the module warning).
_BASE_SELECTORS: SidePanelSelectors = {
    "side_panel_button": (
        # Calendar toolbar entry-point. Docs uses aria-label "Ask Gemini"; the
        # Calendar label may differ — broaden to
        # button[aria-label*="Gemini" i] on drift. RE-VALIDATE FIRST.
        'button[aria-label="Ask Gemini"], button[aria-label*="Gemini" i]'
    ),
    "side_panel_container": '[role="complementary"][aria-label="Gemini"]',
    "side_panel_input": ('[role="complementary"][aria-label="Gemini"] [contenteditable="true"]'),
    "side_panel_response": (
        '[role="complementary"][aria-label="Gemini"] .appsElementsSidekickAgentMessageBubbleContent'
    ),
    "side_panel_loading_indicator": (
        '[role="complementary"][aria-label="Gemini"] [class*="appsElementsSidekickThinkingState"]'
    ),
    "side_panel_response_complete": (
        '[role="complementary"][aria-label="Gemini"] '
        '[role="listitem"]:last-of-type '
        ".appsElementsSidekickResponseOptionsActionBarContainer"
    ),
    "side_panel_new_conversation": (
        '[role="complementary"][aria-label="Gemini"] '
        'button[aria-label*="New conversation" i], '
        '[role="complementary"][aria-label="Gemini"] '
        'button:has-text("New conversation")'
    ),
    "side_panel_close": (
        '[role="complementary"][aria-label="Gemini"] button[aria-label*="Close" i]'
    ),
    "side_panel_send": (
        '[role="complementary"][aria-label="Gemini"] '
        'button[aria-label*="Send" i]:not([disabled]):not([aria-disabled="true"])'
    ),
}


# Heals for Calendar are stored under a SEPARATE overlay key
# ("google_calendar") so a Docs / Sheets heal and a Calendar heal never
# overwrite each other.
SELECTORS: SidePanelSelectors = cast(
    "SidePanelSelectors", resolve_selectors(_BASE_SELECTORS, "google_calendar")
)

__all__ = ["EXPECTED_KEYS", "SELECTORS", "SidePanelSelectors"]
