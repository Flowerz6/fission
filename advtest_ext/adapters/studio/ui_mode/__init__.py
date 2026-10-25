"""ui_mode for Workspace Studio.

Demo-grade Playwright automation against the real Workspace Studio agent
surface. Unlike Docs / Sheets / Slides / Gmail, Studio is NOT the Gemini side
panel — it is an agentic app-runner — so this package does not reuse the shared
side-panel driver; it drives the Studio app surface directly (navigate → run →
read the agent's streamed output).

Non-goals (explicit):

* Selector robustness — the Studio selectors AND the app-URL shape are SEEDED
  guesses and WILL need live validation / healing (see :mod:`selectors`).
* Production parallelism — one page per invoke on the single shared profile.

Entry point: :func:`ui_mode_invoke`. Raises
:class:`~advtest_ext.adapters._ui_mode_common.SidePanelUnavailable` on any
timeout or selector miss (the canonical UI-drift signal the orchestrator keys
on).
"""

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    UiModeSession,
)
from advtest_ext.adapters.studio.ui_mode.invoker import ui_mode_invoke
from advtest_ext.adapters.studio.ui_mode.multi_turn_target import (
    GeminiStudioMultiTurnTarget,
)
from advtest_ext.adapters.studio.ui_mode.selectors import SELECTORS

__all__ = [
    "SELECTORS",
    "GeminiStudioMultiTurnTarget",
    "SidePanelUnavailable",
    "UiModeSession",
    "ui_mode_invoke",
]
