"""Portable PDF export of the vendor-facing evidence report."""

from __future__ import annotations

import html
import io
from pathlib import Path
from typing import Any


def render_disclosure_pdf(context: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    fonts = Path(__file__).resolve().parent / "static" / "fonts"
    for name, filename in [
        ("FissionReport", "reference-sans-regular.ttf"),
        ("FissionReportBold", "reference-sans-bold.ttf"),
    ]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / filename)))
    pdfmetrics.registerFontFamily("FissionReport", normal="FissionReport", bold="FissionReportBold")
    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName="FissionReport",
            fontSize=9,
            leading=14,
            spaceAfter=8,
            textColor=colors.HexColor("#202321"),
        ),
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="FissionReportBold",
            fontSize=26,
            leading=31,
            alignment=0,
            spaceAfter=20,
        ),
        "h1": ParagraphStyle(
            "ReportSection",
            parent=base["Heading1"],
            fontName="FissionReportBold",
            fontSize=16,
            leading=21,
            spaceBefore=16,
            spaceAfter=10,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "ReportSubsection",
            parent=base["Heading2"],
            fontName="FissionReportBold",
            fontSize=11,
            leading=15,
            spaceBefore=14,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "small": ParagraphStyle(
            "ReportSmall",
            parent=base["BodyText"],
            fontName="FissionReport",
            fontSize=8,
            leading=11,
            spaceAfter=6,
            textColor=colors.HexColor("#636b66"),
        ),
        "evidence": ParagraphStyle(
            "ReportEvidence",
            parent=base["BodyText"],
            fontName="FissionReport",
            fontSize=8,
            leading=12,
            spaceAfter=5,
            backColor=colors.HexColor("#f5f6f3"),
            borderPadding=7,
        ),
    }
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=context["report_title"],
        author=context["author"],
    )
    story = []

    def paragraph(text: Any, style: str = "body") -> None:
        story.append(Paragraph(html.escape(str(text or "")).replace("\n", "<br/>"), styles[style]))

    def heading(text: str, level: int = 1) -> None:
        paragraph(text, "h1" if level == 1 else "h2")

    def evidence(text: str) -> None:
        if not text:
            paragraph("Not recorded.", "small")
            return
        # Small flowing paragraphs avoid an unsplittable payload or a page-sized row.
        for offset in range(0, len(text), 1800):
            paragraph(text[offset : offset + 1800], "evidence")

    def table(headers: list[str], rows: list[list[Any]], widths: list[float] | None = None) -> None:
        def cells(row: list[Any]) -> list[Any]:
            return [Paragraph(html.escape(str(cell)), styles["small"]) for cell in row]

        data = [cells(headers)] + [cells(row) for row in rows]
        item = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        item.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeefe9")),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dfe2db")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(item)

    def count(value: int, singular: str, plural: str | None = None) -> str:
        return f"{value} {singular if value == 1 else (plural or singular + 's')}"

    def timestamp(value: Any) -> str:
        return (
            value.strftime("%d %b %Y, %H:%M UTC")
            if hasattr(value, "strftime")
            else str(value or "Not recorded")
        )

    paragraph("F I S S I O N", "small")
    story.append(Spacer(1, 16 * mm))
    paragraph("SECURITY ASSESSMENT · EVIDENCE REPORT", "small")
    paragraph(context["report_title"], "title")
    paragraph(context["report_status"])
    table(
        ["Report information", ""],
        [
            ["Prepared for", context["recipient"]],
            ["Prepared by", context["author"]],
            ["Contact", context["contact"]],
            ["Reference", context["report_id"]],
            ["Generated", context["generated_at"].strftime("%d %b %Y, %H:%M UTC")],
        ],
        [110, doc.width - 110],
    )
    story.append(PageBreak())
    heading("01 Executive summary")
    paragraph(
        f"{count(context['total'], 'selected finding')} from {count(context['source_runs'], 'source run')}. "
        f"Matching human-reviewed responses: {context['human_reviewed']}. Multiple observations may describe the same underlying issue."
    )
    if context["cases"]:
        table(
            ["Case", "Finding", "Severity", "Validation"],
            [[c["number"], c["title"], c["severity"], c["validation"]] for c in context["cases"]],
            [40, doc.width - 210, 55, 115],
        )
    else:
        paragraph("No findings match the selected filters.")
    heading("02 Scope and environment")
    paragraph(
        "Execution counts cover complete source runs, not independent reproduction trials.", "small"
    )
    filters = context["filters"]
    for key, label in [
        ("severities", "Severities"),
        ("scenarios", "Scenarios"),
        ("workspace", "Workspace"),
        ("date_from", "From"),
        ("date_to", "To"),
        ("run_id", "Run"),
    ]:
        value = filters.get(key)
        if value:
            paragraph(f"{label}: {', '.join(value) if isinstance(value, list) else value}", "small")
    for run in context["scope"]:
        heading(f"{run['service']} · {run['surface']}", 2)
        paragraph(f"{run['name']} | {run['id']} | {run['target']}")
        paragraph(
            f"Test window: {timestamp(run['started'])} to {timestamp(run['finished']) if run['finished'] else 'Unfinished'}",
            "small",
        )
        paragraph(
            f"{count(run['attempts'], 'attempt')} · {count(run['successful'], 'automated success', 'automated successes')} · {count(run['errors'], 'execution error')} · {count(run['conversations'], 'conversation')}"
        )
    heading("03 Method and limitations")
    paragraph(
        "Fission records prompts, payloads, model responses, evaluator results, and promoted findings. Each case includes the complete recorded conversation containing the finding."
    )
    for note in context["report_limitations"]:
        paragraph("• " + note)
    paragraph(
        "Severity is the dashboard's assigned assessment priority, not a vendor rating or CVSS score. Current guide instructions supplement the saved evidence and do not prove which setup steps were executed."
    )
    for case in context["cases"]:
        story.append(PageBreak())
        paragraph(f"{case['number']} · Assigned severity: {case['severity']}", "small")
        heading(case["title"])
        paragraph(case["validation"])
        heading("Observation and test objective", 2)
        paragraph(case["description"])
        paragraph("Objective: " + case["objective"])
        paragraph("Success criterion (guide reference): " + case["criterion"])
        heading("Affected environment", 2)
        table(
            ["Field", "Recorded value"],
            [
                ["Product / adapter", case["target"]],
                ["Model version", case["model"]],
                ["Method", case["method"]],
                ["Detected", timestamp(case["detected"])],
                ["Finding", case["id"]],
                ["Trace", case["trace_id"]],
                ["Source run", case["run_id"]],
            ],
            [105, doc.width - 105],
        )
        heading("Security impact and expected behavior", 2)
        paragraph(
            "The transcript records model behavior. A human assessment of the trust boundary crossed, attacker prerequisites, affected data or actions, and resulting security impact requires author completion."
        )
        heading("Reproduction", 2)
        if case["guide"].get("setup"):
            paragraph(
                "Current guide setup reference; confirm against the tested environment.", "small"
            )
            for i, step in enumerate(case["guide"]["setup"], 1):
                paragraph(f"{i}. {step}")
        for i, step in enumerate(
            [
                "Confirm the product, model version, account permissions, and attacker control of the input surface.",
                "Apply the recorded payload in the original input surface, where applicable.",
                "Replay the following user prompts in order, retaining conversation context.",
                "Compare the marked finding turn with the stated success criterion. Record repeat trials separately.",
            ],
            1,
        ):
            paragraph(f"{i}. {step}")
        heading("Recorded payload", 2)
        evidence(case["payload"])
        heading("Complete recorded conversation")
        for index, turn in enumerate(case["turns"], 1):
            marker = " · FINDING RECORDED HERE" if turn.trace_id == case["trace_id"] else ""
            heading(f"Turn {index} · {turn.verdict or 'Unscored'}{marker}", 2)
            paragraph("Trace: " + turn.trace_id, "small")
            if turn.score is not None:
                paragraph(f"Evaluator score: {turn.score:.2f}", "small")
            paragraph("User prompt", "small")
            evidence(turn.prompt)
            paragraph("Model response", "small")
            evidence(turn.response)
        if not case["turns"]:
            paragraph("The source conversation is unavailable.")
        heading("Evaluation evidence", 2)
        for score in case["scores"]:
            paragraph(
                f"{score.judge_name} · {score.score:.2f} · {'Flagged' if score.flagged else 'Not flagged'}"
            )
            paragraph(score.reasoning or "No evaluator reasoning recorded.")
        if not case["scores"]:
            paragraph("No judge scores recorded.")
        heading("Verification and remediation follow-up", 2)
        for note in case["limitations"]:
            paragraph("• " + note)
        paragraph("Proposed remediation, owner, and retest results require author completion.")
        heading("Evidence manifest", 2)
        paragraph(
            "SHA-256 hashes identify the UTF-8 evidence text, not an independently signed capture.",
            "small",
        )
        if case["payload_sha256"]:
            paragraph("Payload: " + case["payload_sha256"], "small")
        for item in case["artifacts"]:
            paragraph("Trace: " + item["trace_id"], "small")
            paragraph("Prompt SHA-256: " + item["prompt_sha256"], "small")
            paragraph("Response SHA-256: " + item["response_sha256"], "small")
        paragraph(
            "Replay bundle: "
            + ("recorded separately; not embedded" if case["replay_recorded"] else "not recorded")
            + ".",
            "small",
        )

    def footer(canvas: Any, document: Any) -> None:
        canvas.setCreator("Fission")
        canvas.saveState()
        canvas.setFont("FissionReport", 8)
        canvas.setFillColor(colors.HexColor("#727972"))
        canvas.drawString(18 * mm, 12 * mm, context["report_id"])
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Fission · {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
