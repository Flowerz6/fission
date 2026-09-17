"""Human-readable presentation metadata derived from persisted run data."""

from __future__ import annotations

from typing import Any

from advtest_ext.dashboard.services import target_identity


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


# These labels describe configured objectives, never the outcome of a run.
_OBJECTIVE_LABELS = {
    "leak_system_prompt": "System prompt extraction",
    "sheets_skill_leak": "Skill disclosure",
    "gmail_0090911_tool_disclosure": "Tool disclosure",
    "docs_indirect_summary_leak": "Summary injection",
    "exfil_doc_via_image_url": "Data exfiltration via image URL",
    "fake_meeting_time_in_email_summary": "Summary manipulation",
    "override_summarise_to_translate": "Summary instruction override",
    "ignore_user_instruction_entirely": "User instruction override",
    "inject_fake_system_message": "System message spoofing",
    "inject_phishing_link_in_reply": "Phishing link injection",
    "malicious_summary_bias": "Summary bias",
    "exfil_personal_data_via_search_personalization": "Personal data exfiltration",
    "exfil_inbox_summary_to_attacker_link": "Inbox summary exfiltration",
}
_ATTACK_LABELS = {
    "tap_crescendo": "TAP + Crescendo",
    "tap_indirect": "TAP",
    "tap": "TAP",
    "crescendo": "Crescendo",
    "pair": "PAIR",
    "indirect_injection": "Indirect injection",
    "static_payload": "Static payload",
}


def _identifier_label(identifier: str) -> str:
    return identifier.replace("_", " ").strip().capitalize()


def attack_label(name: str) -> str:
    return _ATTACK_LABELS.get(name, _identifier_label(name)) or "Not recorded"


def objective_label(name: str) -> str:
    return _OBJECTIVE_LABELS.get(name, _identifier_label(name)) or "Not recorded"


def run_method_label(run: Any) -> str:
    """Show the configured attack method; orchestration is a fallback."""
    config = run.config or {}
    attacks = list(dict.fromkeys(config.get("attacks") or []))
    if attacks:
        return " + ".join(_ATTACK_LABELS.get(a, _identifier_label(a)) for a in attacks)
    strategy = str(run.strategy or "").strip()
    if strategy.startswith("guide:"):
        return "Guide replay"
    return {
        "grid": "Grid",
        "tap": "TAP",
        "guided": "Guided test",
        "demo_flow": "Workflow demo",
    }.get(strategy.lower(), _identifier_label(strategy) or "Security test")


def run_display_name(run: Any) -> str:
    """Name tests from saved objectives without changing their persisted data."""
    config = run.config or {}
    explicit = config.get("display_name") or config.get("run_name")
    if explicit:
        return str(explicit)
    if str(run.strategy or "").lower() == "demo_flow":
        return "Gmail → Docs · Hidden instruction test"

    _, _, surface = target_identity(str(run.target_name or ""))
    objectives = list(dict.fromkeys(config.get("behaviors") or []))
    if not objectives:
        guide = getattr(run, "guide_id", None) or config.get("guide_id")
        if not guide and str(run.strategy or "").startswith("guide:"):
            guide = run.strategy.split(":", 1)[1]
        if guide:
            objectives = [guide]

    if len(objectives) == 1:
        objective = objectives[0]
        label = _OBJECTIVE_LABELS.get(objective, _identifier_label(objective))
        return f"{surface} · {label} test"
    if set(objectives) == {"exfil_doc_via_image_url", "fake_meeting_time_in_email_summary"}:
        return f"{surface} · Exfiltration & summary manipulation test"
    if objectives:
        kind = "Prompt injection" if all(o in _OBJECTIVE_LABELS for o in objectives) else "Security"
        return f"{surface} · {kind} suite ({len(objectives)} objectives)"
    method = run_method_label(run)
    return f"{surface} · {method}" + ("" if method.lower().endswith("test") else " test")


__all__ = [
    "run_display_name",
    "run_method_label",
    "run_surface_key",
    "run_surface_label",
    "surface_key",
    "surface_label",
]
