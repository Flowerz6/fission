"""ui_mode for ``SlidesTarget``.

Demo-grade Playwright automation against the real Gemini side panel
on ``docs.google.com/presentation``. Slides is Docs' closest sibling —
the two share the Gemini Sidekick side panel — so this package is a
focused mirror of ``google_docs.ui_mode``: it reuses all the shared
``_ui_mode_common`` infrastructure and the proven side-panel drive,
targeting the Slides host (``/presentation/d/<id>/edit``) instead of
Docs. The Slides-specific divergences are DECK NAVIGATION (the active
slide canvas / filmstrip) and the SPEAKER-NOTES pane readiness gate,
both seeded from Docs and needing live DOM validation (see
:mod:`selectors`).

Non-goals (explicit):

* Selector robustness — selectors will drift; re-validate before
  each demo. See :mod:`selectors` for the registry.
* Persistence beyond the auth profile — the browser context is
  spun up per call.
* Production parallelism — one browser context per ``ui_mode_invoke``;
  no pool, no concurrency control.

Entry point: :func:`ui_mode_invoke`. Raises
:class:`SidePanelUnavailable` on any timeout or selector miss with a
canonical message that points users at the selector-update checklist.

References (FOUNDATIONS.md): GeminiJack (geminijack_2025) is the
specific Workspace-AI exploit that this surface reproduces — the
Gemini side panel is the carrier in that chain.
"""

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    UiModeSession,
)
from advtest_ext.adapters.slides.ui_mode.invoker import ui_mode_invoke
from advtest_ext.adapters.slides.ui_mode.multi_turn_target import (
    GeminiSlidesMultiTurnTarget,
)
from advtest_ext.adapters.slides.ui_mode.selectors import SELECTORS

__all__ = [
    "SELECTORS",
    "GeminiSlidesMultiTurnTarget",
    "SidePanelUnavailable",
    "UiModeSession",
    "ui_mode_invoke",
]

__citations__: list[str] = [
    "geminijack_2025",
]
