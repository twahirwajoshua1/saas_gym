"""Tests for importing members from a spreadsheet and exporting to Excel."""

import io
import uuid
from datetime import date, datetime, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from apps.attendance import services as attendance
from apps.audit.models import AuditEntry
from apps.billing import services as billing
from apps.billing.models import Invoice
from apps.billing.services import PaymentInput
from apps.dashboard import services as dashboard
from apps.gyms import services as gym_services
from apps.gyms.models import Role
from apps.members import services as members
from apps.members.models import Member, Membership
from apps.members.services import MemberDetails
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE
from apps.people.models import Person
from apps.spreadsheets import exporting, importing
from apps.spreadsheets.reading import UnreadableSpreadsheet, parse_date, read_rows

pytestmark = pytest.mark.django_db

HEADINGS = ["Full name", "Phone", "Branch", "Gender", "Date of birth", "Package", "Valid until"]


def today():
    return timezone.localdate()


def xlsx(rows, headings=HEADINGS) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(headings)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def csv_bytes(text: str, encoding="utf-8") -> bytes:
    return text.encode(encoding)


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    modules.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    modules.enable_module(gym=staff.gym, code=ATTENDANCE_ACCESS)
    members.create_package(
        by=staff.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )
    return staff


# --- Reading files ----------------------------------------------------------


def test_reading_an_excel_file_with_numbers_and_dates_as_excel_writes_them():
    content = xlsx(
        [
            [
                "Eric Mugisha",
                788111222,
                None,
                "M",
                datetime(1995, 3, 14),
                "Monthly",
                datetime(2027, 1, 10),
            ],
            [None, None, None, None, None, None, None],  # empty rows are skipped
            ["Claudine", "0722 333 444", "", "", "", "", ""],
        ]
    )

    rows = read_rows("members.xlsx", content)

    assert [r["_line"] for r in rows] == [2, 4]
    assert rows[0]["phone"] == "788111222"  # a number in Excel, read without ".0"
    assert rows[0]["date_of_birth"] == datetime(1995, 3, 14)
    assert rows[1]["phone"] == "0722 333 444"


def test_reading_a_csv_with_semicolons_and_loose_headings():
    text = "Names;Telephone;Sex;Expiry date;Plan\nEric Mugisha;0788111222;gabo;15/12/2026;Monthly\n"

    rows = read_rows("export.CSV", csv_bytes(text))

    assert rows == [
        {
            "full_name": "Eric Mugisha",
            "phone": "0788111222",
            "gender": "gabo",
            "valid_until": "15/12/2026",
            "package": "Monthly",
            "_line": 2,
        }
    ]


def test_reading_a_csv_saved_by_older_excel():
    rows = read_rows("old.csv", csv_bytes("Name,Phone\nRené Ndayisaba,0788111222\n", "cp1252"))

    assert rows[0]["full_name"] == "René Ndayisaba"


@pytest.mark.parametrize(
    ("name", "content", "reason"),
    [
        ("members.xls", b"anything", "wrong_type"),
        ("members.pdf", b"anything", "wrong_type"),
        ("members.xlsx", b"not really excel", "not_excel"),
        ("members.csv", b"", "empty"),
        ("members.csv", b"Name,Email\nEric,e@x.rw\n", "missing_columns"),
        ("members.csv", b"Name,Phone\n", "no_rows"),
        ("members.csv", b"Name,Phone\n" + b"Eric,0788111222\n" * 2001, "too_many_rows"),
        ("members.csv", b"x" * (5 * 1024 * 1024 + 1), "too_big"),
    ],
)
def test_unusable_files_are_explained(name, content, reason):
    with pytest.raises(UnreadableSpreadsheet) as problem:
        read_rows(name, content)

    assert problem.value.reason == reason


def test_missing_columns_are_named():
    with pytest.raises(UnreadableSpreadsheet) as problem:
        read_rows("m.csv", b"Name,Email\nEric,e@x.rw\n")

    assert problem.value.detail == "Phone"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("15/12/2026", date(2026, 12, 15)),  # day first, as written in Rwanda
        ("03/04/2026", date(2026, 4, 3)),
        ("15-12-2026", date(2026, 12, 15)),
        ("15.12.2026", date(2026, 12, 15)),
        ("2026-12-15", date(2026, 12, 15)),
        (datetime(2026, 12, 15, 0, 0), date(2026, 12, 15)),
        ("31/02/2026", None),
        ("next week", None),
        ("", None),
    ],
)
def test_reading_dates(value, expected):
    assert parse_date(value) == expected


# --- Checking rows ----------------------------------------------------------


def rows_from(*lines):
    return read_rows("m.xlsx", xlsx(list(lines)))


def test_checking_labels_each_row_and_saves_nothing(alpha):
    members.register_member(
        by=alpha.assignment, phone="0788999000", details=MemberDetails(full_name="Already here")
    )
    rows = rows_from(
        ["Eric Mugisha", "0788111222", "", "M", "", "Monthly", "15/01/2099"],
        ["Ready Person", "788 333 444", "", "", "", "", ""],
        ["", "0788555666", "", "", "", "", ""],
        ["Bad Phone", "12345", "", "", "", "", ""],
        ["Already", "+250788999000", "", "", "", "", ""],
        ["Repeated", "0788333444", "", "", "", "", ""],
        ["Odd details", "0788777888", "Nowhere", "X", "yesterday", "Gold", "2026-13-40"],
    )
    people_before = Person.objects.count()

    check = importing.check_rows(alpha.gym, rows)

    assert Person.objects.count() == people_before  # nothing saved
    assert [r.line for r in check.ready] == [3]
    assert [(r.line, r.existing_name) for r in check.existing] == [(6, "Already here")]
    problems = {r.line: r.problems for r in check.problems}
    assert problems[2] == ["valid_until:too_far"]
    assert problems[4] == ["full_name:required"]
    assert problems[5] == ["phone:invalid"]
    assert problems[7] == ["phone:repeated:3"]
    assert sorted(problems[8]) == sorted(
        ["branch:not_found", "gender:invalid", "date_of_birth:invalid", "package:not_found"]
    )


def test_membership_columns_must_come_together_and_be_current(alpha):
    rows = rows_from(
        ["Only package", "0788100001", "", "", "", "Monthly", ""],
        ["Only date", "0788100002", "", "", "", "", "15/12/2099"],
        ["Ended", "0788100003", "", "", "", "Monthly", (today() - timedelta(days=1)).isoformat()],
        ["Good", "0788100004", "", "", "", "monthly", (today() + timedelta(days=20)).isoformat()],
    )

    check = importing.check_rows(alpha.gym, rows)

    problems = {r.line: r.problems for r in check.problems}
    assert problems == {
        2: ["valid_until:required"],
        3: ["package:required"],
        4: ["valid_until:in_past"],
    }
    assert [r.line for r in check.ready] == [5]  # package names ignore capitals


def test_a_gym_with_several_branches_needs_the_branch_column(alpha):
    gym_services.add_branch(gym=alpha.gym, name="Remera")
    rows = rows_from(
        ["No branch", "0788100001", "", "", "", "", ""],
        ["With branch", "0788100002", "remera", "", "", "", ""],
    )

    check = importing.check_rows(alpha.gym, rows)

    assert [r.problems for r in check.problems] == [["branch:required"]]
    assert [r.line for r in check.ready] == [3]


# --- Importing --------------------------------------------------------------


def test_importing_saves_ready_rows_with_their_memberships(alpha):
    until = today() + timedelta(days=20)
    rows = rows_from(
        [
            "Eric Mugisha",
            788111222,
            "",
            "gabo",
            datetime(1995, 3, 14),
            "Monthly",
            until.isoformat(),
        ],
        ["Claudine Mukamana", "0722333444", "", "F", "", "", ""],
        ["Bad", "123", "", "", "", "", ""],
    )

    result = importing.import_rows(by=alpha.assignment, rows=rows, file_name="old-list.xlsx")

    assert (result.members_added, result.memberships_added, result.skipped) == (2, 1, 1)
    eric = Member.objects.for_gym(alpha.gym).get(full_name="Eric Mugisha")
    assert eric.phone == "+250788111222"
    assert eric.gender == "male" and eric.date_of_birth == date(1995, 3, 14)
    membership = Membership.objects.get(member=eric)
    assert membership.imported and membership.end_date == until and membership.start_date == today()
    assert membership.status == "active"

    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="members.imported")
    assert entry.actor == alpha.person and "old-list.xlsx" in entry.summary
    assert entry.details["members_added"] == 2


def test_imported_members_can_check_in_and_owe_nothing(alpha):
    rows = rows_from(
        ["Eric", "0788111222", "", "", "", "Monthly", (today() + timedelta(days=5)).isoformat()]
    )
    importing.import_rows(by=alpha.assignment, rows=rows)
    eric = Member.objects.for_gym(alpha.gym).get()

    assert attendance.check_in(by=alpha.assignment, member=eric)[1] is True
    assert billing.member_balance(eric) == 0
    assert not Invoice.objects.exists()
    figures = dashboard.overview(alpha.gym, today())
    assert figures.active_members == 1
    assert figures.money.by_package == []  # brought in, not sold this month
    assert figures.ending_soon == 1


def test_an_imported_member_can_renew_from_the_day_after(alpha):
    until = today() + timedelta(days=3)
    importing.import_rows(
        by=alpha.assignment,
        rows=rows_from(["Eric", "0788111222", "", "", "", "Monthly", until.isoformat()]),
    )
    eric = Member.objects.for_gym(alpha.gym).get()

    assert members.next_start_date(eric) == until + timedelta(days=1)


def test_a_failure_part_way_saves_nothing(alpha, monkeypatch):
    rows = rows_from(
        ["First", "0788100001", "", "", "", "", ""], ["Second", "0788100002", "", "", "", "", ""]
    )
    real = members.register_member
    calls = []

    def fail_on_second(**kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the power went off")
        return real(**kwargs)

    monkeypatch.setattr(members, "register_member", fail_on_second)

    with pytest.raises(RuntimeError):
        importing.import_rows(by=alpha.assignment, rows=rows)

    assert not Member.objects.exists()
    assert not AuditEntry.objects.filter(action="members.imported").exists()


def test_rows_registered_at_the_desk_after_the_preview_are_skipped(alpha):
    rows = rows_from(["Eric", "0788111222", "", "", "", "", ""])
    assert len(importing.check_rows(alpha.gym, rows).ready) == 1
    members.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric at desk")
    )

    result = importing.import_rows(by=alpha.assignment, rows=rows)

    assert (result.members_added, result.skipped) == (0, 1)
    assert Member.objects.count() == 1


@pytest.mark.parametrize("role", [Role.RECEPTIONIST, Role.SALES_REP, Role.TRAINER])
def test_only_owners_and_managers_import(alpha, add_staff, role):
    staff = add_staff(alpha.gym, "0788000005", role)

    with pytest.raises(PermissionDenied):
        importing.import_rows(
            by=staff.assignment, rows=rows_from(["E", "0788111222", "", "", "", "", ""])
        )


def test_a_number_that_is_a_member_of_another_gym_imports_without_revealing_it(alpha, make_gym):
    bravo = make_gym("Bravo Fitness", "0788000002")
    members.register_member(
        by=bravo.assignment, phone="0788111222", details=MemberDetails(full_name="Bravo's name")
    )

    check = importing.check_rows(
        alpha.gym, rows_from(["Alpha's name", "0788111222", "", "", "", "", ""])
    )
    importing.import_rows(
        by=alpha.assignment, rows=rows_from(["Alpha's name", "0788111222", "", "", "", "", ""])
    )

    assert len(check.ready) == 1 and not check.existing
    assert Member.objects.for_gym(alpha.gym).get().full_name == "Alpha's name"
    assert Member.objects.for_gym(bravo.gym).get().full_name == "Bravo's name"


# --- Screens: importing -----------------------------------------------------


def upload(client, content, name="members.xlsx"):
    return client.post(reverse("spreadsheets:upload"), {"file": SimpleUploadedFile(name, content)})


def test_upload_preview_confirm(alpha, sign_in):
    client = sign_in(alpha)
    content = xlsx(
        [
            [
                "Eric Mugisha",
                "0788111222",
                "",
                "",
                "",
                "Monthly",
                (today() + timedelta(days=9)).isoformat(),
            ],
            ["Bad", "12345", "", "", "", "", ""],
        ]
    )

    preview = upload(client, content)
    page = preview.content.decode()
    assert "Nothing has been saved yet." in page
    assert "Phone number is missing or not valid." in page
    assert "Import 1 member" in page
    assert not Member.objects.exists()

    token = preview.context["token"]
    done = client.post(reverse("spreadsheets:confirm"), {"token": token}, follow=True)
    assert (
        "1 members imported, 1 with a current membership. 1 rows skipped." in done.content.decode()
    )
    assert Member.objects.for_gym(alpha.gym).count() == 1

    again = client.post(reverse("spreadsheets:confirm"), {"token": token}, follow=True)
    assert "already done or has expired" in again.content.decode()
    assert Member.objects.count() == 1  # pressing Confirm twice imports once


def test_an_unusable_file_is_explained_on_the_page(alpha, sign_in):
    client = sign_in(alpha)

    response = upload(client, b"%PDF-1.4", name="members.pdf")

    assert "Upload an Excel (.xlsx) or CSV file" in response.content.decode()


def test_cancel_forgets_the_preview(alpha, sign_in):
    client = sign_in(alpha)
    token = upload(client, xlsx([["Eric", "0788111222", "", "", "", "", ""]])).context["token"]

    client.post(reverse("spreadsheets:cancel"))
    client.post(reverse("spreadsheets:confirm"), {"token": token})

    assert not Member.objects.exists()


def test_a_preview_cannot_be_confirmed_in_another_gym(alpha, make_gym, sign_in):
    """Someone who works at two gyms previews in one, switches, then confirms."""
    bravo = make_gym("Bravo Fitness", "0788000002")
    gym_services.assign_staff(gym=bravo.gym, person=alpha.person, role=Role.OWNER)
    client = sign_in(alpha)
    client.post(reverse("gyms:choose"), {"gym": str(alpha.gym.public_id)})
    token = upload(client, xlsx([["Eric", "0788111222", "", "", "", "", ""]])).context["token"]

    client.post(reverse("gyms:choose"), {"gym": str(bravo.gym.public_id)})
    client.post(reverse("spreadsheets:confirm"), {"token": token})

    assert not Member.objects.exists()


def test_the_template_downloads_and_can_be_read_back(alpha, sign_in):
    response = sign_in(alpha).get(reverse("spreadsheets:template"))

    assert response["Content-Disposition"].startswith("attachment;")
    rows = read_rows("t.xlsx", response.content)
    assert rows[0]["full_name"].startswith("Example Person")
    assert rows[0]["package"] == "Monthly"


@pytest.mark.parametrize("role", [Role.RECEPTIONIST, Role.SALES_REP, Role.TRAINER])
def test_other_roles_have_no_import_or_export_screens(alpha, add_staff, sign_in, role):
    client = sign_in(add_staff(alpha.gym, "0788000005", role))

    assert client.get(reverse("spreadsheets:home")).status_code == 403
    assert client.post(reverse("spreadsheets:export_members")).status_code == 403
    assert reverse("spreadsheets:home") not in client.get(reverse("gyms:home")).content.decode()


# --- Exporting --------------------------------------------------------------


def read_export(content: bytes) -> list[list]:
    sheet = load_workbook(io.BytesIO(content)).active
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def test_exporting_members_with_their_membership_and_balance(alpha, make_gym):
    eric = members.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric Mugisha")
    )
    billing.sell_membership(
        by=alpha.assignment,
        member=eric,
        package=members.find_package_by_name(alpha.gym, "Monthly"),
        start_date=today(),
        idempotency_key=uuid.uuid4(),
        payment=PaymentInput(method="cash", amount_rwf=10000),
    )
    members.register_member(
        by=alpha.assignment, phone="0788333444", details=MemberDetails(full_name="No Membership")
    )
    bravo = make_gym("Bravo Fitness", "0788000002")
    members.register_member(
        by=bravo.assignment, phone="0788555666", details=MemberDetails(full_name="Bravo Member")
    )

    rows = read_export(exporting.export_members(by=alpha.assignment))

    assert rows[0][:2] == ["Full name", "Phone"] and rows[0][-1] == "Owes (RWF)"
    by_name = {row[0]: row for row in rows[1:]}
    assert set(by_name) == {"Eric Mugisha", "No Membership"}  # nothing from Bravo
    assert by_name["Eric Mugisha"][1] == "+250788111222"
    assert by_name["Eric Mugisha"][8] == "Monthly" and by_name["Eric Mugisha"][-1] == 20000
    assert by_name["No Membership"][8] is None and by_name["No Membership"][-1] == 0
    assert AuditEntry.objects.for_gym(alpha.gym).get(action="export.members").details["rows"] == 2


def test_an_exported_name_that_looks_like_a_formula_stays_text(alpha):
    members.register_member(
        by=alpha.assignment,
        phone="0788111222",
        details=MemberDetails(full_name='=HYPERLINK("http://bad.example","Click")'),
    )

    content = exporting.export_members(by=alpha.assignment)

    cell = load_workbook(io.BytesIO(content)).active["A2"]
    assert cell.data_type == "s"
    assert cell.value.startswith("=HYPERLINK")


def test_exporting_payments_and_visits_for_a_period(alpha):
    eric = members.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric Mugisha")
    )
    sale = billing.sell_membership(
        by=alpha.assignment,
        member=eric,
        package=members.find_package_by_name(alpha.gym, "Monthly"),
        start_date=today(),
        idempotency_key=uuid.uuid4(),
        payment=PaymentInput(method="mtn_momo", reference="TX1"),
    )
    billing.reverse_payment(by=alpha.assignment, payment=sale.payment, reason="Entered twice")
    attendance.check_in(by=alpha.assignment, member=eric)

    payments = read_export(
        exporting.export_payments(by=alpha.assignment, start=today(), end=today())
    )
    visits = read_export(exporting.export_visits(by=alpha.assignment, start=today(), end=today()))
    earlier = read_export(
        exporting.export_payments(
            by=alpha.assignment, start=today() - timedelta(days=10), end=today() - timedelta(days=1)
        )
    )

    assert payments[1][:3] == ["RCT-000001", payments[1][1], "Eric Mugisha"]
    assert payments[1][4:9] == ["MTN Mobile Money", "TX1", 30000, "Yes", "Entered twice"]
    assert visits[1][2:5] == ["Eric Mugisha", "+250788111222", "Main branch"]
    assert len(earlier) == 1  # headings only


def test_exports_need_their_application(make_gym):
    staff = make_gym("Basic Gym", "0788000009")

    with pytest.raises(PermissionDenied):
        exporting.export_payments(by=staff.assignment, start=today(), end=today())
    with pytest.raises(PermissionDenied):
        exporting.export_visits(by=staff.assignment, start=today(), end=today())


def test_export_screens(alpha, sign_in):
    client = sign_in(alpha)

    page = client.get(reverse("spreadsheets:home")).content.decode()
    assert "Download payments" in page and "Download visits" in page

    members_file = client.post(reverse("spreadsheets:export_members"))
    assert members_file["Content-Disposition"].startswith('attachment; filename="members-')
    assert client.get(reverse("spreadsheets:export_members")).status_code == 405  # POST only

    bad_range = client.post(
        reverse("spreadsheets:export_payments"),
        {"start": today().isoformat(), "end": (today() - timedelta(days=1)).isoformat()},
    )
    assert bad_range.status_code == 400
    assert "The start date is after the end date." in bad_range.content.decode()

    good = client.post(
        reverse("spreadsheets:export_visits"),
        {"start": today().isoformat(), "end": today().isoformat()},
    )
    assert good["Content-Disposition"].startswith('attachment; filename="visits-')
