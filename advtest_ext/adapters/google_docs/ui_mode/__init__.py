"""ui_mode for ``GoogleDocsTarget``.

Demo-grade Playwright automation against the real Gemini side panel
on ``docs.google.com``. Used for the Google adversarial-team pitch
to demonstrate that advtest can exercise the surface users actually
see (the side panel) rather than only the surface backend code paths
hit (the Gemini API).

Non-goals (explicit):

* Selector robustness — selectors will drift; we re-validate before
  each demo. See :mod:`selectors` for the registry and
  scripts/heal_selectors.py for re-validation (docs/architecture/03-target-adapters.md §9).
* Persistence beyond the auth profile — the browser context is
  spun up per call.
* Production parallelism — one browser context per ``ui_mode_invoke``;
  no pool, no concurrency control.

Entry point: :func:`ui_mode_invoke`. Raises
:class:`SidePanelUnavailable` on any timeout or selector miss with a
canonical message that points users at the selector-update
checklist.

References (FOUNDATIONS.md): GeminiJack (geminijack_2025) is the
specific Workspace-AI exploit that this surface reproduces — the
Gemini side panel is the carrier in that chain.
"""

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    UiModeSession,
)
from advtest_ext.adapters.google_docs.ui_mode.invoker import ui_mode_invoke
from advtest_ext.adapters.google_docs.ui_mode.multi_turn_target import (
    GeminiDocsMultiTurnTarget,
)
from advtest_ext.adapters.google_docs.ui_mode.selectors import SELECTORS

__all__ = [
    "SELECTORS",
    "GeminiDocsMultiTurnTarget",
    "SidePanelUnavailable",
    "UiModeSession",
    "ui_mode_invoke",
]

__citations__: list[str] = [
    "geminijack_2025",
]
