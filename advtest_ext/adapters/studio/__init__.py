"""Workspace Studio adapter.

Workspace Studio is the handbook's high-priority agentic surface: a built app is
pointed at a DATA SOURCE (typically a shared Sheet) and, when run, ingests it
and can act across tools — which opens cross-app and agentic attacks the side
panels do not. The indirect injection rides in the data source (planted via
``sheets.create_and_seed``); the benign trigger is "run the app".

Unlike the Docs/Sheets/Slides/Gmail adapters, Studio is NOT the Gemini side
panel, so it does not share the side-panel driver. The product path is the
``ui_mode`` multi-turn target (:class:`~advtest_ext.adapters.studio.ui_mode.
GeminiStudioMultiTurnTarget`). The Studio selectors and app-URL shape are SEEDED
and need live validation before a real run (see ``ui_mode.selectors``).
"""

from advtest_ext.adapters.studio import recipe_ops
from advtest_ext.adapters.studio.ui_mode import (
    GeminiStudioMultiTurnTarget,
    ui_mode_invoke,
)

__all__ = [
    "GeminiStudioMultiTurnTarget",
    "recipe_ops",
    "ui_mode_invoke",
]
