"""Outlook adapter package.

Faithful-to-the-report email send path: the attacker_sender in the
V1 / V4 chains uses any consumer email account to deliver crafted
email into the recipient's Workspace inbox — Gemini's inbox
grounding then picks it up at type_prompt time. Outlook is the
default because it doesn't require Google OAuth verification (the
existing ``gmail.send_email`` handler hits a verification gate that
blocks unverified test apps), keeping the threat model intact while
removing a friction wall.

Entry point: :func:`outlook_ui_send_email`.
"""

from advtest_ext.adapters.outlook.ui_mode.invoker import (
    OutlookComposeUnavailable,
    OutlookUiSession,
    outlook_ui_send_email,
)
from advtest_ext.adapters.outlook.ui_mode.selectors import SELECTORS

__all__ = [
    "SELECTORS",
    "OutlookComposeUnavailable",
    "OutlookUiSession",
    "outlook_ui_send_email",
]
