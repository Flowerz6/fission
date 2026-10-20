"""Render the evidence transcripts: for each attack in the dataset, the planted
content, the user's request, and the assistant's reply that shows the attack.

    poetry run python scripts/build_pilot_transcripts_pdf.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Workspace_IPI_Attacks_Dataset.json"
OUT = ROOT / "Workspace_IPI_Attacks_Transcripts.pdf"
DBS = [
    ROOT / "advtest.db",
    ROOT / "backups/advtest-source-with-6.1-percent-20260727.db",
    ROOT / "backups/advtest-before-remove-demo-0c7d17f32d2c-20260728.db",
    ROOT / "backups/advtest-before-6.1-percent-import-20260727.db",
]

# reuse the effect detector + normaliser from the dataset builder
_spec = importlib.util.spec_from_file_location(
    "variant_ds", ROOT / "scripts" / "build_pilot_variant_dataset.py")
_vd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vd)
reply_shows_effect = _vd.reply_shows_effect
secondary_shows_effect = _vd.secondary_shows_effect
norm = _vd.norm

INK = colors.HexColor("#202124")
GREY = colors.HexColor("#5f6368")
RULE = colors.HexColor("#dadce0")
PANEL = colors.HexColor("#f1f3f4")
RESP = colors.HexColor("#eef4ea")
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
}
SECTION = {
    "A": "Data exfiltration to an attacker",
    "B": "Content planted for the victim",
    "C": "Instruction override and hijack",
    "D": "Integrity corruption",
    "E": "Confidential disclosure of model internals",
}


def load_success():
    rows = []
    for db in DBS:
        if not db.exists():
            continue
        c = sqlite3.connect(db)
        for beh, pay, rq, rr in c.execute(
            "SELECT behavior_id, payload_text, raw_request, raw_response "
            "FROM traces WHERE verdict='success'"
        ):
            rows.append((beh, pay or "", rq or "", rr or ""))
        c.close()
    return rows


def pick(rows, lineage, payload, trigger):
    """The success reply for this attack that shows the effect; the most
    complete one (longest among effect-bearing replies)."""
    pn, tn = norm(payload), norm(trigger)
    cand = []
    for beh, pay, rq, rr in rows:
        if beh != lineage:
            continue
        match = (norm(pay) == pn) if pay.strip() else (norm(rq) == tn)
        if match and reply_shows_effect(beh, rr):
            cand.append((rq, rr))
    if not cand:  # fall back to any effect-bearing reply for this behaviour
        cand = [(rq, rr) for beh, pay, rq, rr in rows
                if beh == lineage and reply_shows_effect(beh, rr)]
    if not cand:
        return None
    return max(cand, key=lambda x: len(x[1]))


def styles():
    base = getSampleStyleSheet()
    S = {}
    S["title"] = ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=22, leading=26, textColor=INK, alignment=TA_LEFT, spaceAfter=4)
    S["sub"] = ParagraphStyle("s", fontName="Helvetica", fontSize=11, leading=15, textColor=GREY)
    S["body"] = ParagraphStyle("b", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5,
                               leading=14, textColor=INK, spaceAfter=6)
    S["h1"] = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK,
                             spaceBefore=16, spaceAfter=6)
    S["cap"] = ParagraphStyle("cap", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=ACCENT,
                              spaceBefore=8, spaceAfter=4)
    S["label"] = ParagraphStyle("lb", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=GREY,
                                spaceBefore=4, spaceAfter=3)
    S["code"] = ParagraphStyle("cd", fontName="Courier", fontSize=7.3, leading=9.6, textColor=INK,
                               backColor=PANEL, borderPadding=6, spaceBefore=4, spaceAfter=5)
    S["resp"] = ParagraphStyle("rp", fontName="Courier", fontSize=7.3, leading=9.6, textColor=INK,
                               backColor=RESP, borderPadding=6, spaceBefore=4, spaceAfter=5)
    return S


def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def block(text, st, key, limit):
    t = (text or "").strip() or "(empty)"
    if len(t) > limit:
        t = t[:limit] + " [truncated]"
    return Paragraph(esc(t).replace("\n", "<br/>"), st[key])


def build():
    d = json.loads(DATA.read_text())
    recs = d["attacks"]
    rows = load_success()
    st = styles()
    story = []

    story.append(Paragraph("Indirect Prompt Injection Attack Transcripts", st["title"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=RULE))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Prepared for Google Workspace, under NDA.", st["sub"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "Each transcript shows the content planted in the workspace, the request the user makes, and the "
        "assistant's reply (shaded). The reply is where the planted instruction takes effect, while the "
        "user's request stays an ordinary one.", st["body"]))

    by_sec = {}
    for r in recs:
        by_sec.setdefault(r["class"][0], []).append(r)

    for sec in sorted(by_sec):
        story.append(Paragraph(f"{sec}.&nbsp;&nbsp;{esc(SECTION.get(sec, ''))}", st["h1"]))
        for r in sorted(by_sec[sec], key=lambda r: (r["class"], -r["success_rate"]["successes_M"])):
            cls = r["class"]
            got = pick(rows, r["lineage_id"], r["poisoned_payload"], r["user_trigger_prompt"])
            req, resp = (got if got else (r["user_trigger_prompt"], "(not captured)"))
            story.append(KeepTogether([
                Paragraph(f"{r['id']}&nbsp;&nbsp;&nbsp;{cls}&nbsp;&nbsp;{esc(CLASS_NAMES.get(cls, ''))}", st["cap"]),
                Paragraph(f"Planted content &nbsp;({esc(r['product'])})", st["label"]),
                block(r["poisoned_payload"], st, "code", 1400),
                Paragraph("User request", st["label"]),
                block(req, st, "code", 600),
                Paragraph("Assistant response (Gemini)", st["label"]),
                block(resp, st, "resp", 2800),
                Spacer(1, 8),
                HRFlowable(width="100%", thickness=0.4, color=RULE),
            ]))

    # ---- secondary (multi-turn) ----
    sec = d.get("secondary_attacks", [])
    if sec:
        story.append(Paragraph("Secondary attacks (multi-turn)", st["h1"]))
        story.append(Paragraph(
            "These attacks are carried by planted content, but the user reaches the effect over several "
            "turns rather than one request. The decisive turn and the assistant's reply are shown.",
            st["body"]))
        for r in sec:
            tn = norm(r["user_trigger_prompt"])
            cand = [rr for beh, pay, rq, rr in rows
                    if beh == r["lineage_id"] and norm(rq) == tn and secondary_shows_effect(beh, rr)]
            if not cand:
                cand = [rr for beh, pay, rq, rr in rows
                        if beh == r["lineage_id"] and secondary_shows_effect(beh, rr)]
            resp = max(cand, key=len) if cand else "(not captured)"
            story.append(KeepTogether([
                Paragraph(f"{r['id']}&nbsp;&nbsp;&nbsp;{esc(r['name'])}", st["cap"]),
                Paragraph(f"Planted content &nbsp;({esc(r['product'])})", st["label"]),
                block(r["poisoned_payload"], st, "code", 1400),
                Paragraph("User request (decisive turn in the conversation)", st["label"]),
                block(r["user_trigger_prompt"], st, "code", 600),
                Paragraph("Assistant response (Gemini)", st["label"]),
                block(resp, st, "resp", 2800),
                Spacer(1, 8),
                HRFlowable(width="100%", thickness=0.4, color=RULE),
            ]))

    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title="Indirect Prompt Injection Attack Transcripts", author="Red-teaming partner")

    def footer(canvas, dd):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GREY)
        canvas.drawRightString(190 * mm, 10 * mm, f"{dd.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {OUT}  ({OUT.stat().st_size // 1024} KB, "
          f"{len(recs)} core + {len(sec)} secondary transcripts)")


if __name__ == "__main__":
    build()
