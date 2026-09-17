from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


OUT = "Fission_Indirect_Prompt_Injection_Pilot.docx"
NAVY = "17233C"
BLUE = "246BFD"
PALE = "EAF0FF"
LIGHT = "F3F5F8"
MID = "5F6B7A"
WHITE = "FFFFFF"


def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def margins(cell, top=100, start=120, bottom=100, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def no_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = borders.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            borders.append(tag)
        tag.set(qn("w:val"), "nil")


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    tr_pr.append(repeat)


def add_text(p, text, size=9.2, color=NAVY, bold=False):
    r = p.add_run(text)
    r.font.name = "Aptos"
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = RGBColor.from_string(color)
    return r


def heading(doc, text, size=15):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(7)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    add_text(p, text, size=size, color=NAVY, bold=True)
    return p


def body(doc, text, after=4, size=9.2):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.06
    add_text(p, text, size=size)
    return p


def bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.left_indent = Inches(0.18)
    p.paragraph_format.first_line_indent = Inches(-0.13)
    p.paragraph_format.space_after = Pt(2.5)
    p.paragraph_format.line_spacing = 1.02
    add_text(p, text, size=8.8)


def add_header(section):
    header = section.header
    header.is_linked_to_previous = False
    t = header.add_table(rows=1, cols=2, width=Inches(6.85))
    no_borders(t)
    t.columns[0].width = Inches(4.9)
    t.columns[1].width = Inches(1.95)
    p = t.cell(0, 0).paragraphs[0]
    add_text(p, "FISSION", size=10, color=BLUE, bold=True)
    p2 = t.cell(0, 1).paragraphs[0]
    p2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    add_text(p2, "PILOT BRIEF  |  CONFIDENTIAL", size=7.5, color=MID, bold=True)


def add_footer(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_text(p, "Fission  •  Adaptive adversarial testing for Workspace AI", size=7.5, color=MID)


doc = Document()
sec = doc.sections[0]
sec.page_height = Inches(11.7)
sec.page_width = Inches(8.3)
sec.top_margin = Inches(0.62)
sec.bottom_margin = Inches(0.58)
sec.left_margin = Inches(0.72)
sec.right_margin = Inches(0.72)
sec.header_distance = Inches(0.25)
sec.footer_distance = Inches(0.25)
add_header(sec)
add_footer(sec)

styles = doc.styles
styles["Normal"].font.name = "Aptos"
styles["Normal"].font.size = Pt(9.2)
styles["Normal"].font.color.rgb = RGBColor.from_string(NAVY)

# PAGE 1
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(10)
p.paragraph_format.space_after = Pt(2)
add_text(p, "INDIRECT PROMPT INJECTION", size=8.5, color=BLUE, bold=True)
p = doc.add_paragraph()
p.paragraph_format.space_after = Pt(5)
add_text(p, "Small-scale pilot proposal", size=25, color=NAVY, bold=True)
p = doc.add_paragraph()
p.paragraph_format.space_after = Pt(13)
add_text(p, "A focused, evidence-led assessment of cross-surface risk in Google Workspace", size=11.5, color=MID)

callout = doc.add_table(rows=1, cols=1)
callout.alignment = WD_TABLE_ALIGNMENT.CENTER
callout.autofit = False
callout.columns[0].width = Inches(6.75)
cell = callout.cell(0, 0)
shade(cell, PALE)
margins(cell, 150, 180, 150, 180)
p = cell.paragraphs[0]
p.paragraph_format.space_after = Pt(3)
add_text(p, "THE PURPOSE", size=8, color=BLUE, bold=True)
p = cell.add_paragraph()
p.paragraph_format.space_after = Pt(0)
add_text(p, "Establish whether automated, expert-grounded red teaming reveals material indirect prompt-injection risk in a representative Workspace workflow—and whether the resulting evidence is useful enough to support a wider investment decision.", size=10.1, bold=True)

heading(doc, "Recommended pilot", 15)
body(doc, "A four-week, fixed-scope engagement covering one representative cross-surface workflow across up to three Workspace applications—for example Gmail → Docs → Sheets. Testing takes place in customer-approved test accounts, not production, and combines expert-authored attack chains with bounded automated variants and expert-validated findings.", after=6)

t = doc.add_table(rows=4, cols=2)
t.alignment = WD_TABLE_ALIGNMENT.CENTER
t.autofit = False
t.columns[0].width = Inches(1.48)
t.columns[1].width = Inches(5.27)
no_borders(t)
rows = [
    ("SCOPE", "1 workflow • up to 3 Workspace surfaces • 2–3 test accounts"),
    ("TESTING", "3–5 expert-authored seed Playbooks • up to 150 executed traces"),
    ("DURATION", "4 weeks from access and scope confirmation"),
    ("OUTCOME", "A decision-ready view of observed exposure, evidence, and coverage gaps"),
]
for i, (label, value) in enumerate(rows):
    for c in t.rows[i].cells:
        shade(c, LIGHT if i % 2 == 0 else WHITE)
        margins(c)
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = t.cell(i, 0).paragraphs[0]
    add_text(p, label, size=7.8, color=BLUE, bold=True)
    p = t.cell(i, 1).paragraphs[0]
    add_text(p, value, size=8.8, bold=(i == 3))

heading(doc, "What we would test", 15)
body(doc, "The exact workflow is agreed during kickoff. The pilot is intentionally narrow enough to execute quickly while preserving the multi-app, multi-account conditions in which indirect prompt injection becomes meaningful.", after=3)
bullet(doc, "Whether attacker-controlled content introduced through an approved carrier—such as an email, shared document, comment, or spreadsheet cell—is treated as an instruction by the assistant.")
bullet(doc, "Whether that instruction can influence behaviour on another Workspace surface or cause an unintended effect, such as instruction override, biased output, unauthorised data use, or external exfiltration.")
bullet(doc, "How robustly the observed behaviour persists across controlled, meaning-preserving variants of the original expert-authored chain.")

heading(doc, "Pilot boundaries", 15)
body(doc, "The pilot excludes production accounts, destructive payloads, broad penetration testing, remediation implementation, and claims of exhaustive coverage. All actions, domains, test data, and stop conditions are agreed in advance. Findings describe observed behaviour within the tested scope and environment.", after=0, size=8.8)

doc.add_page_break()

# PAGE 2
heading(doc, "Delivery and decision framework", 18)
body(doc, "The engagement is designed to answer a practical feasibility question: does this testing approach produce reliable, actionable evidence that complements the organisation’s existing assurance methods?", after=7, size=10)

heading(doc, "Four-week plan", 15)
t = doc.add_table(rows=5, cols=3)
t.alignment = WD_TABLE_ALIGNMENT.CENTER
t.autofit = False
t.columns[0].width = Inches(0.8)
t.columns[1].width = Inches(1.4)
t.columns[2].width = Inches(4.55)
hdr = t.rows[0]
set_repeat_table_header(hdr)
for c in hdr.cells:
    shade(c, NAVY)
    margins(c, 90, 110, 90, 110)
for idx, txt in enumerate(("WHEN", "PHASE", "ACTIVITY")):
    p = hdr.cells[idx].paragraphs[0]
    add_text(p, txt, size=7.6, color=WHITE, bold=True)
plan = [
    ("Week 1", "Define", "Confirm workflow, threat model, test accounts, access, safety controls, and success criteria."),
    ("Week 2", "Prepare", "Author and validate seed Playbooks; establish signals, invariants, and bounded mutation surfaces."),
    ("Week 3", "Execute", "Run gated variants, capture traces and evidence, and route uncertain outcomes for expert review."),
    ("Week 4", "Conclude", "Analyse results, produce deliverables, and hold an executive and technical readout."),
]
for i, row in enumerate(plan, start=1):
    for c in t.rows[i].cells:
        shade(c, LIGHT if i % 2 else WHITE)
        margins(c, 95, 110, 95, 110)
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for j, txt in enumerate(row):
        p = t.cell(i, j).paragraphs[0]
        add_text(p, txt, size=8.4, bold=(j < 2))

heading(doc, "Deliverables", 15)
deliver = doc.add_table(rows=2, cols=2)
deliver.alignment = WD_TABLE_ALIGNMENT.CENTER
deliver.autofit = False
deliver.columns[0].width = Inches(3.36)
deliver.columns[1].width = Inches(3.39)
no_borders(deliver)
items = [
    ("01  Pilot plan & threat model", "Agreed scope, safety controls, workflow map, attack classes, and success criteria."),
    ("02  Evidence-backed findings", "Reproducible Playbook, trace, screenshots, signal verdicts, provenance, and expert rationale for each confirmed finding."),
    ("03  Pilot report", "Observed attack-success rate with uncertainty, inconclusive outcomes, tested coverage, and material gaps."),
    ("04  Readout & next-step recommendation", "Technical working session plus an executive summary of whether and how to extend testing."),
]
for idx, (title, desc) in enumerate(items):
    cell = deliver.cell(idx // 2, idx % 2)
    shade(cell, PALE if idx in (0, 3) else LIGHT)
    margins(cell, 130, 140, 130, 140)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(3)
    add_text(p, title, size=8.6, color=BLUE, bold=True)
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    add_text(p, desc, size=8.2)

heading(doc, "Inputs and success criteria", 15)
body(doc, "Customer input: a named technical owner; approved test accounts and representative synthetic data; a 60-minute scoping session; access support during execution; and participation in the final readout.", after=3, size=8.8)
body(doc, "The pilot is successful if the agreed workflow is exercised end to end; material outcomes are reproducible and supported by reviewable evidence; uncertainty and coverage gaps are explicit; and the results enable a clear decision on wider testing. Success does not depend on finding a vulnerability.", after=6, size=8.8)

price = doc.add_table(rows=1, cols=2)
price.alignment = WD_TABLE_ALIGNMENT.CENTER
price.autofit = False
price.columns[0].width = Inches(4.7)
price.columns[1].width = Inches(2.05)
no_borders(price)
for c in price.rows[0].cells:
    shade(c, NAVY)
    margins(c, 145, 170, 145, 170)
p = price.cell(0, 0).paragraphs[0]
p.paragraph_format.space_after = Pt(3)
add_text(p, "INDICATIVE COMMERCIAL RANGE", size=7.7, color="AFC7FF", bold=True)
p = price.cell(0, 0).add_paragraph()
p.paragraph_format.space_after = Pt(0)
add_text(p, "Fixed-fee pilot, subject to final scope and access requirements", size=8.5, color=WHITE)
p = price.cell(0, 1).paragraphs[0]
p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
add_text(p, "£20k–£30k", size=17, color=WHITE, bold=True)
p = price.cell(0, 1).add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
p.paragraph_format.space_after = Pt(0)
add_text(p, "plus applicable taxes", size=7.5, color="AFC7FF")

p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(7)
p.paragraph_format.space_after = Pt(0)
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
add_text(p, "Proposed next step: a 45-minute scoping session, followed by a final statement of work within three business days.", size=9, color=BLUE, bold=True)

props = doc.core_properties
props.title = "Fission — Indirect Prompt Injection Pilot Proposal"
props.subject = "Small-scale Google Workspace red-teaming pilot"
props.author = "Fission"
props.keywords = "Fission, indirect prompt injection, red teaming, Google Workspace, pilot"

doc.save(OUT)
print(OUT)
