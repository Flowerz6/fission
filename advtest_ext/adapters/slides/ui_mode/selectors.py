"""Side-panel CSS / role selectors for the Slides Gemini side panel.

WARNING — SEEDED FROM DOCS; LIVE VALIDATION REQUIRED.
=====================================================
These are SEEDED from the working Google Docs registry because the Gemini
side panel is the SAME component across Workspace apps (the
``[role="complementary"][aria-label="Gemini"]`` region and the
``appsElementsSidekick…`` classes are shared). Most should carry over
verbatim; the ones most likely to differ on Slides are:

  * ``side_panel_button`` — the toolbar entry-point that opens the panel.
    The label/toolbar differs from Docs; this is the first to re-validate.
  * ``side_panel_container`` accessible name (if Slides localises it).

Slides also introduces two surfaces that have no Docs analogue and live in
the invoker rather than this registry — DECK NAVIGATION (the filmstrip /
active-slide canvas) and the SPEAKER-NOTES pane readiness gate. Those are
seeded guesses too and MUST be healed against live Slides before a real run.

Every selector here points at a DOM target whose attributes Google can
change at any time — treat the registry as ephemeral. Google Slides is a
closed-source web app under active development.

**Before running against live Slides:** open a deck, DevTools-inspect the
Gemini panel, confirm each selector, and either edit the values here or run
``scripts/heal_selectors.py`` (heals into the ``google_slides`` overlay
without a code edit).

The keys are stable — :func:`ui_mode_invoke` looks each one up by name. The
*values* may shift across Slides releases.

Selector strategy
-----------------
We prefer ARIA role / accessible-name selectors (Playwright's
``role=button[name=…]`` and ``aria-label`` matchers) over raw CSS because
they survive minor markup changes. Where that's not possible (loading
indicators have no stable role), we fall back to attribute matchers on
``[data-…]`` or ``[aria-busy=true]`` shapes because those drift less than
class names.
"""

from __future__ import annotations

from typing import TypedDict, cast

from advtest_ext.adapters._ui_mode_common.selector_overlay import resolve_selectors


class SidePanelSelectors(TypedDict):
    """Typed registry of side-panel locators. Each value is a
    Playwright selector string (CSS / ``role=…`` / ``aria-…``).

    Keep the keys here in sync with the lookups in
    :mod:`advtest_ext.adapters.slides.ui_mode.invoker` — the
    test suite pins against :data:`EXPECTED_KEYS`.
    """

    side_panel_button: str
    """The "Ask Gemini" button on the Slides toolbar that opens the
    side panel. Accessible-name has fluctuated between "Ask Gemini",
    "Help me write", and the localised variants — update on
    selector-drift failure."""

    side_panel_container: str
    """The side-panel container itself — the element that exists
    whenever the panel is visible. The invoker uses this to verify
    the entry-button click actually opened the panel (and to
    fast-path past the open step when a previous run left the panel
    open). Typically a ``role="complementary"`` region with a
    Gemini-flavoured accessible name."""

    side_panel_input: str
    """The textarea (often a contenteditable div, role=textbox)
    inside the open side panel where the user types a prompt."""

    side_panel_response: str
    """The container that holds Gemini's rendered reply. We read
    ``inner_text`` from this element after the loading indicator
    disappears."""

    side_panel_loading_indicator: str
    """The "thinking state" element that signals "Gemini is still
    composing". The invoker waits for it to appear (confirm
    Gemini started), then disappear (confirm it finished) before
    reading the response. Carries the
    ``appsElementsSidekickThinkingState…`` class on the live DOM."""

    side_panel_response_complete: str
    """The action bar (copy / regenerate / etc.) that mounts at the
    bottom of the latest agent message once generation finishes.
    A redundant "response is done" signal alongside the loading
    indicator's hidden state — useful for selectors-have-drifted
    fallbacks. Not currently used by the invoker; reserved for the
    next round of robustness work."""

    side_panel_new_conversation: str
    """The "New conversation" button inside the side panel.

    Clicking it resets the panel to an empty-conversation state,
    discarding Google's auto-summary that fires when the panel is
    first opened. We use this so our PAIR / TAP prompt lands as
    turn 1 of a fresh conversation, not turn 3 of a "summarise →
    sorry, deck empty → user follow-up" thread that muddies the
    judge's view of Gemini's response."""

    side_panel_close: str
    """The button that closes the side panel — used by the teardown
    path so a subsequent scenario starts from a clean state."""

    side_panel_send: str
    """The "send" / "submit" button next to the prompt input (the
    up-arrow icon in the bottom-right of the chat composer).

    Why we need it as a first-class selector: pressing Enter on the
    contenteditable input often gets eaten as a newline by the
    composer's IME / autocomplete state — the prompt sits there but
    never submits. Clicking the send button directly bypasses that
    whole class of focus / keybinding issues. The invoker tries the
    button first and falls back to Enter only if the selector misses
    (drift safety net)."""


# Seeded from the Docs registry — the shared Gemini-panel selectors. The
# panel container and every child selector are scoped under the Gemini
# complementary region, which is identical across apps; only the toolbar
# entry button is a likely Slides-specific divergence (see the module
# warning).
_BASE_SELECTORS: SidePanelSelectors = {
    "side_panel_button": (
        # Slides toolbar entry-point. Docs uses aria-label "Ask Gemini";
        # the Slides label may differ — broaden to
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


# Heals for Slides are stored under a SEPARATE overlay key ("google_slides")
# so a Docs heal and a Slides heal never overwrite each other.
SELECTORS: SidePanelSelectors = cast(
    "SidePanelSelectors", resolve_selectors(_BASE_SELECTORS, "google_slides")
)


EXPECTED_KEYS: frozenset[str] = frozenset(
    {
        "side_panel_button",
        "side_panel_container",
        "side_panel_input",
        "side_panel_response",
        "side_panel_loading_indicator",
        "side_panel_response_complete",
        "side_panel_new_conversation",
        "side_panel_close",
        "side_panel_send",
    }
)
"""Source of truth for the SELECTORS keys. The invoker's lookups
and the unit test both pin against this set, so missing or stray
keys fail loudly rather than at run time."""


__all__ = ["EXPECTED_KEYS", "SELECTORS", "SidePanelSelectors"]
