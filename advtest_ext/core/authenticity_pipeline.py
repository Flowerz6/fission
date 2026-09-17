"""Wiring that turns the pure authenticity scorer into an automatic signal.

The scorer in :mod:`advtest_ext.core.authenticity` answers "is this self-
extraction leak real or hallucinated?" by comparing several runs of the same
guide. This module decides *which* guides and runs that applies to, runs the
scorer, and persists the verdict onto the affected ``Finding`` rows — so the
answer shows up on the finding automatically, with no operator action.

Called on run completion (see the orchestrator) and lazily back-filled for
legacy findings by the dashboard. Idempotent: recomputing overwrites the stored
verdict for every finding of the guide, so a new run corroborates older ones.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from advtest_ext.core.authenticity import AuthenticityReport, authenticity_report_for_runs

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.guides.models import Guide

# Threat classes whose findings are the model disclosing its OWN internals —
# the only class where "does it reproduce across runs?" is the right question.
_SELF_EXTRACTION_MARKERS = (
    "system_prompt_leak",
    "prompt_leakage",
    "tool_registry_disclosure",
    "schema_disclosure",
    "skill_disclosure",
)


def is_self_extraction(guide: Guide) -> bool:
    """Whether authenticity applies to this guide (a self-disclosure leak).

    Keyed on the authoritative ``threat_class`` only, not tags: a guide can be
    tagged ``system-prompt-leak`` for a secondary canary while being an indirect-
    injection attack at heart (e.g. docs_indirect_summary_leak), and authenticity
    does not apply there — "does it reproduce across runs?" is only the right
    question when the model is disclosing its own internals."""
    tc = (getattr(guide, "threat_class", None) or "").lower()
    return any(m in tc for m in _SELF_EXTRACTION_MARKERS)


def artifact_patterns_for_guide(guide: Guide) -> list[str]:
    """The regexes that mark a leak: the guide's own L0 success signals
    (explicit ``pattern``, else an escaped ``token``/``canary``)."""
    patterns: list[str] = []
    for sig in guide.success.signals:
        pat = sig.get("pattern")
        tok = sig.get("token") or sig.get("canary")
        if pat:
            patterns.append(str(pat))
        elif tok:
            patterns.append(re.escape(str(tok)))
    return patterns


def _load_guide(guide_id: str, guides_dir: Path | None) -> Guide | None:
    from advtest_ext.guides.corpus import load_guides

    gdir = guides_dir or (Path(__file__).resolve().parents[2] / "guides")
    if not gdir.exists():
        return None
    return next((g for g in load_guides(gdir) if g.id == guide_id), None)


def recompute_guide_authenticity(
    session_factory: Any,
    guide_id: str,
    *,
    guides_dir: Path | None = None,
    min_consistency: float = 0.5,
    min_runs: int = 2,
) -> AuthenticityReport | None:
    """Score authenticity for a self-extraction guide across all its runs that
    produced a finding, and write the verdict onto every one of those findings.

    Returns the report, or ``None`` when it does not apply (guide missing, not a
    self-extraction guide, no usable success signals, or no findings yet).
    """
    from advtest_ext.storage import session_scope
    from advtest_ext.storage.models import Finding

    guide = _load_guide(guide_id, guides_dir)
    if guide is None or not is_self_extraction(guide):
        return None
    patterns = artifact_patterns_for_guide(guide)
    if not patterns:
        return None

    # Every run of this guide that has at least one finding — those are the runs
    # with a leak worth corroborating (a run with no success contributes nothing).
    with session_scope(session_factory) as s:
        finding_rows = list(
            s.query(Finding.id, Finding.run_id).join(Finding.run).filter_by(guide_id=guide_id).all()
        )
    if not finding_rows:
        return None
    run_ids = sorted({run_id for _, run_id in finding_rows})

    report = authenticity_report_for_runs(
        session_factory,
        run_ids,
        artifact_patterns=patterns,
        min_consistency=min_consistency,
        min_runs=min_runs,
    )
    payload = {
        **report.to_dict(),
        "run_ids": run_ids,
        "guide_id": guide_id,
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with session_scope(session_factory) as s:
        for finding_id, _ in finding_rows:
            row = s.get(Finding, finding_id)
            if row is not None:
                row.authenticity = payload
    return report
