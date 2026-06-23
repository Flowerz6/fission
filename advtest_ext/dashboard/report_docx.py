"""Per-Finding docx report writer.

Mirrors the structure of the Google Red Team Report
(:doc:`platform-plan` § Sidebar: "the dataset-payload flow")
so a Finding produced by the platform slots directly into the
security team's existing reporting flow — the artefact comes
OUT of the platform in the same shape the human-authored
artefacts came IN.

Per-Finding output sections, in order:

- **V<N> - <title>** (heading 1) — derived from the Finding's
  promoted trace (technique x behaviour) or the Recipe name
  when the Finding came from a Recipe Run.
- **OVERVIEW** — the behaviour's description + a one-line
  outcome summary.
- **METHODOLOGY** — account pool, attack name + technique, or
  Recipe steps when available.
- **FINDINGS** — a metric table: judge scores, signal verdicts,
  intercepted-traffic shape, trace verdict.
- **RISKS** — the behaviour's success_criteria as a starting
  point; operators edit downstream.
- **PRIMARY RECOMMENDATION** — placeholder boilerplate for
  operator-supplied content.
- **POC** — the trace's request / response excerpts, screenshot
  + intercepted-capture path references.

No external dependencies — the docx file format is a zip with
XML inside; we build both with the standard library. Same
shape choice as :mod:`advtest_ext.core.loaders.docx_loader`
(reader is stdlib too) so the round-trip (Recipe → run → docx
report → re-ingest) keeps the dependency surface flat.

Phase 7 of the :doc:`red_team_report_infra_plan`.
"""

from __future__ import annotations

import html
import io
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.storage.models import Finding, RecipeRow, Trace


# Word's XML namespaces.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


# ----------------------------------------------------------------------
# Low-level docx primitives — small builders that emit the WordprocessingML
# fragments we need. Kept minimal; only the element shapes the report
# actually uses (heading, paragraph, bullet, table) are supported.
# ----------------------------------------------------------------------


def _heading(text: str, level: int = 1) -> str:
    """Render a heading paragraph. Word maps ``Heading 1`` /
    ``Heading 2`` style names to the same h1/h2 the operator
    sees in the Styles panel — operators can re-style the
    output via the docx template."""
    style = f"Heading{level}"
    return (
        "<w:p>"
        f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{html.escape(text)}</w:t></w:r>'
        "</w:p>"
    )


def _paragraph(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{html.escape(text)}</w:t></w:r></w:p>'


def _bullet(text: str) -> str:
    """One bullet item. Uses the ``ListParagraph`` style which
    every Word version maps to a bullet — and falls back to a
    plain paragraph in viewers without that style."""
    return (
        "<w:p>"
        '<w:pPr><w:pStyle w:val="ListParagraph"/>'
        '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{html.escape(text)}</w:t></w:r>'
        "</w:p>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Two-column-ish data table. Each row is a list of strings
    rendered into one cell per entry. Word will lay them out
    with auto-fit widths."""

    def _cell(text: str) -> str:
        return (
            "<w:tc>"
            '<w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
            f'<w:p><w:r><w:t xml:space="preserve">{html.escape(text)}</w:t></w:r></w:p>'
            "</w:tc>"
        )

    def _row(cells: list[str]) -> str:
        return "<w:tr>" + "".join(_cell(c) for c in cells) + "</w:tr>"

    return (
        "<w:tbl>"
        '<w:tblPr><w:tblStyle w:val="TableGrid"/>'
        '<w:tblW w:w="0" w:type="auto"/></w:tblPr>'
        f"{_row(headers)}"
        + "".join(_row(r) for r in rows)
        + "</w:tbl>"
        # Empty paragraph after the table so subsequent content
        # doesn't render flush against the bottom border.
        + "<w:p/>"
    )


def _code_block(text: str) -> str:
    """Render multi-line text in a monospace-styled paragraph.

    Word's ``HTMLPreformatted`` style is preserved by every
    desktop / web client we've tested. Each line becomes one
    paragraph with that style so wrapping behaves sensibly.
    Empty lines render as blank paragraphs.
    """
    lines = text.splitlines() or [""]
    parts = []
    for line in lines:
        parts.append(
            "<w:p>"
            '<w:pPr><w:pStyle w:val="HTMLPreformatted"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{html.escape(line)}</w:t></w:r>'
            "</w:p>"
        )
    return "".join(parts)


# ----------------------------------------------------------------------
# Package-level builders — the small constellation of XML files every
# .docx needs alongside the document body.
# ----------------------------------------------------------------------


def _document_xml(body_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_W_NS}">'
        f"<w:body>{body_xml}"
        # Required closing section break.
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
        "</w:body></w:document>"
    )


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Types xmlns="{_CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )


def _serialise_docx(body_xml: str) -> bytes:
    """Zip up the four files every minimal docx needs and return
    the raw bytes — ready to ship as an HTTP response body."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("word/document.xml", _document_xml(body_xml))
    return buf.getvalue()


# ----------------------------------------------------------------------
# Finding → report mapping
# ----------------------------------------------------------------------


@dataclass
class ReportInputs:
    """Bundle of data the writer needs from the storage layer.

    Constructed by the dashboard route from the database; passed
    here so the writer doesn't import any ORM types directly.
    Recipe row is optional — Findings from the legacy
    attack-plugin path (no Recipe) get a methodology section
    derived from the trace's attack_name + technique_id
    instead.
    """

    finding: Finding
    trace: Trace
    recipe: RecipeRow | None = None
    behavior_description: str = ""
    behavior_success_criteria: str = ""
    signals: list[Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.signals is None:
            self.signals = []


def build_finding_report(inputs: ReportInputs) -> bytes:
    """Produce the docx bytes for one Finding's report.

    Sections in declared order — every section is always
    rendered (even when empty) so the operator-facing shape
    stays predictable and downstream tooling (templated style
    swaps, batch report rolls-up) can rely on it.
    """
    parts: list[str] = []
    parts.append(_title_section(inputs))
    parts.append(_heading("OVERVIEW", level=2))
    parts.extend(_overview_paragraphs(inputs))
    parts.append(_heading("METHODOLOGY", level=2))
    parts.extend(_methodology_paragraphs(inputs))
    parts.append(_heading("FINDINGS", level=2))
    parts.append(_findings_table(inputs))
    parts.append(_heading("RISKS", level=2))
    parts.extend(_risk_bullets(inputs))
    parts.append(_heading("PRIMARY RECOMMENDATION", level=2))
    parts.extend(_recommendation_paragraphs(inputs))
    parts.append(_heading("POC", level=2))
    parts.extend(_poc_paragraphs(inputs))
    return _serialise_docx("".join(parts))


# ----------------------------------------------------------------------
# Section builders — one per report section.
# ----------------------------------------------------------------------


def _title_section(inputs: ReportInputs) -> str:
    title = inputs.recipe.name if inputs.recipe is not None else _legacy_title(inputs)
    return _heading(title, level=1)


def _legacy_title(inputs: ReportInputs) -> str:
    """Title for Findings that didn't come from a Recipe (legacy
    attack-plugin path)."""
    tech = inputs.trace.technique_id or "no-technique"
    return f"{inputs.trace.attack_name} x {tech} -> {inputs.trace.behavior_id}"


def _overview_paragraphs(inputs: ReportInputs) -> list[str]:
    parts = []
    if inputs.behavior_description:
        parts.append(_paragraph(inputs.behavior_description))
    verdict = inputs.trace.verdict or "unknown"
    parts.append(
        _paragraph(
            f"Trace verdict: {verdict}. "
            f"Promoted to Finding at {inputs.finding.promoted_at:%Y-%m-%d %H:%M UTC} "
            f"with severity {inputs.finding.severity}."
        )
    )
    if inputs.finding.description:
        parts.append(_paragraph(inputs.finding.description))
    return parts


def _methodology_paragraphs(inputs: ReportInputs) -> list[str]:
    """Recipe-derived (step list) when available; otherwise the
    legacy attack-name / technique line."""
    if inputs.recipe is not None and inputs.recipe.steps:
        parts: list[str] = [
            _paragraph(
                "Recipe-driven multi-step chain. Each step below was "
                "executed in order; per-step parameters are recorded "
                "in the catalogue Recipe definition."
            ),
        ]
        for i, step in enumerate(inputs.recipe.steps, start=1):
            parts.append(
                _paragraph(
                    f"Step {i} — {step.step_id}: {step.intent} "
                    f"(operation: {step.operation}, surface: "
                    f"{step.surface.get('app', '?')}/{step.surface.get('region', '?')}"
                    f"@{step.surface.get('account_role', '?')})"
                )
            )
        return parts
    # Legacy attack-plugin path.
    return [
        _paragraph(
            f"Attack: {inputs.trace.attack_name}. "
            f"Technique: {inputs.trace.technique_id or 'none'}. "
            f"Target surface: {inputs.trace.target_name}."
        ),
        _paragraph(
            f"Behaviour under test: {inputs.trace.behavior_id}. "
            "Single-shot delivery against the target via the legacy "
            "attack-plugin orchestrator (advtest run)."
        ),
    ]


def _findings_table(inputs: ReportInputs) -> str:
    """Metric table mirroring the Google Red Team Report's
    FINDINGS section shape. Rows derive from judge scores +
    signal verdicts; one row per measurement so the operator can
    paste it directly into stakeholder writeups."""
    rows: list[list[str]] = []
    for js in inputs.trace.judge_scores:
        rows.append(
            [
                f"Judge: {js.judge_name}",
                f"score={js.score:.3f} (flagged={'yes' if js.flagged else 'no'})",
            ]
        )
    for sig in inputs.signals:
        rows.append(
            [
                f"Signal: {sig.name} ({sig.kind})",
                "fired" if sig.fired else "did not fire",
            ]
        )
    raw = inputs.trace.raw_response_data or {}
    if raw.get("intercepted_traffic_path"):
        rows.append(
            [
                "Intercepted traffic",
                f"captured at {raw['intercepted_traffic_path']}",
            ]
        )
    if raw.get("screenshot_path"):
        rows.append(
            [
                "Screenshot",
                f"captured at {raw['screenshot_path']}",
            ]
        )
    if not rows:
        rows.append(["(no measurements)", "(no signals or judge scores recorded)"])
    return _table(headers=["Metric", "Result"], rows=rows)


def _risk_bullets(inputs: ReportInputs) -> list[str]:
    parts = []
    if inputs.behavior_success_criteria:
        parts.append(_paragraph("Per the behaviour's success_criteria, this Finding indicates:"))
        parts.append(_bullet(inputs.behavior_success_criteria))
    parts.append(
        _paragraph(
            "Operator: edit this section to add organisation-specific "
            "risk language (compliance exposure, reputational impact, "
            "downstream chain-enablement of higher-severity findings, "
            "etc.) before sharing externally."
        )
    )
    return parts


def _recommendation_paragraphs(inputs: ReportInputs) -> list[str]:
    return [
        _paragraph("Short-Term (deployable this quarter, low risk): OPERATOR TO COMPLETE."),
        _paragraph("Medium-Term (architectural, this year): OPERATOR TO COMPLETE."),
        _paragraph("Long-Term (research, multi-year): OPERATOR TO COMPLETE."),
    ]


def _poc_paragraphs(inputs: ReportInputs) -> list[str]:
    """User prompt + model response excerpts + paths to the raw
    artefacts. Operators with python-docx can post-process the
    file to inline the screenshots / intercepted JSONL excerpts
    — for now we link to them so the report stays self-
    contained without binary embedding."""
    parts: list[str] = []
    request = inputs.trace.raw_request or ""
    response = inputs.trace.raw_response or ""
    parts.append(_paragraph("User prompt typed in the panel:"))
    parts.append(_code_block(request if request.strip() else "(empty)"))
    parts.append(_paragraph("Model response (rendered side-panel text):"))
    parts.append(_code_block(_truncate(response, 4000) or "(empty)"))
    raw = inputs.trace.raw_response_data or {}
    artefact_lines: list[str] = []
    if raw.get("screenshot_path"):
        artefact_lines.append(f"Screenshot: {raw['screenshot_path']}")
    if raw.get("intercepted_traffic_path"):
        artefact_lines.append(
            f"Intercepted Gemini traffic (JSONL): {raw['intercepted_traffic_path']}"
        )
    if inputs.finding.replay_bundle_path:
        artefact_lines.append(f"Replay bundle (ZIP): {inputs.finding.replay_bundle_path}")
    if artefact_lines:
        parts.append(_paragraph("Artefacts on disk:"))
        for line in artefact_lines:
            parts.append(_bullet(line))
    return parts


def _truncate(text: str, limit: int) -> str:
    """Cap long response bodies so the docx doesn't balloon past
    a reviewable length. Ellipsis hints the reader to look at
    the replay bundle for the full text."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[... truncated; see replay bundle for full response ...]"
