"""Render the Part 2 PRD to a one-page PDF — the format the brief asks for.

    python tools/make_prd_pdf.py   ->   PRD_Part2.pdf

Content is kept in lockstep with PRD_Part2.md by hand; the markdown stays the
editable source, this is the submission artifact. The script asserts the output
is exactly one page, because "max 1 page" is a scored scope-discipline constraint
rather than a formatting preference.

Text avoids glyphs outside WinAnsiEncoding (no arrows, no emoji, no math signs) —
the standard PDF fonts cannot render them and they come out as black boxes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "PRD_Part2.pdf"

ACCENT = colors.HexColor("#1F4E79")
MUTED = colors.HexColor("#5A5A5A")
RULE = colors.HexColor("#C9D6E4")
BOXBG = colors.HexColor("#F4F8FC")

title = ParagraphStyle(
    "title", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=ACCENT,
    spaceAfter=2,
)
subtitle = ParagraphStyle(
    "subtitle", fontName="Helvetica-Oblique", fontSize=8, leading=10, textColor=MUTED,
    spaceAfter=6,
)
h = ParagraphStyle(
    "h", fontName="Helvetica-Bold", fontSize=9.5, leading=11.5, textColor=ACCENT,
    spaceBefore=7, spaceAfter=2.5,
)
body = ParagraphStyle(
    "body", fontName="Helvetica", fontSize=8.6, leading=11, alignment=TA_JUSTIFY,
    spaceAfter=3,
)
tight = ParagraphStyle("tight", parent=body, spaceAfter=1.5)
small = ParagraphStyle(
    "small", fontName="Helvetica-Oblique", fontSize=7.4, leading=9.2, textColor=MUTED,
)
flow = ParagraphStyle(
    "flow", fontName="Helvetica", fontSize=8.2, leading=11.5, alignment=TA_JUSTIFY,
)


def persona_cell(name: str, text: str) -> list:
    return [Paragraph(f"<b>{name}</b>", tight), Paragraph(text, tight)]


story: list = []

story.append(Paragraph("PRD: SU to CG Trade Document Verification Agent", title))
story.append(
    Paragraph(
        "Part 2 &#183; Builds on the Part 1 POC: same vision extraction, same store, "
        "same analytics layer. v1.0 &#183; 2026-08-02",
        subtitle,
    )
)
story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=4))

story.append(Paragraph("Problem", h))
story.append(
    Paragraph(
        "Every shipment's documents are validated by a CG team member who opens each "
        "attachment, reads every field, and checks it against what the customer requires "
        "&#8212; then types out what is wrong. Rules live in people's heads, so a new hire "
        "errs for weeks, nobody can see how many documents are pending, and there is no "
        "audit trail when a dispute surfaces. The result is 2 to 4 amendment cycles per "
        "shipment at 4 to 24 hours each, with CG bandwidth capping how fast shipments clear.",
        body,
    )
)

story.append(Paragraph("Personas", h))
personas = Table(
    [[
        persona_cell(
            "Meera &#8212; CG validator (3 yrs)",
            "Checks ~40 documents a day across a dozen customers' unwritten rule sets. "
            "Judged on one thing: errors that reach the customer (a wrong HS code means a "
            "customs hold and a penalty). Cares about catching every discrepancy without "
            "re-reading every field, and never being the reason a shipment sat for a day.",
        ),
        persona_cell(
            "Rahul &#8212; SU documentation executive",
            "His job feels done when the doc-set email is sent. Judged on dispatch speed; "
            "every amendment email is unplanned rework. Cares about knowing exactly what to "
            "fix, in one pass &#8212; not \"please recheck the invoice\" ping-pong.",
        ),
    ]],
    colWidths=[3.53 * inch, 3.53 * inch],
)
personas.setStyle(
    TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ])
)
story.append(personas)

story.append(Paragraph("Jobs to be done", h))
story.append(
    Paragraph(
        "<b>1. (Meera)</b> When an SU document lands in my inbox, I want a field-by-field "
        "verdict against the customer's written requirements before I open the attachment, "
        "so that I spend my time only on the fields that are actually wrong.",
        tight,
    )
)
story.append(
    Paragraph(
        "<b>2. (Rahul)</b> When my documents have an issue, I want an amendment request "
        "that lists each field with what I sent and what was expected, so that I can fix "
        "everything in one cycle instead of three.",
        tight,
    )
)

story.append(Paragraph("The flow (bold = human touchpoint)", h))
flow_box = Table(
    [[Paragraph(
        "<b>SU emails the document</b> -&gt; agent detects it (trigger) -&gt; agent extracts "
        "fields (Part 1 vision agent) -&gt; agent compares against the customer rule set "
        "(deterministic) -&gt; agent flags each field: match / mismatch / uncertain / missing "
        "-&gt; agent drafts the reply -&gt; <b>CG opens the verification result</b> -&gt; "
        "<b>CG inspects flagged fields</b> (found vs expected, with the quoted evidence) -&gt; "
        "<b>CG edits the draft if needed</b> -&gt; <b>CG SENDS</b> -&gt; <b>SU fixes and "
        "resends</b> (loop), or on a clean pass the docs go to the customer.",
        flow,
    )]],
    colWidths=[7.06 * inch],
)
flow_box.setStyle(
    TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BOXBG),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])
)
story.append(flow_box)
story.append(Spacer(1, 2))
story.append(
    Paragraph(
        "The three-party structure is untouched &#8212; SU sends, CG validates, the customer "
        "receives one clean set. The agent removes the reading and the typing, not the "
        "humans. It has no send capability at all.",
        body,
    )
)

story.append(Paragraph("North-star metric", h))
story.append(
    Paragraph(
        "<b>Median turnaround: SU email arrival to CG reply sent (minutes).</b> Computed "
        "from the system's own audit trail (v_verifications.turnaround_minutes), so a CG "
        "team lead can check it on Day 14 with one query against the manual baseline of "
        "hours. Guardrail so speed never buys errors: percentage of agent-approved "
        "documents later amended (target: 0).",
        body,
    )
)

story.append(Paragraph("Failure mode, and how it is stopped", h))
story.append(
    Paragraph(
        "<b>Worst case: a false approval</b> &#8212; the agent shows a wrong field as "
        "matched, Meera trusts the green tick, and a bad document reaches customs. Stopped "
        "four ways: <b>(1)</b> verdicts are deterministic rule checks against a written rule "
        "set, so no model judgment can reason a mismatch away; <b>(2)</b> any field below the "
        "confidence bar is <i>uncertain</i>, and uncertain blocks approval exactly like a "
        "mismatch &#8212; the agent never silently approves what it could not read; "
        "<b>(3)</b> every verdict carries the verbatim evidence quote, so CG can spot-check "
        "any field in seconds; <b>(4)</b> the agent cannot send &#8212; an approval only goes "
        "out through Meera's button, and the reply is rendered from the recorded check table, "
        "so it cannot claim anything the comparator did not find.",
        body,
    )
)

story.append(Spacer(1, 4))
story.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=3))
story.append(
    Paragraph(
        "Out of scope this iteration, deliberately: real email integration (the trigger is a "
        "watched folder, per the brief), multi-customer rule packs, rule learning from "
        "amendment history, and cross-document consistency (invoice vs B/L). The last two are "
        "already named as Iteration 2 in the Part 1 PRD.",
        small,
    )
)


def build() -> None:
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=LETTER,
        leftMargin=0.72 * inch,
        rightMargin=0.72 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.5 * inch,
        title="PRD - SU to CG Trade Document Verification Agent (Part 2)",
        author="Swapnil",
    )
    doc.build(story)

    from pypdf import PdfReader

    pages = len(PdfReader(str(OUT)).pages)
    print(f"Wrote {OUT.name} ({OUT.stat().st_size:,} bytes, {pages} page)")
    if pages != 1:
        print(f"ERROR: PRD must be exactly 1 page, got {pages}.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    build()
