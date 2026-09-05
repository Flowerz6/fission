"""Human-readable presentation metadata derived from persisted run data."""

from __future__ import annotations

from typing import Any


def surface_key(target_name: str) -> str:
    name = (target_name or "").lower()
    if any(token in name for token in ("cross", "outlook", "gmail", "email")):
        return "cross-surface"
    if "sheet" in name:
        return "sheets"
    if "doc" in name:
        return "docs"
    return "other"


def surface_label(target_name: str) -> str:
    return {
        "sheets": "Google Sheets",
        "docs": "Google Docs",
        "cross-surface": "Cross-surface",
        "other": "Other integrations",
    }[surface_key(target_name)]


def run_surface_key(run: Any) -> str:
    """Classify a run, accounting for multi-surface workflow strategy."""
    if str(getattr(run, "strategy", "") or "").lower() == "demo_flow":
        return "cross-surface"
    return surface_key(str(getattr(run, "target_name", "") or ""))


def run_surface_label(run: Any) -> str:
    return {
        "sheets": "Google Sheets",
        "docs": "Google Docs",
        "cross-surface": "Cross-surface",
        "other": "Other integrations",
    }[run_surface_key(run)]


def run_display_name(run: Any) -> str:
    config = run.config or {}
    explicit = config.get("display_name") or config.get("run_name")
    if explicit:
        return str(explicit)
    strategy = str(run.strategy or "").replace("_", " ").strip()
    method = {
        "grid": "Technique exploration",
        "tap": "Branching exploration",
        "guided": "Guided playbook assessment",
    }.get(strategy.lower(), strategy.title() or "Assessment")
    return f"{run_surface_label(run)} · {method}"


__all__ = [
    "run_display_name",
    "run_surface_key",
    "run_surface_label",
    "surface_key",
    "surface_label",
]
