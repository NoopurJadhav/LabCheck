"""Generates a formatted PDF lab report from analyzed results."""

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import io

STATUS_COLORS = {
    "NORMAL": colors.HexColor("#2E7D32"),
    "OUT_OF_RANGE": colors.HexColor("#B26A00"),
    "CRITICAL": colors.HexColor("#C62828"),
    "UNKNOWN_TEST": colors.HexColor("#607D8B"),
}


def _format_patient_cell(r, cell_style):
    lines = [f"<b>{r.get('patient_name') or r['patient_id']}</b>"]
    id_line = r["patient_id"]
    if r.get("sex"):
        id_line += f" · {r['sex']}"
    if r.get("age"):
        id_line += f" · {r['age']}y"
    lines.append(f"<font size=7 color='#666666'>{id_line}</font>")
    extra = r.get("extra") or {}
    if extra:
        extra_text = " · ".join(f"{k}: {v}" for k, v in extra.items())
        lines.append(f"<font size=6.5 color='#888888'>{extra_text}</font>")
    return Paragraph("<br/>".join(lines), cell_style)


def _format_delta_cell(r, cell_style):
    delta = r.get("delta")
    if not delta:
        return Paragraph("<font size=7 color='#888888'>first result on file</font>", cell_style)
    pct = delta["change_percent"]
    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "—")
    color = "#C62828" if delta["flag"] == "SIGNIFICANT_CHANGE" else "#666666"
    text = f"<font color='{color}'><b>{arrow} {abs(pct)}%</b></font><br/>"
    text += f"<font size=6.5 color='#888888'>was {delta['previous_value']} {delta.get('previous_unit') or ''}</font>"
    if delta["flag"] == "SIGNIFICANT_CHANGE":
        text += "<br/><font size=6.5 color='#C62828'><b>⚠ significant change</b></font>"
    return Paragraph(text, cell_style)


def build_pdf_report(report_id: str, report: dict) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        topMargin=14 * mm, bottomMargin=14 * mm,
        leftMargin=14 * mm, rightMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleX", parent=styles["Title"], fontSize=18, spaceAfter=2
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"], fontSize=9, textColor=colors.grey
    )
    cell_style = ParagraphStyle(
        "Cell", parent=styles["Normal"], fontSize=8, leading=10
    )

    elements = []
    elements.append(Paragraph("Laboratory Report", title_style))
    elements.append(Paragraph(f"Report ID: {report_id}", meta_style))
    elements.append(Paragraph(f"Generated: {report['created_at']}", meta_style))
    if report.get("signed_by"):
        elements.append(Paragraph(
            f"Signed off by: {report['signed_by']} ({report.get('signed_role','')}) "
            f"on {report.get('signed_at','')}", meta_style
        ))
    else:
        elements.append(Paragraph("Status: PENDING PATHOLOGIST SIGN-OFF", meta_style))
    elements.append(Spacer(1, 8))

    s = report["summary"]
    summary_text = (
        f"Total results: {s['total']}  |  Normal: {s['normal']}  |  "
        f"Out of range: {s['out_of_range']}  |  Critical: {s['critical']}  |  "
        f"Unknown test: {s['unknown']}  |  vs. patient history: {s.get('significant_changes', 0)}"
    )
    elements.append(Paragraph(summary_text, styles["Normal"]))
    elements.append(Spacer(1, 10))

    header = ["Patient", "Test", "Value", "Normal Range", "vs. Last Result", "Status"]
    data = [header]
    for r in report["results"]:
        range_text = r.get("normal_range") or "—"
        if r.get("used_sex_specific_range"):
            range_text += "\n(sex-specific)"
        data.append([
            _format_patient_cell(r, cell_style),
            Paragraph(r["test_name"], cell_style),
            Paragraph(f"<b>{r['value']} {r.get('unit','')}</b>", cell_style),
            Paragraph(range_text.replace("\n", "<br/>"), cell_style),
            _format_delta_cell(r, cell_style),
            Paragraph(f"<b>{r['status'].replace('_',' ')}</b>", cell_style),
        ])

    table = Table(data, repeatRows=1, colWidths=[52*mm, 32*mm, 28*mm, 38*mm, 42*mm, 30*mm])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16212B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7F9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for i, r in enumerate(report["results"], start=1):
        color = STATUS_COLORS.get(r["status"], colors.black)
        style_cmds.append(("TEXTCOLOR", (5, i), (5, i), color))
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)

    elements.append(Spacer(1, 14))
    elements.append(Paragraph(
        "Auto-generated by LabCheck. Flagged values - whether out of reference "
        "range or significantly changed from the patient's own history - "
        "require pathologist review before being released to the patient.", meta_style
    ))

    doc.build(elements)
    return buffer.getvalue()
