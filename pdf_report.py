# pdf_report.py
"""Generate a branded GOV-1000 PDF report for any federal agency."""
from __future__ import annotations
import io
from datetime import datetime
from fpdf import FPDF


def _safe(text: str) -> str:
    """Replace non-latin1 characters with ASCII equivalents for Helvetica."""
    return (text
            .replace("\u2212", "-")
            .replace("\u2013", "-")
            .replace("\u2014", "-")
            .replace("\u00d7", "x")
            .replace("\u2019", "'")
            .replace("\u2018", "'")
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .encode("latin-1", errors="replace").decode("latin-1"))


class GOVReport(FPDF):
    BLUE = (46, 123, 230)
    DARK = (30, 41, 59)
    GRAY = (100, 116, 139)
    LIGHT_BG = (248, 250, 252)

    def header(self):
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(*self.BLUE)
        self.cell(0, 12, "GOV-1000", align="L")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*self.GRAY)
        self.cell(0, 12, datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
                  align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.BLUE)
        self.set_line_width(0.6)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*self.GRAY)
        self.cell(0, 10,
                  "DISCLAIMER: This report is for informational purposes only. "
                  "Scores are derived from public USASpending.gov data.",
                  align="C")


def _section(pdf: GOVReport, title: str):
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*pdf.BLUE)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*pdf.DARK)


def _kv_row(pdf: GOVReport, label: str, value: str):
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*pdf.GRAY)
    pdf.cell(60, 7, _safe(label))
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*pdf.DARK)
    pdf.cell(0, 7, _safe(value), new_x="LMARGIN", new_y="NEXT")


def generate_pdf(data: dict, axes_labels: list[str], tab_name: str,
                 logic_descriptions: dict | None = None,
                 snapshot: dict | None = None,
                 company_name: str = "") -> bytes:
    pdf = GOVReport(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # White-label
    if company_name:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*pdf.GRAY)
        pdf.cell(0, 7, _safe(f"Prepared for: {company_name}"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Agency name
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*pdf.DARK)
    pdf.cell(0, 10, _safe(f"{data['name']}  -  {tab_name}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Total Score
    _section(pdf, "Total Score")
    pdf.set_font("Helvetica", "B", 36)
    pdf.set_text_color(*pdf.BLUE)
    total = int(data.get("total", 0))
    pdf.cell(50, 18, str(total))
    pdf.set_font("Helvetica", "", 16)
    pdf.set_text_color(*pdf.GRAY)
    pdf.cell(0, 18, "/ 1000", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Grade
    if total >= 800:
        grade, comment = "A", "Strong across most dimensions"
    elif total >= 600:
        grade, comment = "B", "Solid, with some areas to watch"
    elif total >= 400:
        grade, comment = "C", "Mixed signals - warrants deeper analysis"
    else:
        grade, comment = "D", "Significant weaknesses in multiple areas"
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*pdf.DARK)
    pdf.cell(0, 7, f"Grade: {grade}  -  {comment}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Axis Scores table
    _section(pdf, "Score Breakdown")
    pdf.ln(2)
    pdf.set_fill_color(*pdf.LIGHT_BG)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*pdf.DARK)
    pdf.cell(60, 8, "Axis", border=1, fill=True)
    pdf.cell(25, 8, "Score", border=1, fill=True, align="C")
    desc_header = "Description" if logic_descriptions else ""
    pdf.cell(0, 8, desc_header, border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    for k in axes_labels:
        v = int(data["axes"].get(k, 0))
        pdf.set_text_color(*pdf.DARK)
        pdf.cell(60, 7, _safe(k), border=1)

        if v >= 160:
            pdf.set_text_color(16, 185, 129)
        elif v >= 100:
            pdf.set_text_color(*pdf.BLUE)
        else:
            pdf.set_text_color(239, 68, 68)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(25, 7, str(v), border=1, align="C")

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*pdf.GRAY)
        desc = logic_descriptions.get(k, "") if logic_descriptions else ""
        pdf.cell(0, 7, _safe(desc), border=1, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)

    # Snapshot
    if snapshot:
        _section(pdf, "Budget Snapshot")
        for label, value in snapshot.items():
            _kv_row(pdf, label, str(value))
        pdf.ln(4)

    # Footer branding
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*pdf.GRAY)
    pdf.cell(0, 5,
             f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} "
             "by GOV-1000 Scoring Engine",
             align="C")

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
