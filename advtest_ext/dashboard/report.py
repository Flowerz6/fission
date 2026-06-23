"""Per-run PDF report assembly.

Sections (mirrored in ``report.html``):

1. Cover — run id, target, dates, headline ASR.
2. Executive summary — overall numbers + heatmap thumbnail.
3. Methodology — attacks used, judges, with paper citations.
4. Findings table — severity, behaviour, judge scores.
5. Selected reproducible traces — short-listed verdict='success'
   traces with prompt/response excerpts.
6. Conversations — the FULL multi-turn chats, one block per tree
   node / replay round, prompts + Gemini replies verbatim.
7. References — references.bib entries cited anywhere in the run.

WeasyPrint is gated behind the optional ``pdf`` extra. The HTML
version of the report is always available via ``/runs/{id}/report``;
the PDF route 501s with an install hint when WeasyPrint isn't on
the path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from advtest_ext.dashboard.references import BibEntry, parse_bibfile
from advtest_ext.dashboard.registry import list_attacks
from advtest_ext.dashboard.transcript import build_transcript_groups
from advtest_ext.dashboard.tree import build_tree_forest
from advtest_ext.storage import (
    asr_heatmap_formatted,
    list_findings,
    list_traces_for_run,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

    from advtest_ext.storage.models import Run


try:  # pragma: no cover - import-time probe; tested via WEASYPRINT_INSTALLED
    import weasyprint as _weasyprint

    _ = _weasyprint  # silence "imported but unused" while keeping the probe
    WEASYPRINT_INSTALLED = True
except Exception:
    WEASYPRINT_INSTALLED = False


def _cited_keys(traces: list[Any]) -> set[str]:
    """Citation keys used by the attacks present in ``traces``.
    Joined against the registry so the report doesn't drag the full
    references.bib in — only what's actually relevant to the run."""
    used_attacks = {t.attack_name for t in traces}
    out: set[str] = set()
    for meta in list_attacks():
        if meta.name in used_attacks:
            out.update(meta.references)
    return out


def build_report_context(
    *,
    session: Session,
    run: Run,
    selected_trace_count: int = 5,
) -> dict[str, Any]:
    """Assemble the Jinja context for ``report.html``. Pure: no I/O
    beyond the SQLAlchemy session + the bibfile read; safe to call
    from tests without spinning a WeasyPrint render."""
    traces = list_traces_for_run(session, run.id)
    findings = list_findings(session, run_id=run.id)

    # Short-list a handful of successful traces for the body. Sort by
    # the highest judge score so the most striking ones surface first.
    successes = [t for t in traces if t.verdict == "success"]
    successes.sort(
        key=lambda t: max((s.score for s in t.judge_scores), default=0.0),
        reverse=True,
    )
    selected_traces = successes[:selected_trace_count]

    cited_keys = _cited_keys(traces)
    all_refs: list[BibEntry] = parse_bibfile()
    references = [b for b in all_refs if b.citation_key in cited_keys]

    attacks_metadata = [a for a in list_attacks() if a.name in {t.attack_name for t in traces}]

    # One ASCII tree per (behaviour, technique) cell, same builder
    # that powers the live ``/runs/{id}/tree`` view. The PDF embeds
    # the tree(1)-style rendering rather than the dashboard's
    # Mermaid view because WeasyPrint doesn't execute JS.
    tree_forest = build_tree_forest(list(traces))

    # FULL multi-turn conversations, one group per tree node / replay
    # round — same grouping the live transcript view uses. The report
    # renders every turn's prompt + Gemini reply verbatim, so the PDF
    # is a faithful per-chat record. ``raw_response`` (the rendered DOM
    # capture) is the source of truth; the intercepted-stream fallback
    # would need per-trace file reads, which the report builder avoids.
    conversations = build_transcript_groups(list(traces), {})

    return {
        "run": run,
        "summary": run.summary or {},
        "heatmap": asr_heatmap_formatted(session, run.id),
        "findings": findings,
        "traces": traces,
        "selected_traces": selected_traces,
        "attacks": attacks_metadata,
        "tree_forest": tree_forest,
        "conversations": conversations,
        "references": references,
    }


def render_pdf(html: str, *, base_url: str | None = None) -> bytes:
    """Render an HTML string into a PDF via WeasyPrint.

    Raises :class:`RuntimeError` (not ImportError) when WeasyPrint
    isn't installed so callers don't have to know whether a missing
    native dep manifests as ``ImportError`` or ``OSError`` from
    cairo at run-time."""
    if not WEASYPRINT_INSTALLED:
        raise RuntimeError("weasyprint is not installed; install the optional 'pdf' extra")
    import weasyprint

    return bytes(weasyprint.HTML(string=html, base_url=base_url).write_pdf())


__all__ = [
    "WEASYPRINT_INSTALLED",
    "build_report_context",
    "render_pdf",
]
