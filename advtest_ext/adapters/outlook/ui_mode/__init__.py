"""ui_mode for ``OutlookTarget`` — send email via outlook.com web UI.

A human-equivalent send path: Playwright drives a real Chrome
context that's already logged into outlook.com (one-time interactive
login via ``scripts/setup_outlook_profile.py``). The flow opens
Compose, fills To/Subject/Body, clicks Send, and waits for the
Sent-confirmation indicator. No SMTP, no Microsoft Graph, no OAuth
on the sender side.

Non-goals (explicit):

* Selector robustness — Outlook's web UI is a closed-source app
  under active development. Selectors will drift. Re-validate
  before each demo (same discipline the Docs adapter follows).
* Sent-folder thread/message id extraction — V1's reply step uses
  ``me`` self-reply so we don't depend on threading. A future
  enhancement could grep the Sent folder for the just-sent message
  to recover a thread id, but it's not load-bearing today.

Entry point: :func:`outlook_ui_send_email`. Raises
:class:`OutlookComposeUnavailable` on any timeout or selector miss.
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
