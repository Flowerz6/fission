"""Side-panel CSS / role selectors for Gmail ui_mode automation.

Same drift warning as the Docs sibling — Gmail is a closed-source
web app, every selector here can move. Re-validate before each
demo run.

Selector strategy mirrors the Docs adapter: ARIA role +
accessible-name first, ``[class*=...]`` attribute matches for
loading-state elements, scope every per-element selector under the
side-panel container so Gmail's main thread list can't false-positive.

Workspace shares the ``appsElementsSidekick*`` Sidekick component
across Docs, Gmail, Sheets, and Slides — that's why the
selector *shapes* below look almost identical to the Docs registry.
The product-specific entry is :data:`SELECTORS["side_panel_button"]`
(Gmail's right-rail Gemini icon vs. Docs' toolbar entry).
"""

from __future__ import annotations

from typing import TypedDict


class SidePanelSelectors(TypedDict):
    """Typed registry of side-panel locators. Keep keys aligned with
    the Docs sibling and with :data:`EXPECTED_KEYS` below."""

    side_panel_button: str
    """Gmail's right-rail Gemini entry. The right rail in mail.google.com
    shows a column of vertical icons (Calendar, Keep, Tasks, Contacts,
    plus Gemini). The Gemini button is typically a square button with
    an aria-label that begins with ``Gemini`` (sometimes ``Open the
    side panel`` historically — that drift is the most likely first
    failure mode here)."""

    side_panel_container: str
    """The side-panel surface — the ARIA complementary region rendered
    by the shared Sidekick component once the panel opens."""

    side_panel_input: str
    """Contenteditable prompt field inside the open panel."""

    side_panel_response: str
    """Latest agent reply bubble. Same Sidekick class as Docs —
    iterate with ``.last`` in the invoker."""

    side_panel_loading_indicator: str
    """"Thinking state" element shown while Gemini is generating."""

    side_panel_response_complete: str
    """Action-bar mounted at the bottom of the latest reply once
    generation finishes. Reserved for selector-drift fallback work;
    not currently used by the invoker."""

    side_panel_new_conversation: str
    """"New conversation" button — same dance as Docs to discard any
    auto-summary that fires on first open against a thread."""

    side_panel_close: str
    """Side-panel close ("X") button."""

    side_panel_send: str
    """Send / submit button on the composer."""


# Snapshot — Workspace AI's side panel is the same component across
# Docs and Gmail, so most selectors below are intentionally
# byte-identical to the Docs registry. The toolbar entry button is
# the one place where Gmail's right-rail icon differs from Docs'
# inline toolbar button. Re-validate before each demo (item 4 in
# TODO_BEFORE_PITCH.md applies here too).
SELECTORS: SidePanelSelectors = {
    "side_panel_button": (
        # Gmail right-rail Gemini entry. Re-validated 2026-09-06 against
        # f04438179@gmail.com: the ONLY Gemini entry present is now a
        # ``<button aria-label="Ask Gemini">`` (the old 2026-05 exclusion
        # of that exact label matched nothing → SidePanelUnavailable).
        # Prefer the exact "Ask Gemini" button, then fall back to the
        # older right-rail ``<div role="button">`` / label-variant forms.
        'button[aria-label="Ask Gemini" i], '
        'div[role="button"][aria-label*="Gemini" i], '
        'button[aria-label*="Gemini" i]'
    ),
    "side_panel_container": (
        # CONFIRMED from live Gmail DOM (2026-05-21):
        #   <div class="MxSLJe" aria-label="Gemini"
        #        jscontroller="zjN4tf"
        #        jsaction="rcuQ6b:2k:DaLnFc; IXqKSb:MnRxed; transitionend:.CLIENT">
        # Key delta vs. Docs: Gmail's panel has NO ``role="complementary"``
        # — Docs has it, Gmail doesn't. So we match on aria-label
        # alone, and disambiguate from the right-rail entry button
        # (which also carries ``aria-label="Gemini"`` but adds
        # ``role="button"``) by excluding role="button". The
        # ``[jscontroller]`` requirement is extra protection
        # against same-name elements that aren't the panel.
        '[aria-label="Gemini"][jscontroller]:not([role="button"])'
    ),
    "side_panel_input": (
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) [contenteditable="true"]'
    ),
    "side_panel_response": (
        # Confirmed shape (2026-05-21):
        #   <div role="listitem" class="oh22h">
        #     ...<span class="rnc2Gd" jsname="t1Rade">Hello!...</span>
        #
        # The selector picks the OUTER bubble (``[role="listitem"]``)
        # but requires a ``jsname="t1Rade"`` descendant so suggestion
        # chips — which are also role=listitem but lack the agent-
        # message-text marker — are excluded. Reading inner_text on
        # the outer bubble concatenates every sub-span Gemini emits
        # as it streams; reading inner_text on the inner ``t1Rade``
        # span only gets one fragment, which for streamed responses
        # is usually a trailing punctuation char ("." or "?") — the
        # symptom that surfaced 2026-05-24 when the narrow selector
        # produced one-character "responses" in the trace view.
        #
        # Refusal bubbles ("I'm still learning, can't help with
        # that.") use the same agent-message component as normal
        # replies, so they DO carry ``jsname="t1Rade"`` — the
        # ``:has()`` filter still catches them.
        # Re-validated 2026-09-06: Gemini-in-Gmail dropped the
        # ``[role="listitem"]`` wrapper around the agent bubble, so the
        # old listitem-scoped selector matched nothing → SidePanelUnavailable
        # on read. Keep the listitem form first (older surfaces still use
        # it) and fall back to the ``jsname="t1Rade"`` message span, which
        # still carries the agent reply text on the current surface.
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) '
        '[role="listitem"]:has([jsname="t1Rade"]), '
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) '
        '[jsname="t1Rade"]'
    ),
    "side_panel_loading_indicator": (
        # Best-guess: Gmail's "thinking" state class. NEEDS VALIDATION
        # — paste a snapshot of an in-flight response if this misses.
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) [aria-busy="true"], '
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) [class*="thinking" i]'
    ),
    "side_panel_response_complete": (
        # Reserved for future drift-safety; not used by the invoker.
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) [role="listitem"]:last-of-type'
    ),
    "side_panel_new_conversation": (
        # Best-effort — Gmail's panel may not have a "New conversation"
        # button at all (Gmail panel scope is per-thread). If the
        # selector misses, the invoker's _start_new_conversation
        # returns False and we proceed.
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) '
        '[role="button"][aria-label*="New" i]'
    ),
    "side_panel_close": (
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) '
        '[role="button"][aria-label*="Close" i]'
    ),
    "side_panel_send": (
        '[aria-label="Gemini"][jscontroller]:not([role="button"]) '
        '[role="button"][aria-label*="Send" i]:not([aria-disabled="true"])'
    ),
}


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


# Gmail-specific: signals that the inbox / thread view has actually
# painted in the main pane. Mail loads the chrome (left nav,
# right rail) before any thread content; clicking the right-rail
# Gemini icon too early lands on an empty pane and the panel
# either won't open or will auto-summarise nothing.
#
# Try each in order — first hit wins. Multiple shapes because
# Gmail's DOM has both [data-message-id] and [data-thread-perm-id]
# attributes available in different states.
THREAD_BODY_SELECTORS: tuple[str, ...] = (
    # Open-thread view: each message in the conversation carries
    # this attribute on its container row.
    "[data-message-id]",
    # Thread row in the inbox listing — present once the inbox has
    # rendered at least one thread. Useful when the seed thread
    # navigation hasn't landed yet but the inbox is up.
    "[data-thread-perm-id]",
    # Coarser fallback: the main mail pane itself. Always exists
    # once Gmail has hydrated, so it's a "page is alive" check
    # rather than a "thread is up" check.
    'div[role="main"]',
)


__all__ = ["EXPECTED_KEYS", "SELECTORS", "THREAD_BODY_SELECTORS", "SidePanelSelectors"]
