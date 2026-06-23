"""Shared exceptions for Workspace ui_mode adapters."""

from __future__ import annotations


class SidePanelUnavailable(RuntimeError):  # noqa: N818 — public-contract name
    """Raised when the Gemini side-panel automation can't complete.

    Covers every "the DOM didn't look the way we expected" case (button
    missing, input not focusable, loading never finishes, response
    container absent). The message is intentionally a single canonical
    string so the demo operator knows exactly where to look — selector
    drift is the dominant failure mode for headed Workspace automation.

    Shared across product invokers (Gmail, Google Docs, Outlook,
    future Sheets / Calendar / Slides) so the orchestrator's catch
    can be a single ``except SidePanelUnavailable`` no matter which
    adapter is driving.
    """
