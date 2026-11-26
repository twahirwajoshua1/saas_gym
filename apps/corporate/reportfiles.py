"""
The report a gym sends a company, as a file it can open.

Two shapes of the same thing: a PDF to read or print, and an Excel file to
sort and total. Both hold every visit in detail, then a summary per service.
Nothing here touches the database: it is given the lines and writes them out.
"""

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.corporate.services import ReportLine

COLUMNS = [
    ("Date and time", 34),
    ("Company", 20),
    ("Staff no.", 12),
    ("Employee", 28),
    ("Department", 20),
    ("Branch", 18),
    ("Activity", 20),
    ("How", 26),
    ("RWF", 12),
]


def _row(line: ReportLine) -> list[str]:
    activity = line.service_name
    how = line.how
    if line.cancelled:
        activity = f"{line.service_name} (taken off)"
        how = f"Taken off the same day: {line.cancel_note}" if line.cancel_note else "Taken off"
    return [
        f"{line.when:%d %b %Y, %H:%M}",
        line.company_name,
        line.employee_number,
        line.employee_name,
        line.department,
        line.branch_name,
        activity,
        how,
        "" if line.cancelled else f"{line.amount_rwf:,}",
    ]


def file_name(*, gym_name: str, company_name: str, start: date, end: date, suffix: str) -> str:
    """A name that says what is inside, so a folder of these stays readable."""
    parts = [gym_name, company_name, f"{start:%Y-%m}", f"{end:%Y-%m}"]
    safe = [
        "".join(ch for ch in part if ch.isalnum() or ch in " -_").strip().replace(" ", "-")
        for part in parts
    ]
    return f"{'-'.join(safe)}.{suffix}"


def to_excel(
    *,
    gym_name: str,
    heading: str,
    lines: list[ReportLine],
    totals: list[tuple[str, int, int]],
) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Visits"

    sheet["A1"] = gym_name
    sheet["A1"].font = Font(bold=True, size=14)
    sheet["A2"] = heading
    sheet.append([])

    header_row = sheet.max_row + 1
    sheet.append([name for name, _ in COLUMNS])
    for column in range(1, len(COLUMNS) + 1):
        cell = sheet.cell(row=header_row, column=column)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top")

    for line in lines:
        sheet.append(_row(line))

    for index, (_, width) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)

    summary = book.create_sheet("Summary")
    summary["A1"] = gym_name
    summary["A1"].font = Font(bold=True, size=14)
    summary["A2"] = heading
    summary.append([])
    summary.append(["Service", "Times used", "Amount (RWF)"])
    for column in "ABC":
        summary[f"{column}{summary.max_row}"].font = Font(bold=True)
    for name, times, amount in totals:
        summary.append([name, times, amount])
    summary.append(["Total", sum(t for _, t, _ in totals), sum(a for _, _, a in totals)])
    for column in "ABC":
        summary[f"{column}{summary.max_row}"].font = Font(bold=True)
    for column, width in zip("ABC", (34, 14, 16), strict=True):
        summary.column_dimensions[column].width = width

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def to_pdf(
    *,
    gym_name: str,
    heading: str,
    lines: list[ReportLine],
    totals: list[tuple[str, int, int]],
) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=heading,
        author=gym_name,
    )
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small")
    small.fontSize = 7.5
    small.leading = 9.5

    story = [
        Paragraph(gym_name, styles["Title"]),
        Paragraph(heading, styles["Heading2"]),
        Spacer(1, 6),
    ]

    if totals:
        summary = [["Service", "Times used", "Amount (RWF)"]]
        summary += [[name, str(times), f"{amount:,}"] for name, times, amount in totals]
        summary.append(
            [
                "Total",
                str(sum(t for _, t, _ in totals)),
                f"{sum(a for _, _, a in totals):,}",
            ]
        )
        table = Table(summary, hAlign="LEFT", colWidths=[70 * mm, 30 * mm, 35 * mm])
        table.setStyle(_style(last_row_bold=True))
        story += [table, Spacer(1, 10)]

    story.append(Paragraph("Every visit", styles["Heading3"]))
    body = [[Paragraph(name, small) for name, _ in COLUMNS]]
    body += [[Paragraph(value, small) for value in _row(line)] for line in lines]
    widths = [32 * mm, 26 * mm, 18 * mm, 42 * mm, 30 * mm, 26 * mm, 30 * mm, 42 * mm, 20 * mm]
    visits = Table(body, repeatRows=1, hAlign="LEFT", colWidths=widths)
    visits.setStyle(_style())
    story.append(visits)

    document.build(story)
    return buffer.getvalue()


def _style(*, last_row_bold: bool = False) -> TableStyle:
    rules = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4FD8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8dce3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if last_row_bold:
        rules.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    return TableStyle(rules)
