"""Compose-flow CSS / role selectors for mail.google.com automation.

Sibling of :mod:`advtest_ext.adapters.outlook.ui_mode.selectors` —
the Outlook module sends the *poisoned* half of the chain
(attacker_sender -> victim inbox); this module sends the
*acknowledgment* half: a reply composed **from the victim's own
Gmail account** so the adopted persona lands in the victim's Sent
folder. Gemini grounds on Sent-folder content too, so the ack is a
second injection surface, not just dressing.

Why a separate registry from the side-panel ``SELECTORS`` in
``gmail/ui_mode/selectors.py``: that registry targets the Sidekick
Gemini panel (read path). This one targets Gmail's Compose window
(write path). They share a profile and a tab origin but no DOM, so
keeping them apart avoids one re-skin breaking both surfaces.

WARNING — selector drift expected.
================================
Gmail (the Google Workspace mail web app) is a closed-source web app
under continuous active development. Treat this registry as
ephemeral: it is correct *as of the last time someone ran the chain
end-to-end against live Gmail*, and may not be correct tomorrow.

**Before every demo:** run a no-op Compose flow against your live
Gmail profile and verify each lookup resolves.

Selector strategy
-----------------
Prefer ARIA role / accessible-name matchers over raw CSS classes —
Gmail's class names are obfuscated and rotate, but the accessible
names (``aria-label="Subject"``, ``name="subjectbox"``) survive
re-skins. Each value is a comma-separated list of fallback selectors
so a single label rename doesn't ground the whole flow; Playwright
treats the comma as a union.
"""

from __future__ import annotations

from typing import TypedDict


class GmailComposeSelectors(TypedDict):
    """Typed registry of Gmail Compose locators."""

    inbox_url: str
    """Full URL the invoker navigates to to land in the Inbox view.
    ``u/0`` pins to the first signed-in account in the profile — the
    same account whose inbox received the poisoned series and whose
    Gemini we trigger, so the ack lands where grounding reads it."""

    compose_button: str
    """The "Compose" button on the Inbox toolbar that opens the
    Compose window. ``gh="cm"`` has been Gmail's stable hook for the
    compose action across many releases; aria-label is the fallback."""

    compose_pane: str
    """Compose window container — keyed on the subject box so it
    matches ONLY the compose dialog, never some other ``role=dialog``
    on the page (settings, confirmations). Used both to confirm the
    Compose-button click opened Compose AND, with ``state=hidden``,
    as the post-send signal (the window detaches on success)."""

    to_field: str
    """Recipient input. Modern Gmail uses a peoplekit ``input`` with
    accessible name 'To recipients'; older releases used a
    ``textarea[name=to]``."""

    subject_field: str
    """Subject line input. ``name="subjectbox"`` is long-lived."""

    body_field: str
    """Message body — a contenteditable rich-text region with
    accessible name 'Message Body'."""

    send_button: str
    """Send button on the Compose toolbar. Gmail's accessible name is
    'Send' (often suffixed with the Ctrl-Enter hint). Excludes the
    disabled state so the wait naturally stalls until Gmail will
    accept the send."""

    sent_confirmation: str
    """Post-send indicator. We wait for the Compose window
    (``compose_pane``) to detach (``state=hidden``) — the most stable
    'sent' signal, mirroring the Outlook invoker. The transient
    'Message sent' toast is more fragile and only used, if at all, by
    failure-path enrichment."""

    compose_error: str
    """An inline error / alert region Gmail injects when a send is
    rejected mid-flow (invalid recipient, quota, transient network).
    Read on the failure path only — never gates the success path."""


COMPOSE_SELECTORS: GmailComposeSelectors = {
    "inbox_url": "https://mail.google.com/mail/u/0/#inbox",
    "compose_button": (
        # ``gh="cm"`` is Gmail's stable compose-action hook; keep the
        # aria-label / text fallbacks for the day Google drops it.
        'div[gh="cm"], '
        'div[role="button"][aria-label*="Compose" i], '
        'div[role="button"]:has-text("Compose")'
    ),
    "compose_pane": (
        # Key the dialog on the subject box so we match the Compose
        # window specifically, not any other role=dialog. Playwright
        # supports :has() in its CSS engine.
        'div[role="dialog"]:has(input[name="subjectbox"]), '
        'div[role="dialog"]:has(input[aria-label*="Subject" i])'
    ),
    "to_field": (
        'input[aria-label*="To recipients" i], '
        'input[aria-label="To" i], '
        'textarea[name="to"], '
        'div[role="dialog"] input[peoplekit-id]'
    ),
    "subject_field": ('input[name="subjectbox"], input[aria-label*="Subject" i]'),
    "body_field": (
        'div[aria-label*="Message Body" i][contenteditable="true"], '
        'div[role="textbox"][aria-label*="Message Body" i]'
    ),
    "send_button": (
        # Gmail's send button is a role=button div whose accessible
        # name starts with 'Send' ("Send ‪(Ctrl-Enter)‬"). Exclude the
        # disabled state. data-tooltip is a robust secondary hook.
        'div[role="button"][aria-label*="Send" i]:not([aria-disabled="true"]), '
        'div[role="button"][data-tooltip*="Send" i]:not([aria-disabled="true"])'
    ),
    "sent_confirmation": (
        # Same element as compose_pane; invoker waits for state=hidden.
        'div[role="dialog"]:has(input[name="subjectbox"]), '
        'div[role="dialog"]:has(input[aria-label*="Subject" i])'
    ),
    "compose_error": (
        # ARIA alert / assertive live-region Gmail uses for send-time
        # errors. Broad on purpose — read only on the failure path so
        # a false positive can't break a successful send.
        '[role="alert"]:visible, div[aria-live="assertive"]'
    ),
}


EXPECTED_KEYS: frozenset[str] = frozenset(
    {
        "inbox_url",
        "compose_button",
        "compose_pane",
        "to_field",
        "subject_field",
        "body_field",
        "send_button",
        "sent_confirmation",
        "compose_error",
    }
)
"""Source of truth for the COMPOSE_SELECTORS keys. The invoker's
lookups and the unit test both pin against this set."""


__all__ = ["COMPOSE_SELECTORS", "EXPECTED_KEYS", "GmailComposeSelectors"]
