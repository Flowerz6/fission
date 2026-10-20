"""Side-panel CSS / role selectors for the Sheets Gemini side panel.

WARNING — LIVE VALIDATION REQUIRED.
==================================
These are SEEDED from the working Google Docs registry because the Gemini
side panel is the same component across Workspace apps (the
``[role="complementary"][aria-label="Gemini"]`` region and the
``appsElementsSidekick…`` classes are shared). Most should carry over
verbatim; the ones most likely to differ on Sheets are:

  * ``side_panel_button`` — the toolbar entry-point that opens the panel.
    The label/toolbar differs from Docs; this is the first to re-validate.
  * ``side_panel_container`` accessible name (if Sheets localises it).

**Before running against live Sheets:** open a Sheet, DevTools-inspect the
Gemini panel, confirm each selector, and either edit the values here or run
``scripts/heal_selectors.py`` (heals into the ``google_sheets`` overlay
without a code edit). See ``docs/SHEETS_UI_MODE_RUNBOOK.md``.

We prefer ARIA role / accessible-name selectors over raw CSS because they
survive minor markup changes.
"""

from __future__ import annotations

from typing import cast

from advtest_ext.adapters._ui_mode_common.selector_overlay import resolve_selectors
from advtest_ext.adapters.google_docs.ui_mode.selectors import (
    COMMON_SELECTOR_KEYS,
    SidePanelSelectors,
)

# Sheets has no suggested-edit / confirm UI, so it pins against the common
# side-panel keys only (not the Docs-only suggestion/confirm keys).
EXPECTED_KEYS = COMMON_SELECTOR_KEYS

# Seeded from the Docs registry — the shared Gemini-panel selectors. The panel
# container and every child selector are scoped under the Gemini complementary
# region, which is identical across apps; only the toolbar entry button is a
# likely Sheets-specific divergence (see the module warning).
_BASE_SELECTORS: SidePanelSelectors = {
    "side_panel_button": (
        # Sheets toolbar entry-point. Docs uses aria-label "Ask Gemini"; the
        # Sheets label may differ — broaden to
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


# Heals for Sheets are stored under a SEPARATE overlay key ("google_sheets")
# so a Docs heal and a Sheets heal never overwrite each other.
SELECTORS: SidePanelSelectors = cast(
    "SidePanelSelectors", resolve_selectors(_BASE_SELECTORS, "google_sheets")
)

__all__ = ["EXPECTED_KEYS", "SELECTORS", "SidePanelSelectors"]
