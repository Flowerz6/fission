"""Side-panel CSS / role selectors for ui_mode automation.

WARNING — selector drift expected.
================================
Google Docs is a closed-source web app under active development.
Every selector in this file points at a DOM target whose attributes
Google can change at any time. Treat the registry as ephemeral: it
is correct *as of the last time someone ran the three demo
scenarios end-to-end against live Docs*, and may not be correct
tomorrow.

**Before every demo:** run each scenario in
``scripts/demo_ui_scenarios.py`` end-to-end. If any fails on
"selector not found" or "timeout waiting for element", update the
matching entry here and re-run. The pre-pitch checklist captures
this in TODO_BEFORE_PITCH.md item 4.

The keys are stable — :func:`ui_mode_invoke` looks each one up by
name. The *values* may shift across Docs releases.

Selector strategy
-----------------
We prefer ARIA role / accessible-name selectors (Playwright's
``role=button[name=…]`` and ``aria-label`` matchers) over raw CSS
because they survive minor markup changes. Where that's not
possible (loading indicators have no stable role), we fall back to
attribute matchers on ``[data-…]`` or ``[aria-busy=true]`` shapes
because those drift less than class names.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict, cast

from advtest_ext.adapters._ui_mode_common.selector_overlay import resolve_selectors


class SidePanelSelectors(TypedDict):
    """Typed registry of side-panel locators. Each value is a
    Playwright selector string (CSS / ``role=…`` / ``aria-…``).

    Keep the keys here in sync with the lookups in
    :mod:`advtest_ext.adapters.google_docs.ui_mode.invoker` — the
    test ``test_ui_mode.test_selectors_have_expected_keys`` enforces
    the contract.
    """

    side_panel_button: str
    """The "Open Gemini" / star button on the Docs toolbar that
    opens the side panel. Accessible-name has fluctuated between
    "Ask Gemini", "Help me write", and the localised variants —
    update on selector-drift failure."""

    side_panel_container: str
    """The side-panel container itself — the element that exists
    whenever the panel is visible. The invoker uses this to verify
    the entry-button click actually opened the panel (and to
    fast-path past the open step when a previous run left the panel
    open). Typically a ``role="dialog"`` / ``role="complementary"``
    region with a Gemini-flavoured accessible name."""

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
    sorry, doc empty → user follow-up" thread that muddies the
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

    suggestion_accept: NotRequired[str]
    """The "Accept" control on a Gemini-proposed *suggested edit*
    (Docs track-changes). When Gemini writes into the doc via the
    inline editor it does NOT commit directly — it proposes a
    suggestion the user must Accept before the text becomes real
    document body. For a write-back exfil guide this click IS the
    exit channel: without it the leaked data stays in a pending
    suggestion the ``document_body`` signal never sees. Returns ALL
    accept controls on the page (a reply can propose several); the
    invoker clicks them oldest-first until none remain."""

    suggestion_reject: NotRequired[str]
    """The "Reject" control on a Gemini-proposed suggested edit —
    the clean-control counterpart to ``suggestion_accept`` (discard
    the proposal so nothing is written). Used by reject-mode runs and
    to clear a stuck "Resolve Gemini suggestions" lock between nodes."""

    action_confirm: NotRequired[str]
    """A generic confirm / allow / continue button on a Gemini
    action-permission prompt (e.g. "Gemini wants to …" → Allow, or a
    "Continue" step gate). Best-effort and permissive — accessible
    names vary and this surface drifts often; the invoker clicks it
    only when ``confirm_actions`` is enabled and swallows a miss."""


# Snapshot taken against the live Docs DOM. Confirmed by DevTools
# inspection — selectors target the ``appsElementsSidekick…``
# component classes that Docs renders for the Gemini side panel.
# Re-validate before each pitch (see TODO_BEFORE_PITCH.md item 4), or run
# ``scripts/heal_selectors.py`` to auto-repair drift into the overlay.
_BASE_SELECTORS: SidePanelSelectors = {
    "side_panel_button": (
        # Toolbar entry-point. Confirmed accessible name is
        # exactly "Ask Gemini". If Docs starts A/B-testing the
        # label (it has historically used "Help me write") this
        # is the first selector that will drift — broaden to
        # ``button[aria-label*="Gemini" i]:not([aria-label*="bottom" i])``
        # to recover.
        'button[aria-label="Ask Gemini"]'
    ),
    "side_panel_container": (
        # The side-panel surface itself: an ARIA complementary
        # region with the exact accessible name "Gemini". Used by
        # the invoker to (a) fast-path past the open dance when
        # the panel is already mounted and (b) verify the entry-
        # button click actually mounted the panel. Every other
        # side-panel selector below is scoped under this root so
        # toolbar / main-document elements with overlapping shapes
        # can't false-positive.
        '[role="complementary"][aria-label="Gemini"]'
    ),
    "side_panel_input": (
        # Side-panel prompt field. A contenteditable inside the
        # panel footer; scoped under the container so other
        # contenteditables on the page (Doc body, comment threads)
        # can't collide.
        '[role="complementary"][aria-label="Gemini"] [contenteditable="true"]'
    ),
    "side_panel_response": (
        # Latest agent reply bubble. The agent message wrapper
        # carries a child element with class
        # ``appsElementsSidekickAgentMessageBubbleContent`` — that
        # element holds the rendered reply text (and only that,
        # not the screen-reader label or the action-bar buttons).
        #
        # The selector intentionally returns ALL agent bubbles in
        # the panel. The invoker uses Playwright's
        # ``locator(...).last`` to pick the newest one. Previously
        # we tried ``:has(...):last-of-type`` to narrow to one
        # match in CSS, but ``:last-of-type`` selects "last of
        # this tag name within parent", which is brittle once the
        # panel wraps messages in heterogeneous containers — it
        # silently returned nothing when the structure shifted.
        '[role="complementary"][aria-label="Gemini"] .appsElementsSidekickAgentMessageBubbleContent'
    ),
    "side_panel_loading_indicator": (
        # "Thinking state" element shown while Gemini is generating.
        # Class-prefix match because Docs sometimes appends a state
        # suffix (e.g. ``…ThinkingStateActive``).
        '[role="complementary"][aria-label="Gemini"] [class*="appsElementsSidekickThinkingState"]'
    ),
    "side_panel_response_complete": (
        # The action-bar container (copy / regenerate / share
        # actions) that mounts at the bottom of the latest agent
        # reply once generation finishes. Redundant "done" signal —
        # not currently used by the invoker, reserved for future
        # robustness work.
        '[role="complementary"][aria-label="Gemini"] '
        '[role="listitem"]:last-of-type '
        ".appsElementsSidekickResponseOptionsActionBarContainer"
    ),
    "side_panel_new_conversation": (
        # The "New conversation" button — confirmed present from
        # the failure-artefact body dump (out/failures/*.txt). We
        # try aria-label first, fall back to button text. Scoped
        # under the panel container so a same-name button
        # elsewhere on the page can't false-positive.
        '[role="complementary"][aria-label="Gemini"] '
        'button[aria-label*="New conversation" i], '
        '[role="complementary"][aria-label="Gemini"] '
        'button:has-text("New conversation")'
    ),
    "side_panel_close": (
        # Side-panel close ("X") button, scoped under the container.
        '[role="complementary"][aria-label="Gemini"] button[aria-label*="Close" i]'
    ),
    "side_panel_send": (
        # Send / submit button on the composer (the up-arrow icon).
        # Permissive — Google has used "Send message", "Send", and
        # localised variants. ``:not([disabled])`` filters out the
        # greyed-out button shown when the input is empty so the
        # selector only matches once there's text to send.
        '[role="complementary"][aria-label="Gemini"] '
        'button[aria-label*="Send" i]:not([disabled]):not([aria-disabled="true"])'
    ),
    "suggestion_accept": (
        # Accept control on a Docs suggested edit. Google renders this
        # both as an inline hover-card button and as a per-suggestion
        # popup; accessible name has appeared as "Accept" and
        # "Accept suggestion". NOT scoped under the Gemini panel — the
        # suggestion controls live in the document editor surface, not
        # the side panel. Permissive across button / role=button.
        'div[role="button"][aria-label*="Accept" i], '
        'button[aria-label*="Accept" i], '
        'div[role="button"]:has-text("Accept"), '
        'button:has-text("Accept")'
    ),
    "suggestion_reject": (
        # Reject control on a Docs suggested edit — mirror of accept.
        'div[role="button"][aria-label*="Reject" i], '
        'button[aria-label*="Reject" i], '
        'div[role="button"]:has-text("Reject"), '
        'button:has-text("Reject")'
    ),
    "action_confirm": (
        # Generic Gemini action-permission / continue button. Very
        # permissive and drift-prone by nature; only used when
        # confirm_actions is enabled. Excludes the composer send button.
        'button[aria-label*="Allow" i], '
        'button:has-text("Allow"), '
        'button:has-text("Continue"), '
        'div[role="button"]:has-text("Allow")'
    ),
}


# Apply any ``ADVTEST_SELECTOR_OVERLAY`` entries for this app on top of the
# baked-in defaults, so a healed selector takes effect without a code edit.
# An empty/absent overlay leaves the base registry unchanged.
SELECTORS: SidePanelSelectors = cast(
    "SidePanelSelectors", resolve_selectors(_BASE_SELECTORS, "google_docs")
)


COMMON_SELECTOR_KEYS: frozenset[str] = frozenset(
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
"""The Gemini side-panel keys shared by every Workspace surface. Sheets and
Calendar pin against this set (they have no suggested-edit / confirm UI)."""

EXPECTED_KEYS: frozenset[str] = COMMON_SELECTOR_KEYS | frozenset(
    {
        # Docs-only: the track-changes suggested-edit accept/reject controls
        # and the action-confirm button Gemini surfaces when it writes to the doc.
        "suggestion_accept",
        "suggestion_reject",
        "action_confirm",
    }
)
"""Source of truth for the Docs SELECTORS keys. The invoker's lookups
and the unit test both pin against this set, so missing or stray
keys fail loudly rather than at run time."""


__all__ = ["COMMON_SELECTOR_KEYS", "EXPECTED_KEYS", "SELECTORS", "SidePanelSelectors"]
