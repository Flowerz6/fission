from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)


OUT = "Fission_Indirect_Prompt_Injection_Pilot.pdf"
NAVY = colors.HexColor("#17233C")
BLUE = colors.HexColor("#246BFD")
PALE = colors.HexColor("#EAF0FF")
LIGHT = colors.HexColor("#F3F5F8")
MID = colors.HexColor("#5F6B7A")

try:
    pdfmetrics.registerFont(TTFont("Aptos", "/usr/share/fonts/truetype/crosextra/Caladea-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("Aptos-Bold", "/usr/share/fonts/truetype/crosextra/Caladea-Bold.ttf"))
except Exception:
    pass

FONT = "Aptos" if "Aptos" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
BOLD = "Aptos-Bold" if "Aptos-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(BOLD, 9)
    canvas.setFillColor(BLUE)
    canvas.drawString(18 * mm, A4[1] - 12 * mm, "FISSION")
    canvas.setFont(BOLD, 6.5)
    canvas.setFillColor(MID)
    canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 12 * mm, "PILOT BRIEF  |  CONFIDENTIAL")
    canvas.setStrokeColor(colors.HexColor("#DDE2EA"))
    canvas.line(18 * mm, A4[1] - 15 * mm, A4[0] - 18 * mm, A4[1] - 15 * mm)
    canvas.setFont(FONT, 6.5)
    canvas.drawCentredString(A4[0] / 2, 8 * mm, "Fission  •  Adaptive adversarial testing for Workspace AI")
    canvas.restoreState()


doc = BaseDocTemplate(
    OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
    topMargin=20 * mm, bottomMargin=14 * mm,
    title="Fission — Indirect Prompt Injection Pilot Proposal",
    author="Fission",
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
doc.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=header_footer))

styles = getSampleStyleSheet()
eyebrow = ParagraphStyle("eyebrow", fontName=BOLD, fontSize=7.5, leading=9, textColor=BLUE, spaceAfter=3)
title = ParagraphStyle("title", fontName=BOLD, fontSize=23, leading=26, textColor=NAVY, spaceAfter=4)
subtitle = ParagraphStyle("subtitle", fontName=FONT, fontSize=10.5, leading=13, textColor=MID, spaceAfter=10)
h1 = ParagraphStyle("h1", fontName=BOLD, fontSize=13.5, leading=16, textColor=NAVY, spaceBefore=7, spaceAfter=4)
body = ParagraphStyle("body", fontName=FONT, fontSize=8.5, leading=11, textColor=NAVY, spaceAfter=4)
small = ParagraphStyle("small", parent=body, fontSize=7.8, leading=9.7)
cell = ParagraphStyle("cell", fontName=FONT, fontSize=7.6, leading=9.4, textColor=NAVY)
cell_b = ParagraphStyle("cellb", parent=cell, fontName=BOLD)
label = ParagraphStyle("label", fontName=BOLD, fontSize=6.8, leading=8, textColor=BLUE)
white = ParagraphStyle("white", fontName=FONT, fontSize=7.6, leading=9, textColor=colors.white)
white_b = ParagraphStyle("whiteb", parent=white, fontName=BOLD)
center_blue = ParagraphStyle("centerblue", fontName=BOLD, fontSize=8.2, leading=10, textColor=BLUE, alignment=TA_CENTER)

story = []
story += [
    Spacer(1, 3 * mm),
    Paragraph("INDIRECT PROMPT INJECTION", eyebrow),
    Paragraph("Small-scale pilot proposal", title),
    Paragraph("A focused, evidence-led assessment of cross-surface risk in Google Workspace", subtitle),
]

purpose = Table([[Paragraph("<font color='#246BFD'><b>THE PURPOSE</b></font><br/><br/><b>Establish whether automated, expert-grounded red teaming reveals material indirect prompt-injection risk in a representative Workspace workflow—and whether the resulting evidence is useful enough to support a wider investment decision.</b>", ParagraphStyle("purpose", parent=body, fontSize=9.1, leading=11.5, spaceAfter=0))]], colWidths=[doc.width])
purpose.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE), ("BOX", (0, 0), (-1, -1), 0, PALE), ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
story += [purpose, Paragraph("Recommended pilot", h1), Paragraph("A four-week, fixed-scope engagement covering one representative cross-surface workflow across up to three Workspace applications—for example Gmail → Docs → Sheets. Testing takes place in customer-approved test accounts, not production, and combines expert-authored attack chains with bounded automated variants and expert-validated findings.", body)]

summary_data = [
    [Paragraph("SCOPE", label), Paragraph("1 workflow • up to 3 Workspace surfaces • 2–3 test accounts", cell)],
    [Paragraph("TESTING", label), Paragraph("3–5 expert-authored seed Playbooks • up to 150 executed traces", cell)],
    [Paragraph("DURATION", label), Paragraph("4 weeks from access and scope confirmation", cell)],
    [Paragraph("OUTCOME", label), Paragraph("<b>A decision-ready view of observed exposure, evidence, and coverage gaps</b>", cell)],
]
summary = Table(summary_data, colWidths=[36 * mm, doc.width - 36 * mm])
summary.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("BACKGROUND", (0, 2), (-1, 2), LIGHT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
story += [summary, Paragraph("What we would test", h1), Paragraph("The exact workflow is agreed during kickoff. The pilot is intentionally narrow enough to execute quickly while preserving the multi-app, multi-account conditions in which indirect prompt injection becomes meaningful.", body)]
for text in [
    "Whether attacker-controlled content introduced through an approved carrier—such as an email, shared document, comment, or spreadsheet cell—is treated as an instruction by the assistant.",
    "Whether that instruction can influence behaviour on another Workspace surface or cause an unintended effect, such as instruction override, biased output, unauthorised data use, or external exfiltration.",
    "How robustly the observed behaviour persists across controlled, meaning-preserving variants of the original expert-authored chain.",
]:
    story.append(Paragraph("• &nbsp;" + text, ParagraphStyle("bullet", parent=small, leftIndent=7, firstLineIndent=-7, spaceAfter=3)))
story += [Paragraph("Pilot boundaries", h1), Paragraph("The pilot excludes production accounts, destructive payloads, broad penetration testing, remediation implementation, and claims of exhaustive coverage. All actions, domains, test data, and stop conditions are agreed in advance. Findings describe observed behaviour within the tested scope and environment.", small), PageBreak()]

story += [Paragraph("Delivery and decision framework", ParagraphStyle("page2", parent=h1, fontSize=17, leading=20, spaceBefore=3)), Paragraph("The engagement is designed to answer a practical feasibility question: does this testing approach produce reliable, actionable evidence that complements the organisation’s existing assurance methods?", ParagraphStyle("intro", parent=body, fontSize=9.2, leading=12, spaceAfter=6)), Paragraph("Four-week plan", h1)]
plan_data = [[Paragraph("WHEN", white_b), Paragraph("PHASE", white_b), Paragraph("ACTIVITY", white_b)]]
for row in [
    ("Week 1", "Define", "Confirm workflow, threat model, test accounts, access, safety controls, and success criteria."),
    ("Week 2", "Prepare", "Author and validate seed Playbooks; establish signals, invariants, and bounded mutation surfaces."),
    ("Week 3", "Execute", "Run gated variants, capture traces and evidence, and route uncertain outcomes for expert review."),
    ("Week 4", "Conclude", "Analyse results, produce deliverables, and hold an executive and technical readout."),
]:
    plan_data.append([Paragraph(f"<b>{row[0]}</b>", cell), Paragraph(f"<b>{row[1]}</b>", cell), Paragraph(row[2], cell)])
plan = Table(plan_data, colWidths=[20 * mm, 33 * mm, doc.width - 53 * mm], repeatRows=1)
plan_style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]
for i in (1, 3):
    plan_style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
plan.setStyle(TableStyle(plan_style))
story += [plan, Paragraph("Deliverables", h1)]

deliver_data = []
deliver_items = [
    ("01  Pilot plan & threat model", "Agreed scope, safety controls, workflow map, attack classes, and success criteria."),
    ("02  Evidence-backed findings", "Reproducible Playbook, trace, screenshots, signal verdicts, provenance, and expert rationale for each confirmed finding."),
    ("03  Pilot report", "Observed attack-success rate with uncertainty, inconclusive outcomes, tested coverage, and material gaps."),
    ("04  Readout & next-step recommendation", "Technical working session plus an executive summary of whether and how to extend testing."),
]
for i in range(0, 4, 2):
    deliver_data.append([Paragraph(f"<font color='#246BFD'><b>{deliver_items[i][0]}</b></font><br/>{deliver_items[i][1]}", cell), Paragraph(f"<font color='#246BFD'><b>{deliver_items[i+1][0]}</b></font><br/>{deliver_items[i+1][1]}", cell)])
deliver = Table(deliver_data, colWidths=[doc.width / 2, doc.width / 2])
deliver.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), PALE), ("BACKGROUND", (1, 1), (1, 1), PALE), ("BACKGROUND", (1, 0), (1, 0), LIGHT), ("BACKGROUND", (0, 1), (0, 1), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
story += [deliver, Paragraph("Inputs and success criteria", h1), Paragraph("<b>Customer input:</b> a named technical owner; approved test accounts and representative synthetic data; a 60-minute scoping session; access support during execution; and participation in the final readout.", small), Paragraph("The pilot is successful if the agreed workflow is exercised end to end; material outcomes are reproducible and supported by reviewable evidence; uncertainty and coverage gaps are explicit; and the results enable a clear decision on wider testing. Success does not depend on finding a vulnerability.", small)]

price = Table([[Paragraph("<font color='#AFC7FF'><b>INDICATIVE COMMERCIAL RANGE</b></font><br/><font color='white'>Fixed-fee pilot, subject to final scope and access requirements</font>", white), Paragraph("<font size='16'><b>£20k–£30k</b></font><br/><font size='6.5' color='#AFC7FF'>plus applicable taxes</font>", ParagraphStyle("price", parent=white, fontName=BOLD, alignment=TA_RIGHT))]], colWidths=[doc.width - 49 * mm, 49 * mm])
price.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10)]))
story += [Spacer(1, 4), price, Spacer(1, 6), Paragraph("Proposed next step: a 45-minute scoping session, followed by a final statement of work within three business days.", center_blue)]

doc.build(story)
print(OUT)
