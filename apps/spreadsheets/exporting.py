"""
Writing Excel files: the import template and the exports.

Every text value is written as plain text. Excel would otherwise run a value
starting with "=" (for example a member's name typed as "=HYPERLINK(...)") as a
formula when the file is opened.
"""

import io
from datetime import date, datetime

from django.core.exceptions import PermissionDenied
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from apps.attendance import services as attendance
from apps.audit import services as audit
from apps.billing import services as billing
from apps.billing.models import Payment
from apps.gyms.models import Gym, StaffAssignment
from apps.gyms.permissions import MANAGER_ROLES, require_role
from apps.members import services as members
from apps.members.models import Member
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE
from apps.spreadsheets.reading import TEMPLATE_HEADINGS

BOLD = Font(bold=True)


def _cell(sheet, value, *, bold=False):
    cell = WriteOnlyCell(sheet, value=value)
    if isinstance(value, str):
        cell.data_type = "s"  # always text, never a formula
    if isinstance(value, date) and not isinstance(value, datetime):
        cell.number_format = "dd/mm/yyyy"
    if isinstance(value, datetime):
        cell.number_format = "dd/mm/yyyy hh:mm"
    if bold:
        cell.font = BOLD
    return cell


def _workbook(title: str, headings: list[str], rows) -> bytes:
    book = Workbook(write_only=True)
    sheet = book.create_sheet(title)
    sheet.append([_cell(sheet, heading, bold=True) for heading in headings])
    for row in rows:
        sheet.append([_cell(sheet, value) for value in row])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def template() -> bytes:
    """An empty import template with one example row."""
    example = [
        "Example Person (delete this row)",
        "0788123456",
        "",
        "F",
        date(1995, 3, 14),
        "Mama Example",
        "0788654321",
        "",
        "Monthly",
        "",
    ]
    return _workbook("Members", list(TEMPLATE_HEADINGS.values()), [example])


def _record_export(by: StaffAssignment, what: str, count: int, extra: dict | None = None):
    audit.record(
        action=f"export.{what}",
        summary=f"{count} {what} exported to Excel",
        gym=by.gym,
        actor=by.person,
        details={"rows": count, **(extra or {})},
    )


def export_members(*, by: StaffAssignment) -> bytes:
    """All the gym's members with their current membership. Owner and branch manager."""
    require_role(by, MANAGER_ROLES)
    gym: Gym = by.gym
    today = timezone.localdate()
    all_members = list(Member.objects.for_gym(gym).select_related("person", "branch"))
    current = {m.member_id: m for m in members.memberships_valid_on(gym, today)}
    payments_on = modules.is_enabled(gym, PAYMENTS_FINANCE)
    balances = billing.balances_for(gym, [m.id for m in all_members]) if payments_on else {}

    headings = [
        "Full name",
        "Phone",
        "Branch",
        "Gender",
        "Date of birth",
        "Emergency contact name",
        "Emergency contact phone",
        "Joined",
        "Current package",
        "Valid until",
    ]
    if payments_on:
        headings.append("Owes (RWF)")

    def rows():
        for member in all_members:
            membership = current.get(member.id)
            row = [
                member.full_name,
                member.person.phone,
                member.branch.name,
                member.get_gender_display(),
                member.date_of_birth,
                member.emergency_contact_name,
                member.emergency_contact_phone,
                member.joined_on,
                membership.package_name if membership else "",
                membership.end_date if membership else None,
            ]
            if payments_on:
                row.append(balances.get(member.id, 0))
            yield row

    content = _workbook("Members", headings, rows())
    _record_export(by, "members", len(all_members))
    return content


def export_payments(*, by: StaffAssignment, start: date, end: date) -> bytes:
    """Payments received between two days. Owner and branch manager."""
    require_role(by, MANAGER_ROLES)
    if not modules.is_enabled(by.gym, PAYMENTS_FINANCE):
        raise PermissionDenied("Payments and Finance is not switched on for this gym.")
    payments = list(
        Payment.objects.for_gym(by.gym)
        .filter(received_on__range=(start, end))
        .select_related("member__person", "received_by", "reversal")
        .order_by("number")
    )
    headings = [
        "Receipt",
        "Date",
        "Member",
        "Phone",
        "Paid by",
        "Reference",
        "Amount (RWF)",
        "Reversed",
        "Reversal reason",
        "Received by",
    ]

    def rows():
        for p in payments:
            reversal = getattr(p, "reversal", None)
            yield [
                p.display_number,
                p.received_on,
                p.member.full_name,
                p.member.person.phone,
                p.get_method_display(),
                p.reference,
                p.amount_rwf,
                "Yes" if reversal else "No",
                reversal.reason if reversal else "",
                p.received_by.full_name if p.received_by else "",
            ]

    content = _workbook("Payments", headings, rows())
    _record_export(
        by, "payments", len(payments), {"from": start.isoformat(), "to": end.isoformat()}
    )
    return content


def export_visits(*, by: StaffAssignment, start: date, end: date) -> bytes:
    """Check-ins between two days. Owner and branch manager."""
    require_role(by, MANAGER_ROLES)
    if not modules.is_enabled(by.gym, ATTENDANCE_ACCESS):
        raise PermissionDenied("Attendance and Access is not switched on for this gym.")
    visits = list(attendance.check_ins_between(by.gym, start, end).select_related("member__person"))
    headings = ["Date", "Time", "Member", "Phone", "Branch", "Admitted by"]

    def rows():
        for v in visits:
            yield [
                v.checked_in_on,
                timezone.localtime(v.checked_in_at).strftime("%H:%M"),
                v.member.full_name,
                v.member.person.phone,
                v.branch.name,
                v.admitted_by.full_name if v.admitted_by else "",
            ]

    content = _workbook("Visits", headings, rows())
    _record_export(by, "visits", len(visits), {"from": start.isoformat(), "to": end.isoformat()})
    return content
