"""Generates the two sample trade documents in /sample_docs.

They describe the same shipment (GC-2026-9001) and deliberately disagree in one
place: the invoice declares 18,720.00 kg gross, the Bill of Lading and the ERP
record both say 18,960.00 kg. That 240 kg is the demo's punchline — it is exactly
the class of error a CG validator hunts for by hand today.

The B/L's HS code sits under the carrier stamp, which is why the vision agent
returns it with low confidence and the review UI flags it.

Run:  python tools/make_sample_docs.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "sample_docs"
W, H = A4


def box(c: canvas.Canvas, x: float, y: float, w: float, h: float, title: str = "") -> None:
    c.setLineWidth(0.6)
    c.rect(x, y, w, h)
    if title:
        c.setFont("Helvetica", 6)
        c.setFillGray(0.4)
        c.drawString(x + 2 * mm, y + h - 4 * mm, title.upper())
        c.setFillGray(0)


def lines(c: canvas.Canvas, x: float, y: float, rows: list[str], size: int = 8.5, lead: float = 4.2) -> None:
    c.setFont("Helvetica", size)
    for i, row in enumerate(rows):
        c.drawString(x, y - i * lead * mm, row)


def commercial_invoice(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(W / 2, H - 22 * mm, "COMMERCIAL INVOICE")
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(W / 2, H - 27 * mm, "(Original for Buyer)")

    left, right = 18 * mm, W / 2 + 2 * mm
    colw = W / 2 - 22 * mm
    top = H - 32 * mm

    box(c, left, top - 26 * mm, colw, 26 * mm, "Exporter")
    lines(c, left + 3 * mm, top - 8 * mm, [
        "Sunrise Agro Exports Pvt Ltd",
        "Plot 44, MIDC Industrial Area,",
        "Navi Mumbai 400705, Maharashtra, India",
        "IEC: 0308091234   GSTIN: 27AABCS1429B1ZL",
    ])

    box(c, right, top - 26 * mm, colw, 13 * mm, "Invoice No. & Date")
    lines(c, right + 3 * mm, top - 8 * mm, [
        "Invoice No.: INV-2026-0847",
        "Date: 22-Jun-2026",
    ])
    box(c, right, top - 26 * mm, colw, 13 * mm)
    lines(c, right + 3 * mm, top - 21 * mm, [
        "Buyer's Order No.: PO-SUNP-4471",
        "Exporter's Ref: SAE/EXP/2026/0847",
    ])

    top -= 28 * mm
    box(c, left, top - 24 * mm, colw, 24 * mm, "Consignee")
    lines(c, left + 3 * mm, top - 8 * mm, [
        "Sunpeak Foods BV",
        "Waalhaven Oostzijde 18,",
        "3087 BM Rotterdam, Netherlands",
        "VAT: NL812345678B01",
    ])
    box(c, right, top - 24 * mm, colw, 24 * mm, "Notify Party")
    lines(c, right + 3 * mm, top - 8 * mm, [
        "Same as Consignee",
        "Attn: Documentation Desk",
        "docs@sunpeakfoods.nl",
    ])

    top -= 26 * mm
    qw = (W - 36 * mm) / 4
    for i, (label, value) in enumerate([
        ("Country of Origin", "India"),
        ("Country of Final Destination", "Netherlands"),
        ("Port of Loading", "Nhava Sheva (INNSA)"),
        ("Port of Discharge", "Rotterdam (NLRTM)"),
    ]):
        box(c, left + i * qw, top - 12 * mm, qw, 12 * mm, label)
        lines(c, left + i * qw + 2.5 * mm, top - 8 * mm, [value], size=8)

    top -= 14 * mm
    for i, (label, value) in enumerate([
        ("Vessel / Voyage", "MAERSK CHENNAI / 226W"),
        ("Terms of Delivery", "CIF Rotterdam"),
        ("Terms of Payment", "TT 30 days from B/L date"),
        ("Currency", "USD"),
    ]):
        box(c, left + i * qw, top - 12 * mm, qw, 12 * mm, label)
        lines(c, left + i * qw + 2.5 * mm, top - 8 * mm, [value], size=8)

    # Line items
    top -= 20 * mm
    table_w = W - 36 * mm
    headers = [("Marks & Description of Goods", 0.42), ("HS Code", 0.13),
               ("Quantity", 0.13), ("Rate (USD)", 0.14), ("Amount (USD)", 0.18)]
    c.setFillGray(0.9)
    c.rect(left, top - 7 * mm, table_w, 7 * mm, fill=1, stroke=1)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 8)
    x = left
    for label, frac in headers:
        c.drawString(x + 2 * mm, top - 4.8 * mm, label)
        x += table_w * frac

    c.rect(left, top - 34 * mm, table_w, 27 * mm)
    x = left
    for _, frac in headers[:-1]:
        x += table_w * frac
        c.line(x, top - 34 * mm, x, top)

    col_x = [left]
    for _, frac in headers[:-1]:
        col_x.append(col_x[-1] + table_w * frac)

    lines(c, col_x[0] + 2 * mm, top - 12 * mm, [
        "Indian Basmati Rice, 5% Broken,",
        "packed in 25 kg PP bags",
        "Crop year 2025-26",
        "Marks: SUNP/ROT/2026-0847",
    ], size=8)
    lines(c, col_x[1] + 2 * mm, top - 12 * mm, ["1006.30"], size=8)
    lines(c, col_x[2] + 2 * mm, top - 12 * mm, ["720 BAGS", "18,000.00 KGS"], size=8)
    lines(c, col_x[3] + 2 * mm, top - 12 * mm, ["62.00 / bag"], size=8)
    lines(c, col_x[4] + 2 * mm, top - 12 * mm, ["44,640.00"], size=8)

    top -= 36 * mm
    box(c, left, top - 24 * mm, table_w * 0.55, 24 * mm, "Weights & Packing")
    lines(c, left + 3 * mm, top - 8 * mm, [
        "Total Packages: 720 BAGS (Seven Hundred Twenty Only)",
        "Net Weight: 18,000.00 KGS",
        "Gross Weight: 18,720.00 KGS",
        "Containers: 2 x 40' HC",
    ], size=8)

    box(c, left + table_w * 0.55, top - 24 * mm, table_w * 0.45, 24 * mm, "Amount")
    lines(c, left + table_w * 0.55 + 3 * mm, top - 8 * mm, [
        "FOB Value:            USD 42,100.00",
        "Freight:              USD  2,190.00",
        "Insurance:            USD    350.00",
    ], size=8)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left + table_w * 0.55 + 3 * mm, top - 21 * mm,
                 "TOTAL INVOICE VALUE (CIF): USD 44,640.00")

    top -= 30 * mm
    c.setFont("Helvetica", 7.5)
    c.drawString(left, top, "Amount in words: US Dollars Forty Four Thousand Six Hundred Forty Only")
    c.drawString(left, top - 5 * mm,
                 "We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.")
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(W - 18 * mm, top - 22 * mm, "For Sunrise Agro Exports Pvt Ltd")
    c.setFont("Helvetica", 7.5)
    c.drawRightString(W - 18 * mm, top - 30 * mm, "Authorised Signatory")
    c.save()


def bill_of_lading(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    left = 18 * mm
    table_w = W - 36 * mm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(left, H - 20 * mm, "MAERSK LINE")
    c.setFont("Helvetica", 8)
    c.drawString(left, H - 25 * mm, "Bill of Lading for Ocean Transport or Multimodal Transport")
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(W - 18 * mm, H - 20 * mm, "B/L No. MAEU778213")
    c.setFont("Helvetica", 8)
    c.drawRightString(W - 18 * mm, H - 25 * mm, "Booking No. 226W-4471-SAE")

    top = H - 30 * mm
    colw = table_w / 2

    box(c, left, top - 22 * mm, colw, 22 * mm, "Shipper")
    lines(c, left + 3 * mm, top - 8 * mm, [
        "Sunrise Agro Exports Pvt Ltd",
        "Plot 44, MIDC Industrial Area,",
        "Navi Mumbai 400705, India",
    ])
    box(c, left + colw, top - 22 * mm, colw, 22 * mm, "B/L Issue Date & Place")
    lines(c, left + colw + 3 * mm, top - 8 * mm, [
        "Date of Issue: 27-Jun-2026",
        "Place of Issue: Nhava Sheva, India",
        "Shipped on Board: 27-Jun-2026",
        "No. of Original B/Ls: THREE (3)",
    ])

    top -= 24 * mm
    box(c, left, top - 20 * mm, colw, 20 * mm, "Consignee")
    lines(c, left + 3 * mm, top - 8 * mm, [
        "Sunpeak Foods B.V.",
        "Waalhaven Oostzijde 18,",
        "3087 BM Rotterdam, Netherlands",
    ])
    box(c, left + colw, top - 20 * mm, colw, 20 * mm, "Notify Party")
    lines(c, left + colw + 3 * mm, top - 8 * mm, [
        "Sunpeak Foods B.V.",
        "Attn: Documentation Desk",
        "docs@sunpeakfoods.nl",
    ])

    top -= 22 * mm
    qw = table_w / 4
    for i, (label, value) in enumerate([
        ("Vessel / Voyage", "MAERSK CHENNAI / 226W"),
        ("Port of Loading", "Nhava Sheva, India"),
        ("Port of Discharge", "Rotterdam, Netherlands"),
        ("Place of Delivery", "Rotterdam CY"),
    ]):
        box(c, left + i * qw, top - 13 * mm, qw, 13 * mm, label)
        lines(c, left + i * qw + 2.5 * mm, top - 8.5 * mm, [value], size=7.5)

    # Container / cargo table
    top -= 17 * mm
    headers = [("Container Nos. / Seal Nos.", 0.28), ("Pkgs", 0.10),
               ("Description of Goods (Said to Contain)", 0.40), ("Gross Weight", 0.22)]
    c.setFillGray(0.9)
    c.rect(left, top - 7 * mm, table_w, 7 * mm, fill=1, stroke=1)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 7.5)
    x = left
    for label, frac in headers:
        c.drawString(x + 2 * mm, top - 4.8 * mm, label)
        x += table_w * frac

    c.rect(left, top - 44 * mm, table_w, 37 * mm)
    col_x = [left]
    for _, frac in headers[:-1]:
        col_x.append(col_x[-1] + table_w * frac)
    for x in col_x[1:]:
        c.line(x, top - 44 * mm, x, top)

    lines(c, col_x[0] + 2 * mm, top - 12 * mm, [
        "MRKU4821736 / 40HC",
        "Seal: ML8827341",
        "",
        "MRKU5590128 / 40HC",
        "Seal: ML8827342",
    ], size=7.5)
    lines(c, col_x[1] + 2 * mm, top - 12 * mm, ["360", "BAGS", "", "360", "BAGS"], size=7.5)
    lines(c, col_x[2] + 2 * mm, top - 12 * mm, [
        "SAID TO CONTAIN: INDIAN BASMATI RICE",
        "5% BROKEN IN 25 KG PP BAGS",
        "CROP YEAR 2025-26",
        "MARKS: SUNP/ROT/2026-0847",
        "INVOICE NO. INV-2026-0847",
        "HS: 100630",
        "COUNTRY OF ORIGIN: INDIA",
    ], size=7.5)
    lines(c, col_x[3] + 2 * mm, top - 12 * mm, [
        "9,480.00 KGS",
        "",
        "",
        "9,480.00 KGS",
    ], size=7.5)

    # The carrier stamp lands across the HS code line. This is on purpose: it is
    # why the extractor reports hs_code at ~0.54 confidence instead of 0.95.
    c.saveState()
    c.translate(col_x[2] + 30 * mm, top - 30 * mm)
    c.rotate(14)
    c.setStrokeColorRGB(0.15, 0.25, 0.6)
    c.setFillColorRGB(0.15, 0.25, 0.6)
    c.setLineWidth(1.6)
    c.rect(-26 * mm, -7 * mm, 52 * mm, 14 * mm)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(0, 1.5 * mm, "CLEAN ON BOARD")
    c.setFont("Helvetica", 7)
    c.drawCentredString(0, -4 * mm, "MAERSK LINE  27 JUN 2026")
    c.restoreState()
    c.setFillGray(0)
    c.setStrokeGray(0)

    top -= 46 * mm
    box(c, left, top - 16 * mm, table_w * 0.6, 16 * mm, "Totals")
    lines(c, left + 3 * mm, top - 7 * mm, [
        "Total Packages: 720 BAGS      Net Weight 18,000.00 KGS",
        "Gross Weight 18,960.00 KGS    Measurement: 54.00 CBM",
        "Freight: PREPAID",
    ], size=8)
    box(c, left + table_w * 0.6, top - 16 * mm, table_w * 0.4, 16 * mm, "Carrier")
    lines(c, left + table_w * 0.6 + 3 * mm, top - 7 * mm, [
        "Maersk A/S as Carrier",
        "Signed at Nhava Sheva, 27-Jun-2026",
    ], size=8)

    c.setFont("Helvetica", 6.5)
    c.drawString(left, top - 24 * mm,
                 "RECEIVED by the Carrier the Goods as specified above in apparent good order and condition unless otherwise stated, to be transported to")
    c.drawString(left, top - 28 * mm,
                 "such place as agreed, authorised or permitted herein and subject to all the terms and conditions appearing on the reverse of this Bill of Lading.")
    c.save()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    commercial_invoice(OUT / "commercial_invoice_INV-2026-0847.pdf")
    bill_of_lading(OUT / "bill_of_lading_MAEU778213.pdf")
    print(f"Wrote 2 sample documents to {OUT}")


if __name__ == "__main__":
    main()
