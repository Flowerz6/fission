"""ui_mode for ``GmailTarget``.

Demo-grade Playwright automation against the real Gemini side panel
on ``mail.google.com``. Mirrors the Docs ui_mode adapter — Workspace
ships the same Sidekick side-panel component across Docs / Gmail /
Sheets, so most selectors and almost all of the flake-hardening
logic transfer directly. The product-specific bits are the URL,
the thread-list / thread-view readiness checks, and the entry point
into the side panel.

Non-goals (explicit), same as Docs:

* Selector robustness — selectors will drift; re-validate before
  each demo.
* Persistence beyond the auth profile — context is spun up per call
  unless an external session is reused.
* Production parallelism — one Chrome per ``UiModeSession``.

Entry point: :func:`ui_mode_invoke`. Raises
:class:`SidePanelUnavailable` on any timeout / selector miss.
"""

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    UiModeSession,
)
from advtest_ext.adapters.gmail.ui_mode.compose import (
    GmailComposeUnavailable,
    GmailSendResult,
    GmailUiComposeSession,
    gmail_ui_send_email,
)
from advtest_ext.adapters.gmail.ui_mode.compose_selectors import COMPOSE_SELECTORS
from advtest_ext.adapters.gmail.ui_mode.invoker import ui_mode_invoke
from advtest_ext.adapters.gmail.ui_mode.selectors import SELECTORS

__all__ = [
    "COMPOSE_SELECTORS",
    "SELECTORS",
    "GmailComposeUnavailable",
    "GmailSendResult",
    "GmailUiComposeSession",
    "SidePanelUnavailable",
    "UiModeSession",
    "gmail_ui_send_email",
    "ui_mode_invoke",
]

__citations__: list[str] = [
    "geminijack_2025",
]
