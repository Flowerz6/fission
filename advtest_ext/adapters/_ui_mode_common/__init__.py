"""Shared infrastructure across per-product ui_mode adapters.

Modules in this package are intentionally product-agnostic — they
should behave identically whether driven by the Docs, Gmail,
Sheets, or Calendar Gemini side panel. Product-specific selectors
and invokers continue to live under
``adapters/<product>/ui_mode/``; only logic that legitimately
applies to every Workspace surface belongs here.
"""

from advtest_ext.adapters._ui_mode_common.errors import SidePanelUnavailable
from advtest_ext.adapters._ui_mode_common.interception import (
    WORKSPACE_GEMINI_URL_PATTERNS,
    TrafficInterceptor,
)
from advtest_ext.adapters._ui_mode_common.playwright_helpers import (
    human_click,
    human_type,
    jitter_pause,
    wait_for_button_ready,
)
from advtest_ext.adapters._ui_mode_common.session import (
    UiModeSession,
    close_context,
    launch_context,
)
from advtest_ext.adapters._ui_mode_common.session_pool import (
    SessionFactory,
    UiModeSessionPool,
)

__all__ = [
    "WORKSPACE_GEMINI_URL_PATTERNS",
    "SessionFactory",
    "SidePanelUnavailable",
    "TrafficInterceptor",
    "UiModeSession",
    "UiModeSessionPool",
    "close_context",
    "human_click",
    "human_type",
    "jitter_pause",
    "launch_context",
    "wait_for_button_ready",
]
