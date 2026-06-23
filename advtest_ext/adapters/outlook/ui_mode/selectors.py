"""Compose-flow CSS / role selectors for outlook.com automation.

WARNING — selector drift expected.
================================
outlook.com (Outlook on the Web / OWA) is a closed-source web app
under continuous active development. Microsoft re-skins the Compose
surface on a roughly-quarterly cadence and A/B tests aria-labels.
Treat this registry as ephemeral: it is correct *as of the last
time someone ran the V1 chain end-to-end against live Outlook*,
and may not be correct tomorrow.

**Before every demo:** run a no-op Compose flow against your live
Outlook profile and verify each lookup resolves. The pre-pitch
checklist captures this.

Selector strategy
-----------------
Prefer ARIA role / accessible-name matchers (Playwright's
``button[aria-label*=…]`` and ``role=textbox[name=…]``) over raw
CSS classes — accessible names survive minor re-skins where CSS
class names change every release. Each value is a comma-separated
list of fallback selectors so a single label rename doesn't ground
the whole flow; Playwright treats the comma as union.
"""

from __future__ import annotations

from typing import TypedDict


class OutlookSelectors(TypedDict):
    """Typed registry of Outlook Compose locators."""

    inbox_url: str
    """Full URL the invoker navigates to to land in the Inbox view.
    Outlook's deep links sometimes redirect through a homepage / SSO
    flow on cold-start; landing on /mail/inbox is the most direct
    path to a state where 'New mail' is visible."""

    new_mail_button: str
    """The "New mail" button on the Inbox toolbar that opens the
    Compose pane. aria-label has historically been 'New mail',
    'New message', or 'Compose' depending on release."""

    compose_pane: str
    """Compose container — the element that exists whenever the
    Compose surface is mounted. Used to verify the New-mail click
    actually opened Compose."""

    to_field: str
    """Recipient input. A contenteditable in modern OWA (role=textbox
    with accessible name 'To'). Older releases used a plain input."""

    subject_field: str
    """Subject line input."""

    body_field: str
    """Message body — a contenteditable rich-text region. Scoped
    under the Compose pane so we don't collide with other
    contenteditables elsewhere on the page (e.g. the search bar)."""

    send_button: str
    """Send button on the Compose toolbar."""

    sent_confirmation: str
    """A post-send indicator we can wait for: the Compose pane
    disappears on success, OR a transient 'Sent' toast appears.
    Either tells us the send went through."""

    compose_error: str
    """An inline error banner Outlook injects when a send is
    rejected mid-flow (attachment scan blocked the message,
    recipient address invalid, quota hit, transient network
    error). Read on the failure path only — promotes the generic
    'pane stayed mounted' error into a specific reason the
    operator can act on."""


SELECTORS: OutlookSelectors = {
    "inbox_url": "https://outlook.live.com/mail/0/inbox",
    "new_mail_button": (
        # Confirmed against live Outlook 2026-05-25: the aria-label
        # is "New email" (visible chip text matches). Microsoft has
        # used "New mail", "New message", and "Compose" historically
        # — keep all of them as fallbacks so a re-skin doesn't
        # immediately re-break the chain.
        #
        # CRITICAL: exclude the "New mail from <sender>" TOAST
        # notification button (aria-roledescription="Notification").
        # When a reply lands in the inbox mid-run, a loose
        # contains-match otherwise grabs that toast instead of the
        # real Compose button (and the toast sits under a dialog
        # backdrop that eats the click). Exact-label matches go first;
        # every contains-match fallback excludes the notification.
        'button[aria-label="New mail" i]:not([aria-roledescription="Notification" i]), '
        'button[aria-label="New email" i]:not([aria-roledescription="Notification" i]), '
        'button[aria-label="New message" i]:not([aria-roledescription="Notification" i]), '
        'button[aria-label="Compose" i]:not([aria-roledescription="Notification" i]), '
        'button[aria-label*="New email" i]:not([aria-roledescription="Notification" i]), '
        'button[aria-label*="New mail" i]:not([aria-roledescription="Notification" i]), '
        'button[aria-label*="New message" i]:not([aria-roledescription="Notification" i])'
    ),
    "compose_pane": (
        # Compose mounts inside its own region with an accessible
        # name. The aria-label has varied between 'Message body',
        # 'Compose', and a localised string; broaden on miss.
        '[role="region"][aria-label*="message" i], '
        '[role="dialog"][aria-label*="new message" i], '
        'div[aria-label="Message body" i]'
    ),
    "to_field": (
        # 'To' recipient field. Modern OWA uses a role=textbox
        # contenteditable; older releases used an input.
        '[role="textbox"][aria-label*="To" i], '
        'input[aria-label="To" i], '
        'div[contenteditable="true"][aria-label*="To" i]'
    ),
    "subject_field": (
        'input[aria-label*="Subject" i], '
        '[role="textbox"][aria-label*="Subject" i], '
        'input[placeholder*="subject" i]'
    ),
    "body_field": (
        # Compose body contenteditable. Scope to the compose pane
        # so the Inbox search bar or any other contenteditable
        # can't false-positive.
        'div[aria-label*="Message body" i][contenteditable="true"], '
        '[role="textbox"][aria-label*="Message body" i]'
    ),
    "send_button": (
        # The send button. Outlook has used 'Send', the up-arrow
        # icon, and 'Send (Ctrl+Enter)'. Broad match by aria-label.
        'button[aria-label="Send" i]:not([disabled]):not([aria-disabled="true"]), '
        'button[aria-label*="Send (" i]:not([disabled]):not([aria-disabled="true"])'
    ),
    "sent_confirmation": (
        # The Compose pane usually unmounts on successful send.
        # Waiting for the pane to detach (state='hidden') is a more
        # robust 'done' signal than chasing a transient toast.
        # The invoker uses this with state='hidden'.
        '[role="region"][aria-label*="message" i], [role="dialog"][aria-label*="new message" i]'
    ),
    "compose_error": (
        # ARIA alert / live-region notifications Outlook uses for
        # send-failure messages. Broad on purpose — Microsoft has
        # shipped these as role=alert, role=status, and bare divs
        # with aria-live across recent releases. We only read this
        # on the failure path so a false positive can't break a
        # successful send; the worst case is a slightly misleading
        # enrichment message.
        '[role="alert"]:visible, '
        '[role="status"][aria-live="assertive"], '
        'div[aria-live="assertive"][aria-atomic="true"]'
    ),
}


EXPECTED_KEYS: frozenset[str] = frozenset(
    {
        "inbox_url",
        "new_mail_button",
        "compose_pane",
        "to_field",
        "subject_field",
        "body_field",
        "send_button",
        "sent_confirmation",
        "compose_error",
    }
)
"""Source of truth for the SELECTORS keys. The invoker's lookups
and the unit test both pin against this set."""


__all__ = ["EXPECTED_KEYS", "SELECTORS", "OutlookSelectors"]
