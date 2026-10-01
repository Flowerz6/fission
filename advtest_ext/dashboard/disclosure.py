"""Evidence-led, vendor-facing reports built from saved findings and reviews."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from advtest_ext.dashboard.presentation import (
    finding_method,
    finding_severity,
    finding_title,
    objective_label,
    run_display_name,
)
from advtest_ext.dashboard.services import SERVICE_LABELS, target_identity
from advtest_ext.dashboard.transcript import build_transcript_groups
from advtest_ext.storage import get_run, list_traces_for_run
from advtest_ext.storage.models import GoldLabel


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _recorded_model(run: Any, trace: Any) -> str:
    sources = [
        (trace.raw_response_data or {}) if trace else {},
        (run.config or {}).get("target_options") or {},
    ]
    for source in sources:
        for key in ("model_version", "model_id", "model_name", "model"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return "Not recorded"


def _guide_reference(behavior_id: str) -> dict[str, Any]:
    """Current catalog references supplement evidence; never imply recorded setup."""
    from advtest_ext.guides.corpus import load_guide

    # Stored scenario identifiers are not filesystem paths.
    if not behavior_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in behavior_id
    ):
        return {}
    directory = Path(__file__).resolve().parents[2] / "guides"
    for suffix in (".yaml", ".yml", ".json"):
        path = directory / (behavior_id + suffix)
        if path.is_file():
            try:
                guide = load_guide(path)
                return {
                    "objective": guide.objective,
                    "criterion": guide.success.rubric,
                    "threat_class": guide.threat_class,
                    "surface": guide.target.surface,
                    "setup": [step.intent for step in guide.setup_steps],
                }
            except (ValueError, OSError):
                return {}
    return {}


def enrich_disclosure_context(
    session: Session,
    context: dict[str, Any],
    *,
    recipient: str | None = None,
    author: str | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    findings = context["findings"]
    run_ids = list(dict.fromkeys(f.run_id for f in findings))
    if context["filters"].get("run_id") and context["filters"]["run_id"] not in run_ids:
        run_ids.append(context["filters"]["run_id"])
    runs = {rid: get_run(session, rid) for rid in run_ids}
    runs = {rid: run for rid, run in runs.items() if run is not None}
    traces = {rid: list_traces_for_run(session, rid) for rid in runs}
    # Reuse the same shared-capture guard and response selection as the dashboard.
    from advtest_ext.dashboard.app import _full_responses_for_traces

    groups = {
        rid: build_transcript_groups(ts, _full_responses_for_traces(ts))
        for rid, ts in traces.items()
    }
    by_trace = {
        turn.trace_id: group for gs in groups.values() for group in gs for turn in group.turns
    }
    trace_ids = [f.trace_id for f in findings]
    reviews: dict[str, list[Any]] = {}
    if trace_ids:
        for label in session.scalars(
            select(GoldLabel)
            .where(GoldLabel.trace_id.in_(trace_ids))
            .order_by(GoldLabel.reviewed_at.desc())
        ):
            reviews.setdefault(label.trace_id, []).append(label)
    cases = []
    scope = []
    for rid, run in runs.items():
        service, _, surface = target_identity(run.target_name)
        counts = Counter(t.verdict or "unscored" for t in traces[rid])
        scope.append(
            {
                "id": rid,
                "name": run_display_name(run),
                "target": run.target_name,
                "service": SERVICE_LABELS[service],
                "surface": surface,
                "started": run.started_at,
                "finished": run.finished_at,
                "attempts": len(traces[rid]),
                "successful": counts["success"],
                "errors": counts["error"],
                "conversations": len(groups[rid]),
            }
        )
    for index, finding in enumerate(findings, 1):
        trace = finding.trace
        run = runs.get(finding.run_id)
        guide = _guide_reference(trace.behavior_id) if trace else {}
        group = by_trace.get(finding.trace_id)
        labels = reviews.get(finding.trace_id, [])
        current_labels = [
            label
            for label in labels
            if trace and label.response_snapshot and label.response_snapshot == trace.raw_response
        ]
        verdicts = sorted({label.expert_verdict for label in current_labels})
        if len(verdicts) > 1:
            validation = "Human reviews disagree"
        elif verdicts:
            validation = "Human-reviewed response: " + verdicts[0]
        elif labels:
            validation = "Human review recorded; current response not matched"
        else:
            validation = "Automated finding; human review pending"
        payload = trace.payload_text or "" if trace else ""
        limitations = []
        model = _recorded_model(run, trace) if run else "Not recorded"
        if model == "Not recorded":
            limitations.append("Exact model version was not captured.")
        if not current_labels:
            limitations.append("No matching human-reviewed response is available for this finding.")
        limitations.append(
            "Independent replay reliability and production impact have not been established by this report."
        )
        if not guide.get("setup"):
            limitations.append(
                "Environment setup and attacker permissions require author completion."
            )
        cases.append(
            {
                "number": f"F-{index:03d}",
                "id": finding.id,
                "title": finding_title(finding),
                "severity": finding_severity(finding),
                "method": finding_method(finding),
                "run_id": finding.run_id,
                "trace_id": finding.trace_id,
                "target": trace.target_name if trace else "Not recorded",
                "model": model,
                "detected": finding.promoted_at,
                "objective": guide.get("objective")
                or (objective_label(trace.behavior_id) if trace else "Not recorded"),
                "description": finding.description or "No analyst description recorded.",
                "criterion": guide.get("criterion") or "No explicit success criterion recorded.",
                "guide": guide,
                "validation": validation,
                "review_count": len(current_labels),
                "automated_verdict": trace.verdict if trace else "Not recorded",
                "payload": payload,
                "payload_sha256": _digest(payload) if payload else None,
                "turns": group.turns if group else [],
                "scores": list(trace.judge_scores) if trace else [],
                "limitations": limitations,
                "artifacts": [
                    {
                        "trace_id": turn.trace_id,
                        "prompt_sha256": _digest(turn.prompt),
                        "response_sha256": _digest(turn.response),
                    }
                    for turn in (group.turns if group else [])
                ],
                "replay_recorded": bool(finding.replay_bundle_path),
            }
        )
    stable = "\n".join(f"{c['id']}:{c['payload_sha256']}:{c['artifacts']}" for c in cases)
    context.update(
        {
            "report_title": context["report_title"]
            if context["report_title"] != "Findings report"
            else "AI security assessment",
            "report_id": "FIS-" + _digest(stable)[:12].upper(),
            "generated_at": datetime.now(UTC),
            "recipient": (recipient or "").strip() or "Not specified",
            "author": (author or "").strip() or "Not specified",
            "contact": (contact or "").strip() or "Not specified",
            "cases": cases,
            "scope": scope,
            "source_runs": len(runs),
            "human_reviewed": sum(bool(c["review_count"]) for c in cases),
            "total_attempts": sum(len(ts) for ts in traces.values()),
            "report_status": "Evidence draft — analyst review required",
            "report_limitations": [
                "Finding counts represent selected recorded observations, not a count of distinct vulnerabilities.",
                "Automated success verdicts and assigned severities are assessment signals; they do not establish vendor-confirmed impact.",
                "Conversation turns and adaptive attack attempts are not independent reproduction trials.",
            ],
        }
    )
    return context
