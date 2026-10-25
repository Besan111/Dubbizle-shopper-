"""One-page decision PDF: photo, decision, three numbers, why, elsewhere, plate check, sources."""
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

INK = colors.HexColor("#14213d")
MUTED = colors.HexColor("#5c6370")
GO = colors.HexColor("#1b7f3b")
WAIT = colors.HexColor("#b26a00")
STOP = colors.HexColor("#b3261e")
RULE = colors.HexColor("#d9dde3")


def tone(decision):
    d = decision.upper()
    if d.startswith(("HOLD", "PASS", "NO-GO")):
        return STOP
    return WAIT if any(w in d for w in ("NOT AT", "RAISE", "HIGH", "NEGOTIATE", "ONLY")) else GO


def para(c, text, x, y, width, font="Helvetica", size=9.5, leading=12.5, color=INK):
    c.setFont(font, size)
    c.setFillColor(color)
    for line in simpleSplit(text, font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y


def render(out, case, verdict, plate, photo, sources):
    W, H = A4
    m = 16 * mm
    cw = W - 2 * m
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle(f"Dubbizle Shopper - {case['id']}")
    c.setAuthor("Dubbizle Shopper")

    # header
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(m, H - m - 4, "Dubbizle Shopper")
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    v = case["vehicle"]
    title = f"{v['year']} {v['make']} {v['model']} | {v['km']:,} km | {v['spec']} spec | {case['intent'].title()}"
    c.drawRightString(W - m, H - m - 4, title)
    y = H - m - 14

    # photo
    if photo:
        img = ImageReader(str(photo))
        iw, ih = img.getSize()
        cap = 92 * mm if iw / ih > 1.5 else 74 * mm
        ph = min(cw * ih / iw, cap - (14 * mm if verdict.shortlist else 0))  # room for the shortlist
        pw = ph * iw / ih
        c.drawImage(img, m + (cw - pw) / 2, y - ph, pw, ph)
        if case.get("photo_caption"):
            c.setFont("Helvetica", 7)
            c.setFillColor(colors.white)
            c.drawRightString(m + (cw + pw) / 2 - 6, y - ph + 6, case["photo_caption"])
        y -= ph + 8

    # decision banner
    col = tone(verdict.decision)
    c.setFillColor(col)
    c.roundRect(m, y - 22 * mm, cw, 22 * mm, 3 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(m + 6 * mm, y - 10 * mm, verdict.decision)
    c.setFont("Helvetica", 10)
    for i, line in enumerate(simpleSplit(verdict.action, "Helvetica", 10, cw - 12 * mm)[:2]):
        c.drawString(m + 6 * mm, y - 15.5 * mm - i * 12, line)
    y -= 22 * mm + 8

    # three numbers
    tiles = verdict.tiles or [("List at", f"AED {verdict.list_price:,}"), ("Accept from", f"AED {verdict.floor:,}"),
                              ("Fair value", f"AED {verdict.fair:,}")]
    tw = (cw - 2 * 4 * mm) / 3
    for i, (label, val) in enumerate(tiles):
        x = m + i * (tw + 4 * mm)
        c.setStrokeColor(RULE)
        c.roundRect(x, y - 16 * mm, tw, 16 * mm, 2 * mm, stroke=1, fill=0)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8.5)
        c.drawString(x + 4 * mm, y - 5.5 * mm, label.upper())
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(x + 4 * mm, y - 12.5 * mm, val)
    y -= 16 * mm + 16

    def section(label, lines):
        nonlocal y
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(m, y, label.upper())
        y -= 12
        for ln in lines:
            y = para(c, ln if ln[:1].isdigit() else "- " + ln, m, y, cw)
        y -= 4

    if verdict.shortlist:
        section("Shortlist, ranked by flip profit", verdict.shortlist)
    section("Why", verdict.reasons)
    if verdict.checklist:
        section("Before you pay", verdict.checklist)
    if verdict.intent != "flip":
        section("Elsewhere?", [verdict.export_line])
    section("Plate and chassis check", [plate.get("line", "Not run.")])
    section("Agents", [verdict.agents_line])

    if y < m + 24 * mm:
        raise ValueError("Content runs into the footer; shorten the reasons or checklist to keep one page.")

    # footer: sources and confidence
    c.setStrokeColor(RULE)
    c.line(m, m + 22 * mm, W - m, m + 22 * mm)
    foot = (f"Confidence: {verdict.confidence} ({verdict.comps_used} comparables, asking prices adjusted to "
            f"private-sale value). Sources: " + "; ".join(sources))
    para(c, foot, m, m + 22 * mm - 10, cw, size=7, leading=8.6, color=MUTED)
    c.showPage()
    c.save()
    return out
