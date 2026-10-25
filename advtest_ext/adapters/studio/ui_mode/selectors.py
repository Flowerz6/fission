"""Selectors for the Workspace Studio agent surface.

WARNING — SEEDED GUESSES; LIVE VALIDATION REQUIRED.
===================================================
Unlike Docs / Sheets / Slides / Gmail, Workspace Studio is **not** the Gemini
side panel — it is an agentic app-builder/runner, so these selectors are NOT
inherited from the shared ``[role="complementary"][aria-label="Gemini"]``
registry. They are seeded guesses at the Studio *app-run* surface (the panel
where a built app is executed over its data source and the agent streams its
result) and MUST be healed against the live Studio DOM before a real run.

Heals land in a SEPARATE overlay key (``google_studio``) so a Studio heal never
collides with a Docs/Slides/Sheets heal. Run ``scripts/heal_selectors.py --app
google_studio --manual --url <studio app url>`` once the real surface is
reachable, or DevTools-inspect and edit the ``_BASE_SELECTORS`` values here.

The KEYS are the stable contract (:func:`~advtest_ext.adapters.studio.ui_mode.
invoker.ui_mode_invoke` and the test suite pin against :data:`EXPECTED_KEYS`);
the VALUES are ephemeral and will drift as Studio evolves.

Selector strategy: prefer ARIA role / accessible-name matchers over raw CSS
(they survive minor markup churn); fall back to ``[data-…]`` / ``[aria-busy]``
attribute shapes where no stable role exists.
"""

from __future__ import annotations

from typing import TypedDict, cast

from advtest_ext.adapters._ui_mode_common.selector_overlay import resolve_selectors


class StudioSelectors(TypedDict):
    """Typed registry of Workspace Studio agent-surface locators. Each value is
    a Playwright selector string (CSS / ``role=…`` / ``aria-…``)."""

    app_container: str
    """The Studio app surface container — the element present once a built app
    is open and runnable. Used to confirm navigation landed on the app."""

    agent_input: str
    """The input where the user's trigger prompt is typed (e.g. "Run the Weekly
    Report app on this data source"). Often a ``role=textbox`` / contenteditable."""

    run_button: str
    """The "Run" / "Send" control that executes the app / submits the turn to
    the agent. Clicked directly (Enter is often eaten by the composer)."""

    agent_response: str
    """The container holding the agent's streamed output — read via
    ``inner_text`` once the running indicator clears."""

    running_indicator: str
    """The "agent is running / generating" state. The invoker waits for it to
    appear (run started) then disappear (run finished) before reading output."""

    run_complete: str
    """A redundant "run finished" signal (an action bar / status chip that
    mounts when the agent settles) — a fallback for running_indicator drift."""

    new_run: str
    """Reset the app to a fresh run / clear the prior conversation so the next
    turn lands clean (the Studio analogue of "New conversation")."""


# Seeded guesses — NONE of these are validated against live Studio. Broad,
# role/name-based matchers so a heal has the best chance of a near-miss rather
# than a total miss. RE-VALIDATE EVERY ONE before trusting a run.
_BASE_SELECTORS: StudioSelectors = {
    "app_container": (
        '[role="main"], [data-app-surface], main[aria-label*="Studio" i], '
        'div[aria-label*="app" i]'
    ),
    "agent_input": (
        '[contenteditable="true"], textarea, [role="textbox"], '
        'input[type="text"][aria-label*="prompt" i]'
    ),
    "run_button": (
        'button[aria-label*="Run" i]:not([disabled]):not([aria-disabled="true"]), '
        'button[aria-label*="Send" i]:not([disabled]):not([aria-disabled="true"]), '
        'button:has-text("Run")'
    ),
    "agent_response": (
        '[data-agent-response], [class*="agentMessage" i], '
        '[role="article"], [aria-live="polite"]'
    ),
    "running_indicator": (
        '[aria-busy="true"], [class*="running" i], [class*="thinking" i], '
        '[role="progressbar"]'
    ),
    "run_complete": (
        '[data-run-complete], [class*="responseActions" i], '
        'button[aria-label*="Copy" i]'
    ),
    "new_run": (
        'button[aria-label*="New" i], button:has-text("New run"), '
        'button:has-text("Clear")'
    ),
}


# Studio heals are stored under their own overlay key so they never overwrite a
# Docs/Slides/Sheets/Gmail heal (those share the Gemini panel; Studio does not).
SELECTORS: StudioSelectors = cast(
    "StudioSelectors", resolve_selectors(_BASE_SELECTORS, "google_studio")
)


EXPECTED_KEYS: frozenset[str] = frozenset(
    {
        "app_container",
        "agent_input",
        "run_button",
        "agent_response",
        "running_indicator",
        "run_complete",
        "new_run",
    }
)
"""Source of truth for the SELECTORS keys — the invoker's lookups and the unit
test both pin against this set, so a missing or stray key fails loudly."""


__all__ = ["EXPECTED_KEYS", "SELECTORS", "StudioSelectors"]
