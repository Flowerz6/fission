"""An optional JSON overlay for ui_mode side-panel selectors.

Selectors drift whenever Google ships a Workspace UI change. Rather than edit
``selectors.py`` by hand every time, the heal loop (see
:mod:`advtest_ext.adapters._ui_mode_common.selector_heal`) writes an overlay
file and each app's ``selectors.py`` merges it on top of its hard-coded
defaults at import. Point ``ADVTEST_SELECTOR_OVERLAY`` at the JSON file.

Overlay shape — keyed by app, then by selector name::

    {
      "google_docs": {"side_panel_button": "button[aria-label='Ask Gemini']"},
      "google_sheets": {"side_panel_button": "..."}
    }

Only keys already present in the base registry are ever applied, so an overlay
can *correct* a selector but never introduce a stray one.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_ENV_OVERLAY = "ADVTEST_SELECTOR_OVERLAY"


def _overlay_path() -> Path | None:
    raw = os.environ.get(_ENV_OVERLAY)
    return Path(raw) if raw else None


def load_overlay(app: str, *, path: Path | None = None) -> dict[str, str]:
    """Overlay entries for ``app`` (empty when no overlay / no file / no
    entries). Non-string values are ignored so a malformed file degrades to
    the base selectors rather than breaking the run."""
    resolved = path or _overlay_path()
    if resolved is None or not resolved.exists():
        return {}
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    app_entries = data.get(app, {}) if isinstance(data, dict) else {}
    if not isinstance(app_entries, dict):
        return {}
    return {k: v for k, v in app_entries.items() if isinstance(v, str)}


def resolve_selectors(
    base: Mapping[str, Any], app: str, *, path: Path | None = None
) -> dict[str, str]:
    """``base`` with the overlay for ``app`` merged on top — but only for keys
    that already exist in ``base`` (an overlay corrects, never extends).

    ``base`` is typically a ``SidePanelSelectors`` TypedDict; values are
    coerced to ``str`` so the result is a plain ``dict[str, str]``."""
    merged: dict[str, str] = {str(k): str(v) for k, v in base.items()}
    for key, selector in load_overlay(app, path=path).items():
        if key in merged:
            merged[key] = selector
    return merged


def write_overlay(app: str, fixes: Mapping[str, str], *, path: Path) -> dict[str, dict[str, str]]:
    """Merge ``fixes`` into the overlay JSON at ``path`` under ``app`` and
    write it back (creating the file/dirs if needed). Returns the new document."""
    doc: dict[str, dict[str, str]] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                doc = {k: dict(v) for k, v in loaded.items() if isinstance(v, dict)}
        except (json.JSONDecodeError, OSError):
            doc = {}
    doc.setdefault(app, {}).update(fixes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc


__all__ = ["load_overlay", "resolve_selectors", "write_overlay"]
