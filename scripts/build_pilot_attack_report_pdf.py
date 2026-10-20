"""Render the full per-attack report: every distinct successful attack from
Workspace_IPI_Attacks_Dataset.json, in the proposal's data format.

    poetry run python scripts/build_pilot_attack_report_pdf.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Workspace_IPI_Attacks_Dataset.json"
OUT = ROOT / "Workspace_IPI_Attacks_Data.pdf"

INK = colors.HexColor("#202124")
GREY = colors.HexColor("#5f6368")
RULE = colors.HexColor("#dadce0")
PANEL = colors.HexColor("#f1f3f4")
ACCENT = colors.HexColor("#15417e")

CLASS_NAMES = {
    "A1": "Cross-surface trust delegation to doc exfil",
    "A2": "Image-beacon exfil (zero-click)",
    "A3": "Hyperlink exfil (click-through)",
    "A4": "Calendar tool-call exfil",
    "B1": "Phishing link injected into a composed reply",
    "C1": "Full goal hijack",
    "C2": "Task substitution (summarise to translate)",
    "C3": "Privilege-escalation spoof (fake system message)",
    "C4": "Victim-authorization hijack",
    "D1": "Summary fact corruption",
    "E1": "System-prompt leak",
    "E2": "Agent-architecture and skill-manifest leak",
}
SECTION = {
    "A": "Data exfiltration to an attacker",
    "B": "Content planted for the victim",
    "C": "Instruction override and hijack",
    "D": "Integrity corruption",
    "E": "Confidential disclosure of model internals",
}


def styles():
    base = getSampleStyleSheet()
    S = {}
    S["title"] = ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=22, leading=26, textColor=INK, alignment=TA_LEFT, spaceAfter=4)
    S["sub"] = ParagraphStyle("s", fontName="Helvetica", fontSize=11, leading=15, textColor=GREY)
    S["h1"] = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK,
                             spaceBefore=16, spaceAfter=6)
    S["body"] = ParagraphStyle("b", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5,
                               leading=14, textColor=INK, spaceAfter=6)
    S["small"] = ParagraphStyle("sm", fontName="Helvetica", fontSize=8.5, leading=12, textColor=GREY)
    S["cap"] = ParagraphStyle("cap", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=ACCENT,
                              spaceBefore=8, spaceAfter=4)
    S["label"] = ParagraphStyle("lb", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=GREY,
                                spaceBefore=4, spaceAfter=3)
    S["cell"] = ParagraphStyle("cl", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK)
    S["code"] = ParagraphStyle("cd", fontName="Courier", fontSize=7.3, leading=9.6, textColor=INK,
                               backColor=PANEL, borderPadding=6, spaceBefore=6, spaceAfter=6)
    S["jrn"] = ParagraphStyle("jr", fontName="Helvetica", fontSize=9, leading=13, textColor=INK,
                              leftIndent=16, firstLineIndent=-16, spaceAfter=3)
    return S


def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def code(text, st, limit=1500):
    t = (text or "").strip()
    if len(t) > limit:
        t = t[:limit] + " [truncated, full text in the JSON dataset]"
    return Paragraph(esc(t).replace("\n", "<br/>"), st["code"])


def fields(rows, st):
    data = [[Paragraph(k, st["label"]), Paragraph(v, st["cell"])] for k, v in rows]
    t = Table(data, colWidths=[30 * mm, 139 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def build():
    d = json.loads(DATA.read_text())
    st = styles()
    recs = d["attacks"]
    story = []

    story.append(Paragraph("Indirect Prompt Injection Attack Data", st["title"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=RULE))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Prepared for Google Workspace, under NDA. {len(recs)} distinct indirect-injection attacks.",
        st["small"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph(
        "Each entry is one distinct poisoned payload with the fields the proposal requests, followed by "
        "reproduction steps. The attack is carried by the planted payload; field 6 is the benign request "
        "the user makes.", st["body"]))

    story.append(Paragraph("User journeys (referenced in field 5)", st["h1"]))
    for jid, desc in d["user_journeys"].items():
        story.append(Paragraph(f"<b>{jid}</b>&nbsp;&nbsp;{esc(desc)}", st["jrn"]))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.6, color=RULE))

    by_sec = {}
    for r in recs:
        by_sec.setdefault(r["class"][0], []).append(r)

    for sec in sorted(by_sec):
        story.append(Paragraph(f"{sec}.&nbsp;&nbsp;{esc(SECTION.get(sec, ''))}", st["h1"]))
        rows = sorted(by_sec[sec], key=lambda r: (r["class"], -r["success_rate"]["successes_M"]))
        for r in rows:
            cls = r["class"]
            sr = r["success_rate"]
            md = r["metadata"]
            uj = r["user_journey"]
            tech = ", ".join(md.get("techniques", [])[:8]) or "none recorded"
            payload = r["poisoned_payload"]
            attack = r["attack_string"]

            blk = [
                Paragraph(f"{r['id']}&nbsp;&nbsp;&nbsp;{cls}&nbsp;&nbsp;{esc(CLASS_NAMES.get(cls, ''))}",
                          st["cap"]),
                Paragraph("1. Poisoned payload (full text)", st["label"]),
                code(payload, st),
            ]
            if _norm(attack) == _norm(payload):
                blk.append(Paragraph(
                    "2. Attack string: the planted payload above is itself the injected instruction; "
                    "there is no separate benign cover in this payload.", st["cell"]))
            else:
                blk.append(Paragraph("2. Attack string (verbatim)", st["label"]))
                blk.append(code(attack, st, limit=700))
            blk.append(fields([
                ("3. Harm type", esc(r["harm_type"])),
                ("4. Metadata", esc(md.get("strategy_obfuscation", "")) +
                 (f". Techniques: {esc(tech)}." if tech != "none recorded" else "")),
                ("5. Product, journey", f"{esc(r['product'])}. {uj['id']}: {esc(uj['description'])}"),
                ("6. Trigger prompt", esc(r["user_trigger_prompt"])),
                ("7. Success rate", f"{sr['tries_N']} attempts, {sr['successes_M']} successes"),
                ("8. Lineage id", esc(r["lineage_id"])),
                ("Reproduction", esc(" ".join(r["reproduction"]["steps"]))),
            ], st))
            blk.append(Spacer(1, 8))
            blk.append(HRFlowable(width="100%", thickness=0.4, color=RULE))
            story.append(KeepTogether(blk))

    # ---- secondary (multi-turn) ----
    sec = d.get("secondary_attacks", [])
    if sec:
        story.append(Paragraph("Secondary attacks (multi-turn)", st["h1"]))
        story.append(Paragraph(
            "Carried by planted content, but the user reaches the effect over several turns. Reported "
            "separately from the core set.", st["small"]))
        for r in sec:
            uj = r["user_journey"]
            sr = r["success_rate"]
            blk = [
                Paragraph(f"{r['id']}&nbsp;&nbsp;&nbsp;{esc(r['name'])}", st["cap"]),
                Paragraph("1. Poisoned payload (full text)", st["label"]),
                code(r["poisoned_payload"], st),
                fields([
                    ("2. Harm type", esc(r["harm_type"])),
                    ("3. Metadata", esc(r["metadata"]["strategy_obfuscation"])),
                    ("4. Product, journey", f"{esc(r['product'])}. {uj['id']}: {esc(uj['description'])}"),
                    ("5. Decisive user turn", esc(r["user_trigger_prompt"])),
                    ("6. Success rate", f"{sr['tries_N']} attempts, {sr['successes_M']} successes"),
                    ("7. Lineage id", esc(r["lineage_id"])),
                ], st),
                Spacer(1, 8),
                HRFlowable(width="100%", thickness=0.4, color=RULE),
            ]
            story.append(KeepTogether(blk))

    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title="IPI Attack Data", author="Red-teaming partner")

    def footer(canvas, dd):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GREY)
        canvas.drawRightString(190 * mm, 10 * mm, f"{dd.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {OUT}  ({OUT.stat().st_size // 1024} KB, {len(recs)} attacks)")


if __name__ == "__main__":
    build()
