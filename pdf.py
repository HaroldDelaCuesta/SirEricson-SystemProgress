"""Builds the PDF downloads (summary report, lists)."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from flask import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

GOLD = colors.HexColor("#9a6b1f")
INK = colors.HexColor("#231d12")


def _t(value):
    """Text that is safe for the PDF's built-in fonts and for its markup."""
    s = "" if value is None else str(value)
    return escape(s.encode("cp1252", "replace").decode("cp1252"))


def build_pdf(title, subtitle, sections, filename, site_name="", wide=False):
    """sections: list of dicts {heading, note?, headers, rows, widths?}. widths are fractions of the page."""
    size = landscape(A4) if wide else A4
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm, title=title[:100], author=site_name[:60])
    base = getSampleStyleSheet()
    brand = ParagraphStyle("brand", parent=base["Normal"], fontSize=9, textColor=GOLD, leading=11)
    h1 = ParagraphStyle("h1", parent=base["Title"], alignment=0, fontSize=20, leading=24, textColor=INK, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=13, textColor=GOLD, spaceBefore=10, spaceAfter=4)
    small = ParagraphStyle("small", parent=base["Normal"], fontSize=9, textColor=colors.grey)
    cell = ParagraphStyle("cell", parent=base["Normal"], fontSize=8.5, leading=10.5)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white)

    story = [Paragraph(_t(site_name).upper(), brand), Paragraph(_t(title), h1), Paragraph(_t(subtitle), small),
             Spacer(1, 6)]
    avail = size[0] - 28 * mm
    for sec in sections:
        story.append(Paragraph(_t(sec["heading"]), h2))
        if sec.get("note"):
            story.append(Paragraph(_t(sec["note"]), small))
        headers, rows = sec["headers"], sec["rows"]
        if not rows:
            story.append(Paragraph("No records.", small))
            continue
        data = [[Paragraph(f"<b>{_t(h)}</b>", head) for h in headers]]
        data += [[Paragraph(_t(c), cell) for c in r] for r in rows]
        widths = [avail * w for w in sec["widths"]] if sec.get("widths") else None
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GOLD),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f1e7")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9ccb0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)

    stamp = datetime.now().strftime("%b %d, %Y %H:%M")

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(14 * mm, 10 * mm, f"{site_name} - generated {stamp}"[:120].encode("cp1252", "replace").decode("cp1252"))
        canvas.drawRightString(size[0] - 14 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return Response(buf.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
