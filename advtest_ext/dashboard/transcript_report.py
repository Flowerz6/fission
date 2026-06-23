"""Professional PDF disclosure report for a run's multi-turn transcripts.

Builds a real, paginated PDF (via reportlab) — NOT a print-to-PDF of the
dashboard HTML. Intended to hand to a vendor security team: a cover block,
an executive summary, then every multi-turn conversation laid out one
section per attack-tree node / replay round, with each turn's user prompt
and Gemini response shown VERBATIM.

Single entry point: :func:`build_transcript_pdf` -> ``bytes``.

reportlab is an optional dependency (pure-Python, ``pip install reportlab``).
The route guards on :data:`REPORTLAB_INSTALLED` and returns an install hint
when it's absent, so the rest of the dashboard never hard-depends on it.
"""

from __future__ import annotations

import html
import io
from typing import Any

from advtest_ext.dashboard.transcript import build_transcript_groups

try:  # pragma: no cover - import probe
    import reportlab as _reportlab

    _ = _reportlab
    REPORTLAB_INSTALLED = True
except Exception:
    REPORTLAB_INSTALLED = False


# Verdict → (background, text) colors for the small status chip.
_VERDICT_COLORS: dict[str, tuple[str, str]] = {
    "success": ("#e6f4ea", "#137333"),
    "refused": ("#fce8e6", "#c5221f"),
    "error": ("#fef7e0", "#b06000"),
}


def _safe(raw: str) -> str:
    """Escape text for a reportlab Paragraph and preserve line breaks.

    reportlab Paragraphs parse a mini-HTML markup, so a literal ``<`` (e.g.
    a ``<tool_response>`` payload) would break parsing — escape first, then
    turn newlines into ``<br/>`` so wrapping prose keeps its structure."""
    return html.escape(raw or "").replace("\n", "<br/>")


def build_transcript_pdf(
    *,
    run: Any,
    traces: list[Any],
    full_responses: dict[str, str] | None = None,
    summary: str | None = None,
    severity: str | None = None,
    impact: str | None = None,
    repro_steps: list[str] | None = None,
) -> bytes:
    """Render the run's multi-turn transcripts into a professional PDF.

    ``full_responses`` is the optional intercepted-stream fallback the
    transcript grouping uses when the rendered capture is empty.

    The disclosure framing (``summary`` / ``severity`` / ``impact`` /
    ``repro_steps``) is optional — when omitted, sensible defaults are
    derived from the run (an IPI-via-Workspace-grounding finding, severity
    inferred from whether any turn succeeded). Pass explicit values to
    tailor the report for a specific vendor hand-off.
    Raises :class:`RuntimeError` if reportlab isn't installed.
    """
    if not REPORTLAB_INSTALLED:
        raise RuntimeError("reportlab is not installed; run `pip install reportlab`")

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    # ---- styles -------------------------------------------------------
    base = getSampleStyleSheet()
    accent = colors.HexColor("#c0392b")
    ink = colors.HexColor("#1a1a1a")
    muted = colors.HexColor("#666666")

    h_title = ParagraphStyle(
        "ReportTitle",
        parent=base["Title"],
        fontSize=22,
        leading=26,
        textColor=ink,
        spaceAfter=2,
    )
    h_sub = ParagraphStyle(
        "ReportSub",
        parent=base["Normal"],
        fontSize=11,
        leading=15,
        textColor=muted,
        spaceAfter=14,
    )
    h_section = ParagraphStyle(
        "Section",
        parent=base["Heading1"],
        fontSize=15,
        leading=19,
        textColor=ink,
        spaceBefore=16,
        spaceAfter=6,
    )
    h_node = ParagraphStyle(
        "Node",
        parent=base["Heading2"],
        fontSize=13,
        leading=17,
        textColor=accent,
        spaceBefore=14,
        spaceAfter=2,
    )
    s_meta = ParagraphStyle(
        "Meta",
        parent=base["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=muted,
    )
    s_turnhead = ParagraphStyle(
        "TurnHead",
        parent=base["Normal"],
        fontSize=10.5,
        leading=14,
        textColor=ink,
        spaceBefore=8,
        spaceAfter=2,
    )
    s_label = ParagraphStyle(
        "Label",
        parent=base["Normal"],
        fontSize=8,
        leading=11,
        textColor=muted,
        spaceBefore=4,
        spaceAfter=1,
    )
    s_user = ParagraphStyle(
        "UserBody",
        parent=base["BodyText"],
        fontSize=9.5,
        leading=13,
        textColor=ink,
        alignment=TA_LEFT,
        backColor=colors.HexColor("#f4f6f8"),
        borderColor=colors.HexColor("#e3e8ee"),
        borderWidth=0.5,
        borderPadding=6,
        spaceAfter=2,
    )
    s_gemini = ParagraphStyle(
        "GeminiBody",
        parent=base["BodyText"],
        fontSize=9.5,
        leading=13,
        textColor=ink,
        alignment=TA_LEFT,
        backColor=colors.HexColor("#fbf7ff"),
        borderColor=colors.HexColor("#ebe0f5"),
        borderWidth=0.5,
        borderPadding=6,
        spaceAfter=2,
    )
    # Highlight styling for the conversation(s) that succeeded.
    success_green = colors.HexColor("#137333")
    h_node_success = ParagraphStyle(
        "NodeSuccess",
        parent=base["Heading2"],
        fontSize=13,
        leading=17,
        textColor=success_green,
        spaceBefore=14,
        spaceAfter=2,
    )
    s_success_banner = ParagraphStyle(
        "SuccessBanner",
        parent=base["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=success_green,
        backColor=colors.HexColor("#e6f4ea"),
        borderColor=success_green,
        borderWidth=0.5,
        borderPadding=5,
        spaceBefore=8,
        spaceAfter=6,
    )

    # ---- compose flowables --------------------------------------------
    story: list[Any] = []

    story.append(Paragraph("Adversarial Testing Report", h_title))
    story.append(
        Paragraph(
            "Indirect Prompt Injection — Multi-turn Conversation Transcripts",
            h_sub,
        )
    )

    # Metadata block (run identity + headline counts).
    total = len(traces)
    succ = sum(1 for t in traces if getattr(t, "verdict", "") == "success")
    refused = sum(1 for t in traces if getattr(t, "verdict", "") == "refused")
    errored = sum(1 for t in traces if getattr(t, "verdict", "") == "error")
    meta_rows = [
        ["Run ID", str(getattr(run, "id", "—"))],
        ["Target", str(getattr(run, "target_name", "—") or "—")],
        ["Strategy", str(getattr(run, "strategy", "—") or "—")],
        ["Started", str(getattr(run, "started_at", "") or "—")[:19]],
        ["Finished", str(getattr(run, "finished_at", "") or "—")[:19]],
        ["Turns", f"{total} total · {succ} successful · {refused} refused · {errored} errored"],
    ]
    meta_tbl = Table(
        [
            [Paragraph(f"<b>{html.escape(k)}</b>", s_meta), Paragraph(html.escape(v), s_meta)]
            for k, v in meta_rows
        ],
        colWidths=[28 * mm, 140 * mm],
    )
    meta_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#eeeeee")),
            ]
        )
    )
    story.append(meta_tbl)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=accent))

    # ---- disclosure framing (exec summary / severity / impact / repro) ---
    s_body = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontSize=10,
        leading=14,
        textColor=ink,
        spaceAfter=4,
    )
    any_success = any(getattr(t, "verdict", "") == "success" for t in traces)
    sev = severity or ("High" if any_success else "Informational")
    sev_bg, sev_fg = {
        "Critical": ("#c0392b", "#ffffff"),
        "High": ("#e67e22", "#ffffff"),
        "Medium": ("#f39c12", "#ffffff"),
        "Low": ("#95a5a6", "#ffffff"),
        "Informational": ("#dfe6e9", "#2d3436"),
    }.get(sev, ("#dfe6e9", "#2d3436"))

    default_summary = (
        "This report documents an indirect prompt-injection (IPI) assessment "
        "of the Gemini for Workspace side panel. Content planted in the user's "
        "Workspace (Drive documents / inbox) is shown to influence the "
        "assistant's behaviour when the assistant grounds on that content in "
        "response to ordinary, benign user requests — i.e. the user never types "
        "the malicious instruction; it rides in the grounded data. The full "
        "multi-turn conversations are reproduced verbatim below for review."
    )
    default_impact = (
        "An attacker who can place content where the victim's Gemini will "
        "ground on it (a shared Drive doc, a sent email) can steer the "
        "assistant's output without any action by the victim beyond a normal "
        "request — enabling content manipulation, injected links, or "
        "disclosure of model/configuration details (OWASP LLM01/LLM07)."
    )
    default_repro = [
        "Plant the payload in a Workspace resource the victim's Gemini can "
        "retrieve (e.g. a Drive document on a named project).",
        "As the victim, open the Gemini side panel on a blank/unrelated host document.",
        'Issue only benign requests (e.g. "summarise my notes on <project>") '
        "— never reference the planted instruction.",
        "Observe the assistant ground on the planted content and exhibit the "
        "injected behaviour in its reply (see transcripts).",
    ]

    story.append(Paragraph("Executive Summary", h_section))
    story.append(Paragraph(html.escape(summary or default_summary), s_body))

    story.append(Paragraph("Severity", h_section))
    story.append(
        Paragraph(
            f'<font size="10" backColor="{sev_bg}" color="{sev_fg}"> {html.escape(sev)} </font>',
            s_body,
        )
    )

    story.append(Paragraph("Impact", h_section))
    story.append(Paragraph(html.escape(impact or default_impact), s_body))

    story.append(Paragraph("Reproduction Steps", h_section))
    for i, step in enumerate(repro_steps or default_repro, 1):
        story.append(Paragraph(f"{i}. {html.escape(step)}", s_body))

    # ---- methodology (how the attack + scoring work, high level) -------
    story.append(Paragraph("Methodology", h_section))
    story.append(
        Paragraph(
            "<b>Attacker (automated LLM).</b> An attacker model drives the "
            "conversation. It opens with an entirely benign request, then on "
            "each turn reads the assistant's last reply and takes one small "
            "step closer to the goal — a gradual escalation known as "
            "<b>Crescendo</b>. The malicious instruction itself is never typed "
            "by the attacker; it lives in the planted content the assistant "
            "grounds on, so each turn stays innocuous on its own.",
            s_body,
        )
    )
    story.append(
        Paragraph(
            "<b>If a conversation fails.</b> When a line of attack stalls or is "
            "refused, the system does not give up — it branches. Using "
            "<b>TAP (Tree of Attacks with Pruning)</b>, the attacker proposes "
            "several different angles, each becomes a fresh conversation (a new "
            "node in the tree), the weakest are pruned, and the most promising "
            "are explored further. Tree depth reflects how many rounds of "
            "re-strategising were needed: depth&nbsp;0 is the first attempt, "
            "depth&nbsp;1 its alternative framings, and so on.",
            s_body,
        )
    )
    story.append(
        Paragraph(
            "<b>Judge (automated scoring).</b> Every turn's reply is scored by "
            "an automated judge that checks for grounding on the planted "
            "content and for injection effects (e.g. an injected marker, a "
            "language override, or a system-prompt/configuration disclosure). "
            "A refusal scores 0; a fired injection scores high. The score both "
            "guides the attacker's next move and decides when a conversation "
            "has <b>succeeded</b> (cleared the success threshold).",
            s_body,
        )
    )

    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=accent))

    # Conversations — one section per tree node / replay round. A node is
    # "successful" when any of its turns cleared the judge (verdict success).
    groups = build_transcript_groups(list(traces), full_responses or {})

    def _node_succeeded(grp: Any) -> bool:
        return any(getattr(t, "verdict", "") == "success" for t in grp.turns)

    success_count = sum(1 for g in groups if _node_succeeded(g))

    story.append(Paragraph("Conversation Transcripts", h_section))
    if not groups:
        story.append(Paragraph("No conversation turns were recorded for this run.", s_meta))
    else:
        summary_line = (
            f"{len(groups)} conversation(s), one per attack-tree node / replay round. "
            "Each turn shows the user prompt and Gemini's full response, verbatim."
        )
        if success_count:
            summary_line += (
                f" <b>{success_count} conversation(s) succeeded</b> — these are "
                "highlighted in green below."
            )
        else:
            summary_line += " No conversation succeeded in this run."
        story.append(Paragraph(summary_line, s_meta))

    for gi, grp in enumerate(groups, 1):
        succeeded = _node_succeeded(grp)
        title_text = f"Conversation {gi} — {html.escape(grp.label)}"
        if succeeded:
            title_text = "✓ " + title_text  # check mark prefix
        node_head = [
            Paragraph(title_text, h_node_success if succeeded else h_node),
        ]
        if succeeded:
            node_head.append(
                Paragraph(
                    "✓ <b>SUCCESSFUL</b> — the injection fired in this "
                    "conversation (a turn cleared the judge's success threshold).",
                    s_success_banner,
                )
            )
        # Keep the node header with at least its first turn's heading.
        story.append(KeepTogether(node_head))

        for turn in grp.turns:
            chip = _verdict_chip(turn, colors)
            score = (
                f"  ·  score {turn.score:.2f}" if getattr(turn, "score", None) is not None else ""
            )
            head = f"<b>Turn {turn.turn}</b>{chip}{html.escape(score)}"
            block = [
                Paragraph(head, s_turnhead),
                Paragraph("USER PROMPT", s_label),
                Paragraph(_safe(turn.prompt), s_user),
                Paragraph("GEMINI RESPONSE", s_label),
                Paragraph(_safe(turn.response), s_gemini),
            ]
            # Keep a turn's pieces together when it fits; long responses
            # still split across pages because the Paragraph itself can.
            story.append(KeepTogether(block) if len(turn.response) < 1500 else block[0])
            if len(turn.response) >= 1500:
                story.extend(block[1:])

    # ---- render -------------------------------------------------------
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=f"Fission transcript report — {getattr(run, 'id', '')}",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _verdict_chip(turn: Any, colors: Any) -> str:
    """An inline colored verdict chip for the turn heading."""
    verdict = getattr(turn, "verdict", None)
    if not verdict:
        return ""
    bg, fg = _VERDICT_COLORS.get(str(verdict), ("#f1f3f4", "#555555"))
    # reportlab inline markup: a colored, padded font run.
    return f' <font size="8" backColor="{bg}" color="{fg}"> {html.escape(str(verdict))} </font>'


def _footer(canvas: Any, doc: Any) -> None:
    """Page footer: tool attribution + page number."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColorRGB(0.6, 0.6, 0.6)
    canvas.drawString(
        18 * _MM,
        10 * _MM,
        "Generated by Fission — adversarial content shown verbatim for security review.",
    )
    canvas.drawRightString(192 * _MM, 10 * _MM, f"Page {doc.page}")
    canvas.restoreState()


_MM = 72.0 / 25.4  # points per millimetre (reportlab default unit is points)


__all__ = ["REPORTLAB_INSTALLED", "build_transcript_pdf"]
