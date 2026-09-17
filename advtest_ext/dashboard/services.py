"""Service selection and canonical workspace surfaces for dashboard reads.

A service is a presentation filter over complete campaigns, not an access-control
boundary. Campaign evidence and exports keep their original, complete contents.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from advtest_ext.storage.models import Finding, GoldLabel, HitlItem, Run, Trace

SERVICE_LABELS = {
    "gemini": "Gemini",
    "openai": "OpenAI",
    "claude": "Claude",
    "other": "Other targets",
    "all": "All services",
}
GEMINI_SURFACES = {
    "docs": "Docs",
    "sheets": "Sheets",
    "gmail": "Gmail",
    "slides": "Slides",
    "drive": "Drive",
    "calendar": "Calendar",
}
COLLECTION_PATHS = {"/", "/runs", "/findings", "/reports", "/surfaces", "/judge"}


def target_identity(target_name: str) -> tuple[str, str, str]:
    """Normalize known adapter aliases, preserving unknown targets separately."""
    name = re.sub(r"[^a-z0-9]", "", target_name.lower())
    for surface, label in GEMINI_SURFACES.items():
        prefixes = (f"google{surface}", f"gemini{surface}")
        if name.startswith(prefixes) or name in (surface, f"{surface}target"):
            return "gemini", surface, label
    if name.startswith("gemini"):
        return "gemini", "assistant", "Gemini assistant"
    if name.startswith(("openai", "chatgpt")):
        return "openai", "assistant", "Assistant"
    if name.startswith(("anthropic", "claude")):
        return "claude", "assistant", "Assistant"
    words = re.sub(r"(?<!^)(?=[A-Z0-9])", " ", target_name).split()
    if words and words[-1] == "Target":
        words.pop()
    return (
        "other",
        hashlib.sha256(target_name.encode()).hexdigest()[:16],
        " ".join(words) or target_name,
    )


def service_catalog(session: Session) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Known configured Gemini integration plus services found in real campaigns."""
    trace_services: dict[str, set[str]] = defaultdict(set)
    for run_id, target in session.query(Trace.run_id, Trace.target_name).distinct():
        service = target_identity(target)[0]
        if service != "other":
            trace_services[run_id].add(service)
    assignments: dict[str, str] = {}
    for run_id, target in session.query(Run.id, Run.target_name):
        service = target_identity(target)[0]
        # Older imports sometimes stored a generic run name but a precise trace target.
        if service == "other" and len(trace_services[run_id]) == 1:
            service = next(iter(trace_services[run_id]))
        assignments[run_id] = service
    keys = {"gemini", *assignments.values()}
    options = [{"key": key, "label": SERVICE_LABELS[key]} for key in SERVICE_LABELS if key in keys]
    options.append({"key": "all", "label": SERVICE_LABELS["all"]})
    return options, assignments


def scope_campaign_reads(session: Session, run_ids: list[str]) -> None:
    """Filter collection queries before LIMIT, including aggregates and options.

    Select complete runs so cross-surface workflows retain all their evidence.
    Listener belongs to this request's session and never changes stored rows.
    """
    criteria = [with_loader_criteria(Run, Run.id.in_(run_ids), include_aliases=True)]
    for model in (Trace, Finding, HitlItem, GoldLabel):
        criteria.append(
            with_loader_criteria(model, model.run_id.in_(run_ids), include_aliases=True)
        )

    def apply_scope(state: ORMExecuteState) -> None:
        if state.is_select:
            state.statement = state.statement.options(*criteria)

    event.listen(session, "do_orm_execute", apply_scope)


def group_surfaces(
    session: Session, surfaces: list[dict[str, Any]], service: str
) -> list[dict[str, Any]]:
    """Merge adapter variants; count each campaign once per canonical surface."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    run_ids: dict[str, set[str]] = defaultdict(set)
    for target, run_id in session.query(Trace.target_name, Trace.run_id).distinct():
        run_ids[target].add(run_id)

    def group_for(owner: str, key: str, label: str) -> dict[str, Any]:
        return groups.setdefault(
            (owner, key),
            {
                "service": owner,
                "service_label": SERVICE_LABELS[owner],
                "key": key,
                "label": label,
                "attempts": 0,
                "findings": 0,
                "run_ids": set(),
                "severities": defaultdict(int),
                "adapters": [],
                "configured": not (owner == "gemini" and key == "drive"),
            },
        )

    # Known surfaces stay visible before their first test. Drive has no runnable
    # adapter yet, so it is explicitly marked Not configured rather than active.
    if service in ("gemini", "all"):
        for key, label in GEMINI_SURFACES.items():
            group_for("gemini", key, label)
    for surface in surfaces:
        owner, key, label = target_identity(surface["target_name"])
        group = group_for(owner, key, label)
        group["adapters"].append(surface)
        group["attempts"] += surface["attempts"]
        group["findings"] += surface["findings"]
        group["run_ids"].update(run_ids[surface["target_name"]])
        group["configured"] = True
        for severity, count in surface["sev_chips"]:
            group["severities"][severity] += count
    order = {"crit": 0, "critical": 0, "high": 1, "med": 2, "medium": 2, "low": 3}
    for group in groups.values():
        group["campaign_ids"] = sorted(group.pop("run_ids"))
        group["runs"] = len(group["campaign_ids"])
        group["sev_chips"] = sorted(
            group.pop("severities").items(), key=lambda item: order.get(item[0], 9)
        )
        if group["findings"]:
            group["exposure"], group["exposure_label"] = "exposed", "Exposed"
        elif group["attempts"]:
            group["exposure"], group["exposure_label"] = "mitigated", "Tested · no findings"
        else:
            group["exposure"], group["exposure_label"] = (
                "untested",
                "Not tested" if group["configured"] else "Not configured",
            )
    return list(groups.values())
