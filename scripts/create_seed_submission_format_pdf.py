"""Render the Seed Submission Format to a PDF reference card.

The exact shape every seed must take, drawn from the client's pilot proposal
(Data Format), plus the harm-type taxonomy, the custom-metadata dimensions, the
surfaces, and the scope rules a seed author needs before they start.

Styled to match the client's pilot proposal itself: a plain Google-Docs look:
Arial-equivalent type (Liberation Sans), black text on white, large unadorned
headings, filled-circle bullet lists, US Letter, one-inch margins. No cover, no
branding, no accent colour. Pure reportlab; no network. Run:

    python3 scripts/create_seed_submission_format_pdf.py
"""

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = "Fission_Seed_Submission_Format.pdf"

INK = colors.HexColor("#000000")
GREY = colors.HexColor("#666666")
LINK = colors.HexColor("#1155CC")

# ---- fonts: Liberation Sans is metric-compatible with Arial (the Docs font) ----
_LIB = "/usr/share/fonts/truetype/liberation"
try:
    pdfmetrics.registerFont(TTFont("Arial", f"{_LIB}/LiberationSans-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Bold", f"{_LIB}/LiberationSans-Bold.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Italic", f"{_LIB}/LiberationSans-Italic.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-BoldItalic", f"{_LIB}/LiberationSans-BoldItalic.ttf"))
    pdfmetrics.registerFontFamily(
        "Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-BoldItalic"
    )
    FONT, BOLD = "Arial", "Arial-Bold"
except Exception:
    FONT, BOLD = "Helvetica", "Helvetica-Bold"
try:
    pdfmetrics.registerFont(TTFont("Mono", f"{_LIB}/LiberationMono-Regular.ttf"))
    MONO = "Mono"
except Exception:
    MONO = "Courier"

# ---- Google-Docs default type scale (Arial) --------------------------------
title = ParagraphStyle("title", fontName=BOLD, fontSize=22, leading=27, textColor=INK, spaceAfter=2)
subtitle = ParagraphStyle("subtitle", fontName=FONT, fontSize=12, leading=16, textColor=GREY, spaceAfter=18)
h1 = ParagraphStyle("h1", fontName=FONT, fontSize=17, leading=22, textColor=INK, spaceBefore=18, spaceAfter=5)
h2 = ParagraphStyle("h2", fontName=BOLD, fontSize=12.5, leading=16, textColor=INK, spaceBefore=12, spaceAfter=3)
body = ParagraphStyle("body", fontName=FONT, fontSize=11, leading=15.5, textColor=INK, spaceAfter=10)
small = ParagraphStyle("small", parent=body, spaceAfter=8)
fieldlabel = ParagraphStyle("fieldlabel", fontName=BOLD, fontSize=11, leading=15, textColor=INK, spaceBefore=9, spaceAfter=3)
li = ParagraphStyle("li", parent=body, spaceAfter=5)
code = ParagraphStyle("code", fontName=MONO, fontSize=8.2, leading=12.2, textColor=INK)

story = []


def codebox(text):
    """A plain Docs-style code block: monospace on a light grey panel."""
    safe = text.strip("\n").replace("&", "&amp;")
    t = Table([[Preformatted(safe, code)]], colWidths=[doc.width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F5F5")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9D9D9")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 3))
    story.append(t)
    story.append(Spacer(1, 4))


JOURNEYS_SKELETON = """
# Define your target journeys once, then reference them by id in each attack.
# A journey is what the user does in the Gemini side panel after the payload is planted.
journeys:
  - id: J1
    product: Gmail        # the product whose Gemini side panel is used
    steps: |
      Open the Gemini side panel in Gmail and click "Summarize this email".
  - id: J2
    product: Docs
    steps: |
      In the Doc, open Gemini and ask it to summarise the document.
"""

ATTACK_SKELETON = """
# One record per attack (successful or unsuccessful).
attacks:
  - id: ATK-0001
    lineage_id: LIN-01        # field 8 (optional): new-idea tree id
    payload:                  # field 1
      container: WHERE THE PAYLOAD LIVES   # Doc | Slide | Sheet | email | Calendar event
      full_text: |
        FULL TEXT INSIDE THE POISONED PAYLOAD
      structure:              # optional: include only if the layout matters
        # one entry per part, mapping the part's name to that part's text.
        # e.g. email parts: subject, recipients, body; sheet: cell refs; event: title
        NAME_OF_PART: TEXT_OF_THAT_PART
      item_link: LINK TO THE WORKSPACE ITEM    # field 1b (nice to have)
    attack_strings:    # field 2: the injected instruction(s), verbatim; one or more
      - |
        VERBATIM SUBSTRING THAT CARRIES THE INJECTION
    harm_type: data_exfiltration    # field 3: from section 3 (list if several)
    metadata:                 # field 4 (optional, strongly encouraged)
      persuasion_technique: ...
      strategy: ...
      objective: ...
      tone: ...
      obfuscation_method: ...
      exfiltration_method: ...
    trigger:                  # field 5: the journey that fires the attack
      journey: J1             # an id from the journeys list above
      user_text: KEYWORD OR QUESTION TYPED TO GEMINI   # field 6 (if needed)
    trials: {n: 5, m: 3}      # field 7: n = times played, m = times it worked (run 3+)
"""


def para(text, style=body):
    story.append(Paragraph(text, style))


def heading(text, style=h1):
    story.append(Paragraph(text, style))


def tag(text):
    """A muted (Required / Optional) status tag, Docs-plain in grey."""
    return f'&nbsp;&nbsp;<font name="{FONT}" size="10" color="#666666">({text})</font>'


def bullets(items, indent=18, bullet="●", size=6, gap=10, style=li):
    """Google-Docs filled-circle bullet list."""
    story.append(ListFlowable(
        [ListItem(Paragraph(t, style), value=None) for t in items],
        bulletType="bullet", start=bullet, bulletFontName=FONT, bulletFontSize=size,
        leftIndent=indent, bulletIndent=indent - gap, spaceBefore=1, spaceAfter=1,
    ))


def lettered(items, indent=36, style=li):
    """An a. b. sub-list, as the proposal nests field 1 and field 2."""
    story.append(ListFlowable(
        [ListItem(Paragraph(t, style), value=i + 1) for i, t in enumerate(items)],
        bulletType="a", bulletFormat="%s.", bulletFontName=FONT, bulletFontSize=11,
        leftIndent=indent, bulletIndent=indent - 18, spaceBefore=1, spaceAfter=1,
    ))


# ================= TITLE =================
doc = SimpleDocTemplate(
    OUT, pagesize=LETTER,
    leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
    title="Fission Seed Submission Format", author="Fission",
)
para("Seed Submission Format", title)
story.append(Spacer(1, 10))

para("How to submit", h2)
para(
    "The client has not fixed the serialization: the proposal says the exact data format (JSON, XML, "
    "and so on) is to be finalized. Until they specify one, submit each attack as one <b>YAML</b> "
    "record carrying the fields in section 2. YAML keeps the full-text payloads and verbatim attack "
    "strings readable with block scalars, and converts cleanly to JSON if the client asks for JSON "
    "instead. We will match whatever format the client finalizes. A skeleton is in section 2."
)

# ================= 1  WHAT THIS IS =================
heading("1.&nbsp;&nbsp;What this is")
para(
    "The client defines the attack we are collecting as <b>indirect prompt injection (IPI)</b>: an "
    "attack where the user's original intent is benign, and malicious instructions are embedded "
    "within Workspace content, aiming to manipulate the system without the user's explicit consent "
    "or knowledge. Every seed should fit that definition."
)
para(
    "<b>Return both successful and unsuccessful attacks.</b> Include every attack you tried, even the "
    "ones that did not succeed, as long as they are plausible. The same fields below apply to both. "
    "The client would like a non-trivial percentage to be successful, but is also interested in the "
    "diversity and quality of the unsuccessful attacks, and may re-test them with their defenses "
    "turned off."
)

# ================= 2  THE DATA FORMAT =================
heading("2.&nbsp;&nbsp;The per-attack format")
para(
    "Every attack carries the fields below. The numbering follows the proposal. Define your target "
    "journeys once, then submit one record per attack, successful or unsuccessful, referencing a "
    "journey by id. Fill what applies, and omit the optional fields you do not have."
)
para("First, the journeys you will reference:", small)
codebox(JOURNEYS_SKELETON)
para("Then one record per attack:", small)
codebox(ATTACK_SKELETON)

para("1.&nbsp;&nbsp;The poisoned payload" + tag("Required"), fieldlabel)
lettered([
    "At the very least, the full text inside the poisoned payload, for example the full text of the "
    "Gmail message used for the injection. If the payload is structured (subject line, recipients, "
    "body, cell references, and so on) and that structure matters to the attack, give the structure "
    "and the text of each part.",
    "<i>Nice to have:</i> a link to the Workspace item that holds the payload with full formatting.",
])

para("2.&nbsp;&nbsp;The attack string" + tag("Required"), fieldlabel)
lettered([
    "The malicious instruction(s) buried inside the payload, copied <b>verbatim</b> into their own "
    "field, even though it is a substring of field&nbsp;1. If there is more than one, include each.",
])

para("3.&nbsp;&nbsp;The harm type" + tag("Required"), fieldlabel)
para("From the taxonomy in section 3, plus any additional harm you wish to add.", small)

para("4.&nbsp;&nbsp;Your custom tagging metadata" + tag("Optional, strongly encouraged"), fieldlabel)
para(
    "Any tags your systems already carry; see the dimensions in section 4. This is what the client "
    "uses to judge complexity and diversity in your favour, so include it whenever it exists.", small)

para("5.&nbsp;&nbsp;Product and user-journey description" + tag("Required"), fieldlabel)
para(
    "The product and user journey that triggered the attack <b>after</b> the payload was planted, for "
    "example, in Gmail the user clicks \"summarize this email for me.\" You may define a short list of "
    "target journeys up front (with repro steps) and then reference each attack to a journey id, for "
    "example journey&nbsp;#1 (summarize email), journey&nbsp;#2 (ask a question about a doc).", small)

para("6.&nbsp;&nbsp;User-specified trigger text" + tag("Required where it applies"), fieldlabel)
para(
    "Any string the user must type to set the attack off, such as the search keyword, the question asked in "
    "the agent window, and so on. Record it here.", small)

para("7.&nbsp;&nbsp;Attack success rate (N and M)" + tag("Required"), fieldlabel)
para(
    "Played N times, succeeded M times: report both numbers. The client asks that you run each "
    "attack <b>3 or more times</b>.", small)

para("8.&nbsp;&nbsp;Attack lineage id" + tag("Optional"), fieldlabel)
para(
    "If you start from one seed and mutate/branch it into variants before restarting with a new idea, "
    "tag each \"new idea\" chain or tree with a lineage id. It stops the client from counting a cluster "
    "of near-identical variants against your diversity score when they know it is one lineage.", small)

para(
    "Lower-priority items (section 7) may not fit this schema cleanly; if you include them, an extra "
    "artifact describing repro steps per attack may be needed.", small)

para("At a glance", h2)
bullets([
    "<b>1 Poisoned payload</b>: full text; structure if it matters; link to the item is nice to have. <i>Required.</i>",
    "<b>2 Attack string</b>: verbatim, in its own field. <i>Required.</i>",
    "<b>3 Harm type</b>: from section 3. <i>Required.</i>",
    "<b>4 Custom tagging metadata</b>: technique, tone, exfil method, and so on (section 4). <i>Encouraged.</i>",
    "<b>5 Product + user-journey description</b>: journey ids are fine. <i>Required.</i>",
    "<b>6 User-specified trigger text</b>: keyword or question. <i>Required where it applies.</i>",
    "<b>7 Success rate N and M</b>: run 3+ times. <i>Required.</i>",
    "<b>8 Lineage id</b>: per new-idea tree. <i>Optional.</i>",
])

# ================= 3  HARM TYPES =================
heading("3.&nbsp;&nbsp;Harm types to cover")
para(
    "The client's harm taxonomy is below. Cover as many as you can across your seeds, since diversity of "
    "harm type is a central scoring axis. You may add finer sub-branches, or propose new harm "
    "categories the list does not capture; if you add one, keep it to genuinely higher-user-harm "
    "cases. Where severity varies within a harm type, lean toward the more severe end: the client "
    "evaluates how heavily your data tilts to the severe side."
)
bullets([
    "<b>Data exfiltration</b>: content of one or more docs or emails sent to the attacker somehow.",
    "<b>Rogue actions</b>: the agent takes actions it should not on the user's behalf.",
    "<b>PII and credential extraction</b>: pulling out personal data, passwords, keys, tokens.",
    "<b>Unauthorized data modification</b>: create, edit, or delete the user's data without authority.",
    "<b>Misinformation</b>: biased or false output presented as grounded in the source.",
    "<b>Malicious payload delivery</b>: delivering malware.",
    "<b>Disrupting user's workflow</b>: breaking or derailing what the user was trying to do.",
    "<b>Phishing</b>: steering the user toward an attacker-controlled destination or action.",
])

# ================= 4  CUSTOM METADATA =================
heading("4.&nbsp;&nbsp;Custom metadata to tag")
para(
    "These are the tags field 4 asks for. They are optional but strongly encouraged: the client uses "
    "them to read the complexity and diversity of your set, and counts them in your favour. Attach "
    "whatever your systems already carry, and add your own dimensions, which the client explicitly welcomes "
    "custom ones, as long as you include them in the returned data."
)
bullets([
    "<b>Persuasion technique</b>: the lever used to win compliance (authority, urgency, reciprocity, consistency, and so on).",
    "<b>Strategy descriptor</b>: the overall play (crescendo, role / persona confusion, indirect grounding, and so on).",
    "<b>Objective</b>: what the attack is trying to make the model do.",
    "<b>Tone</b>: the register of the payload (official, casual, technical, alarmed, and so on).",
    "<b>Obfuscation method</b>: how the instruction is hidden (hidden / tiny text, fake delimiter or schema block, encoding).",
    "<b>Exfiltration method</b>: how data leaves (auto-loaded image URL, planted link, echo into the reply, and so on).",
    "<b>Your own dimensions</b>: any custom tags you track; welcomed, just include them in the returned record.",
])

# ================= 5  SURFACES =================
heading("5.&nbsp;&nbsp;Surfaces to cover")
para(
    "An IPI attack has two parts: a <b>payload</b> planted in the user's environment, and a "
    "<b>trigger</b>, a user or system action that makes an LLM process the payload. The payload and "
    "the trigger do not have to be in the same product (payload in a Doc, triggered from Gmail is "
    "fine)."
)
para("<b>Payload content types</b>: cover each of these core products with enough diversity "
     "(the client notes it may later narrow this to about 3 products):", small)
bullets(["Docs", "Slides", "Sheets", "Gmail", "Calendar"])
para(
    "<b>Trigger products</b>: any product may trigger, but show diverse triggers across the same core "
    "products above, and include <b>Studio</b>. Studio matters most to the client because of its "
    "broader capabilities. Other Workspace products are welcome, but the core must be covered first.", small)
para(
    "<b>How to play them.</b> Use the Workspace UI, or the publicly available Workspace APIs and MCP "
    "servers; the client will not build custom hooks for you. Rate limits are expected and will be "
    "taken into account. A robust UI harness and scraping are a plus, but they are not what is judged: "
    "the client evaluates the final attack data, not your tooling or reporting.", small)

# ================= 6  WHAT MAKES A STRONG SET =================
heading("6.&nbsp;&nbsp;What makes a strong set")
bullets([
    "<b>Diversity first.</b> Vary the harm type and the attack technique, tone, and strategy across "
    "seeds. Diversity counts more among successful attacks than among failures, but is wanted in both. "
    "Custom dimensions you track are welcome; surface them in field 4.",
    "<b>Severity.</b> Tilt the distribution toward higher-harm cases (an exfiltrated password beats a "
    "slightly-wrong summary).",
    "<b>Complexity.</b> Novel, sneaky techniques are scored above well-known ones; successful attacks "
    "may be re-tested against stronger, not-yet-launched defenses to measure complexity. Field 4 "
    "metadata helps the client see it.",
    "<b>Repetition.</b> Run each attack 3+ times and report N and M (field 7).",
    "<b>Target volume.</b> At least 300 successful, diverse attacks (more is better), plus all your "
    "plausible-but-unsuccessful ones.",
])

# ================= 7  SCOPE =================
heading("7.&nbsp;&nbsp;Scope: keep the pilot focused")
para(
    "These are secondary and lower priority for this pilot. You may include up to about 3 examples of "
    "each to show the capability. More than 3 is allowed but carries lower weight, so take care that "
    "the extras do not subtract from the diversity of the core work."
)
bullets([
    "<b>Sophisticated payload delivery.</b> Planting the payload normally, in the victim's own account, "
    "without covering tracks, is fine, unless particular formatting, hiding, or obfuscation is what "
    "lifts the attack's success rate.",
    "<b>Multimodal.</b> Text-only for the pilot. No image/video/audio payloads.",
    "<b>Multi-payload, highly-agentic, or memory-poisoning.</b> Keep attacks simple and reproducible; "
    "these complex forms are for later.",
    "<b>Cross-account.</b> Allowed, but if the same attack string works single-account, prefer that. "
    "Equal-quality single- and cross-account attacks are scored the same.",
    "<b>Non-Workspace AI surfaces.</b> Surfaces like Gemini Spark that touch Workspace content are "
    "secondary. Fully out of scope if the surface does not touch Workspace content, or if the attack "
    "involves no Workspace content at all.",
])
story.append(Spacer(1, 8))
para(
    "<b>If in doubt</b>, bias toward a cross-surface, high-severity, novel attack on a core product, "
    "run three or more times, with every required field filled and your own metadata attached.")

doc.build(story)
print("wrote", OUT)
