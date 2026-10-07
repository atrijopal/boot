"""The meeting record as a PDF built for paper: its own layout (not a screenshot of the page), numbered sections, tables where they help, headers and footers
with page numbers, restrained use of one accent colour. Built only from the checked Result, so it carries exactly the decisions and tasks the other formats do."""
import io
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

from app.config.settings import ROOT
from app.core import insights
from app.core.report import label_name, mmss, names, owner_of, registers, who
from app.core.schemas import Result

FONTS = ROOT / "web" / "static" / "fonts"
INK, MUTED, RULE, ACCENT, NAVY, WASH = (colors.HexColor(x) for x in ("#151412", "#7b756c", "#d8d1c3", "#b8620a", "#232b3a", "#f6f2ea"))
_registered = False


def _fonts() -> None:
    global _registered
    if _registered:
        return
    for name, file in (("Plex", "IBMPlexSans-Regular.ttf"), ("Plex-Medium", "IBMPlexSans-Medium.ttf"), ("Plex-Semi", "IBMPlexSans-SemiBold.ttf"), ("Plex-Italic", "IBMPlexSans-Italic.ttf"),
                       ("Mono", "IBMPlexMono-Regular.ttf"), ("Mono-Medium", "IBMPlexMono-Medium.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(FONTS / file)))
    pdfmetrics.registerFontFamily("Plex", normal="Plex", bold="Plex-Semi", italic="Plex-Italic", boldItalic="Plex-Semi")
    _registered = True


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName="Plex", fontSize=9.6, leading=14, textColor=INK, alignment=TA_LEFT)
    return {
        "body": ParagraphStyle("body", **base, spaceAfter=3),
        "lead": ParagraphStyle("lead", **{**base, "fontName": "Plex-Medium", "fontSize": 12, "leading": 17}, spaceAfter=6),
        "title": ParagraphStyle("title", **{**base, "fontName": "Plex-Semi", "fontSize": 25, "leading": 29}, spaceAfter=3),
        "sub": ParagraphStyle("sub", **{**base, "fontSize": 10.5, "textColor": MUTED}, spaceAfter=10),
        "h": ParagraphStyle("h", **{**base, "fontName": "Plex-Semi", "fontSize": 12.5, "leading": 16}, spaceBefore=16, spaceAfter=5, keepWithNext=1),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Plex-Semi", "fontSize": 10.3}, spaceBefore=7, spaceAfter=2, keepWithNext=1),
        "meta": ParagraphStyle("meta", **{**base, "fontName": "Mono", "fontSize": 7.4, "leading": 10, "textColor": MUTED}),
        "metaval": ParagraphStyle("metaval", **{**base, "fontSize": 9.6, "leading": 13}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 9, "leading": 12.4}),
        "cellmono": ParagraphStyle("cellmono", **{**base, "fontName": "Mono", "fontSize": 7.8, "leading": 11, "textColor": MUTED}),
        "th": ParagraphStyle("th", **{**base, "fontName": "Mono-Medium", "fontSize": 7.2, "leading": 9, "textColor": colors.white}),
        "unspec": ParagraphStyle("unspec", **{**base, "fontName": "Plex-Italic", "fontSize": 9, "leading": 12.4, "textColor": MUTED}),
        "bullet": ParagraphStyle("bullet", **base, leftIndent=11, bulletIndent=0, spaceAfter=3),
        "note": ParagraphStyle("note", **{**base, "fontSize": 8.4, "leading": 12, "textColor": MUTED}, spaceBefore=10),
    }


class _Numbered(canvas.Canvas):
    """Page X of Y: the total is only known once every page has been laid out."""

    def __init__(self, *a, footer_left: str = "", header_right: str = "", **k):
        super().__init__(*a, **k)
        self._pages, self.footer_left, self.header_right = [], footer_left, header_right

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._decorate(n)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        w, h = A4
        self.setStrokeColor(RULE)
        self.setLineWidth(0.5)
        self.line(22 * mm, h - 15 * mm, w - 22 * mm, h - 15 * mm)
        self.line(22 * mm, 15 * mm, w - 22 * mm, 15 * mm)
        self.setFont("Mono", 7)
        self.setFillColor(MUTED)
        self.drawString(22 * mm, h - 12.4 * mm, "MEETING RECORD")
        self.drawRightString(w - 22 * mm, h - 12.4 * mm, self.header_right)
        self.drawString(22 * mm, 10.8 * mm, self.footer_left)
        self.drawRightString(w - 22 * mm, 10.8 * mm, f"Page {self._pageNumber} of {total}")


def _p(text: str, st: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(" ".join(str(text).split())), st)


def _src(rec, nm, S) -> Paragraph:
    e = rec.evidence[0]
    return Paragraph(f'{escape(mmss(e.start))}<br/>{escape(who(e.speaker, nm))}', S["cellmono"])


def _table(head: list[str], rows: list[list], widths: list[float], S) -> Table:
    t = Table([[Paragraph(h, S["th"]) for h in head]] + rows, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WASH])]))
    return t


def build(r: Result, sess: dict, corrections: list[dict] | None = None) -> bytes:
    """`sess` supplies the date and source file name; `corrections` are the transcript fixes in force, [{time, original, corrected}]."""
    _fonts()
    S = _styles()
    nm = names(r)
    reg = registers(r)
    date = sess.get("meeting_date") or (sess.get("created_at") or "")[:10] or "not provided"
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=22 * mm, bottomMargin=22 * mm, title=f"Meeting record {date}", author="Meeting Record", subject=sess.get("filename", ""))
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)])])
    W = doc.width / mm
    out: list = []

    bar = Table([[""]], colWidths=[26 * mm], rowHeights=[2.2 * mm])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    bar.hAlign = "LEFT"
    out += [bar, Spacer(1, 7), Paragraph("Meeting record", S["title"]), _p(sess.get("filename") or "Recording", S["sub"])]

    people = [who(l, nm) for l in sorted({s.speaker for s in r.refined.segments if s.speaker_known})]
    meta = [("DATE", date + ("" if sess.get("meeting_date") else " (processed)")), ("DURATION", mmss(r.audio.duration_seconds)),
            ("PARTICIPANTS", ", ".join(people) if people else "Speakers were not distinguished in this recording"), ("PREPARED", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))]
    mt = Table([[Paragraph(k, S["meta"]), _p(v, S["metaval"])] for k, v in meta], colWidths=[28 * mm, (W - 28) * mm])
    mt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("LINEBELOW", (0, -1), (-1, -1), 0.5, RULE)]))
    out += [mt, Spacer(1, 10)]
    if r.headline:
        out.append(_p(r.headline.text, S["lead"]))

    n = [0]

    def section(title: str) -> Paragraph:
        n[0] += 1
        return Paragraph(f'<font name="Mono-Medium" color="#b8620a" size="9">{n[0]:02d}</font>&nbsp;&nbsp;{escape(title)}', S["h"])

    if r.summary:
        out.append(section("Summary"))
        out += [Paragraph(escape(" ".join(c.text.split())), S["bullet"], bulletText="•") for c in r.summary]
    if r.topics:
        out.append(section("Discussion by topic"))
        for t in r.topics:
            block = [Paragraph(f'{escape(t.title)} <font name="Mono" size="7.4" color="#7b756c">&nbsp; {mmss(t.start)}–{mmss(t.end)}{" · " + t.outcome if t.outcome else ""}</font>', S["h2"])]
            block += [Paragraph(escape(" ".join(b.text.split())), S["bullet"], bulletText="•") for b in t.bullets]
            out.append(KeepTogether(block) if len(block) < 7 else block[0])
            if len(block) >= 7:
                out += block[1:]

    out.append(section("Key decisions"))
    dec = reg["decisions"]
    if dec:
        rows = []
        for i, x in enumerate(dec, 1):
            ctx = r.decision_context.get(x.rid)
            cell = [Paragraph(escape(" ".join(x.text.split())), S["cell"])] + ([Paragraph(escape(" ".join(ctx.text.split())), S["unspec"])] if ctx else [])
            if x.status != "confirmed":
                cell.append(Paragraph(f"Status: {escape(x.status)}", S["cellmono"]))
            rows.append([Paragraph(f"D{i}", S["cellmono"]), cell, _src(x, nm, S)])
        out.append(_table(["#", "DECISION", "SOURCE"], rows, [10, W - 10 - 30, 30], S))
    else:
        out.append(_p("No explicit decisions were identified in this recording.", S["unspec"]))

    out.append(section("Action items"))
    acts = reg["action_items"]
    if acts:
        rows = []
        for i, x in enumerate(acts, 1):
            owner = owner_of(x, nm)
            dl = x.attrs.get("deadline")
            rows.append([Paragraph(f"A{i}", S["cellmono"]), _p(x.text, S["cell"]), _p(owner, S["cell"]) if owner != "unspecified" else _p("Unspecified", S["unspec"]),
                         _p(dl, S["cell"]) if dl else _p("Unspecified", S["unspec"]), _src(x, nm, S)])
        out.append(_table(["#", "TASK", "OWNER", "DEADLINE", "SOURCE"], rows, [10, W - 10 - 33 - 30 - 30, 33, 30, 30], S))
    else:
        out.append(_p("No actionable tasks were identified in this recording.", S["unspec"]))

    oq = [x for x in reg["open_questions"] if x.status == "open"]
    if oq:
        out.append(section("Open questions"))
        out += [Paragraph(f'{escape(" ".join(x.text.split()))} <font name="Mono" size="7.4" color="#7b756c">&nbsp;{mmss(x.start)}</font>', S["bullet"], bulletText="•") for x in oq]
    if reg["key_figures"]:
        out.append(section("Key figures"))
        out.append(_table(["FIGURE", "VALUE", "SOURCE"], [[_p(x.attrs.get("label", ""), S["cell"]), _p(x.attrs.get("value", ""), S["cell"]), _src(x, nm, S)] for x in reg["key_figures"]], [W - 40 - 30 - 0, 40, 30][:3], S))
    notes = insights.attention(r)
    if notes or corrections:
        out.append(section("Notes"))
        out += [Paragraph(escape(x), S["bullet"], bulletText="•") for x in notes]
        if corrections:
            out.append(Paragraph("Corrections applied to the transcript", S["h2"]))
            out.append(_table(["TIME", "AS HEARD", "CORRECTED TO"], [[Paragraph(c["time"], S["cellmono"]), _p(c["original"], S["cell"]), _p(c["corrected"], S["cell"])] for c in corrections[:25]], [18, (W - 18) / 2, (W - 18) / 2], S))
    out.append(Paragraph("Every item in this record is taken from the recording; the source column gives the time and speaker. Owners and deadlines appear only where the recording states them; "
                         "otherwise they are marked Unspecified. The raw transcript is kept separately and unchanged.", S["note"]))

    doc.build(out, canvasmaker=lambda *a, **k: _Numbered(*a, footer_left=f"{sess.get('filename') or 'recording'}", header_right=date, **k))
    return buf.getvalue()
