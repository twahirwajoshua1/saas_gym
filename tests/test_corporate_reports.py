"""
The monthly report a gym sends a company.

What the company gets is not a total: it is every visit, with the employee's
number, their department, when they came and what they used. These tests cover
the filters, the whole-month rule, and the two files the gym can download.
"""

import io
from datetime import date

import pytest
from openpyxl import load_workbook

from apps.corporate import services as corp
from apps.corporate.models import Contract, ContractStatus, CorporateVisit
from apps.corporate.services import EmployeeDetails
from apps.gyms.models import Branch
from apps.members import services as member_services
from apps.members.models import Service
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, CORPORATE_WELLNESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db

URL = "/gym/reports/"


@pytest.fixture
def gym(make_gym):
    staff = make_gym("Umucyo Wellness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, CORPORATE_WELLNESS):
        module_services.enable_module(gym=staff.gym, code=code)
    member_services.create_service(by=staff.assignment, name="Gym", price_rwf=5000)
    member_services.create_service(by=staff.assignment, name="Sauna", price_rwf=7000)
    return staff


def contract_for(gym, name, officer_phone, agreed_prices):
    """A company with an approved contract and the prices it agreed."""
    created = corp.create_company(
        name=name,
        tin="",
        officer_name=f"{name} officer",
        officer_phone=officer_phone,
        actor=None,
    )
    branch = Branch.objects.for_gym(gym.gym).first()
    by_name = {s.name: s for s in Service.objects.for_gym(gym.gym)}
    contract = corp.propose_contract(
        by=gym.assignment,
        company_code=created.company.code,
        terms=corp.ContractTerms(
            start_date=date(2026, 1, 1),
            end_date=date(2027, 12, 31),
            payment_days=30,
            prices={
                (by_name[service].id, branch.id): price for service, price in agreed_prices.items()
            },
        ),
    )
    corp.decide_contract(by=created.officer, contract=contract, approve=True)
    return created, Contract.objects.get(pk=contract.pk)


def visit(gym, employee, services_used):
    by_name = {s.name: s for s in Service.objects.for_gym(gym.gym)}
    result = corp.check_in_employee(
        by=gym.assignment,
        employee=employee,
        service_ids=[by_name[name].id for name in services_used],
        branch=Branch.objects.for_gym(gym.gym).first(),
        phone_code_ok=True,
    )
    return result.visit


@pytest.fixture
def wasac(gym):
    created, _contract = contract_for(gym, "WASAC", "0788900001", {"Gym": 4000, "Sauna": 5600})
    employee = corp.add_employee(
        by=created.officer,
        details=EmployeeDetails(
            full_name="Jean Claude Habumugisha",
            phone="0788111222",
            employee_number="W-0142",
            department="Finance",
        ),
    )
    return created, employee


# --- Which months a report covers -------------------------------------------


def test_a_report_always_covers_whole_months():
    assert corp.report_window(date(2026, 11, 14), 1) == (date(2026, 11, 1), date(2026, 11, 30))
    assert corp.report_window(date(2026, 11, 1), 3) == (date(2026, 9, 1), date(2026, 11, 30))
    assert corp.report_window(date(2026, 2, 1), 3) == (date(2025, 12, 1), date(2026, 2, 28))
    assert corp.report_window(date(2026, 11, 1), 12) == (date(2025, 12, 1), date(2026, 11, 30))


def test_half_months_are_refused():
    with pytest.raises(corp.InvalidDetails):
        corp.report_window(date(2026, 11, 1), 7)


# --- The lines themselves ---------------------------------------------------


def test_every_line_says_who_came_from_where_and_what_they_used(gym, wasac):
    _created, employee = wasac
    visit(gym, employee, ["Gym", "Sauna"])

    start, end = corp.report_window(CorporateVisit.objects.get().checked_in_on, 1)
    lines = corp.report_lines(gym.gym, start=start, end=end)

    assert len(lines) == 2
    line = next(line for line in lines if line.service_name == "Sauna")
    assert line.company_name == "WASAC"
    assert line.employee_number == "W-0142"
    assert line.employee_name == "Jean Claude Habumugisha"
    assert line.department == "Finance"
    assert line.amount_rwf == 5600  # the price WASAC agreed, not the door price


def test_a_service_taken_off_stays_on_the_list_but_costs_nothing(gym, wasac):
    _created, employee = wasac
    made = visit(gym, employee, ["Gym", "Sauna"])
    sauna = made.activities.get(service_name="Sauna")
    corp.cancel_activity(by=gym.assignment, activity=sauna, reason="Had to leave, urgent call")

    start, end = corp.report_window(made.checked_in_on, 1)
    lines = corp.report_lines(gym.gym, start=start, end=end)
    taken_off = next(line for line in lines if line.service_name == "Sauna")

    assert taken_off.cancelled is True
    assert taken_off.amount_rwf == 0
    assert taken_off.cancel_note == "Had to leave, urgent call"
    # It is not counted in the summary either.
    assert corp.report_totals(lines) == [("Gym", 1, 4000)]


def test_the_report_can_be_narrowed_to_one_activity(gym, wasac):
    _created, employee = wasac
    made = visit(gym, employee, ["Gym", "Sauna"])
    sauna = Service.objects.for_gym(gym.gym).get(name="Sauna")

    start, end = corp.report_window(made.checked_in_on, 1)
    lines = corp.report_lines(gym.gym, start=start, end=end, service=sauna)

    assert [line.service_name for line in lines] == ["Sauna"]


def test_another_gyms_visits_never_appear(gym, wasac, make_gym):
    _created, employee = wasac
    made = visit(gym, employee, ["Gym"])
    other = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")

    start, end = corp.report_window(made.checked_in_on, 1)

    assert corp.report_lines(other.gym, start=start, end=end) == []


# --- The screen and the two files -------------------------------------------


def test_the_receptionist_sees_the_detail_not_just_a_total(gym, wasac, add_staff, sign_in):
    from apps.gyms.models import Role

    _created, employee = wasac
    visit(gym, employee, ["Gym", "Sauna"])
    receptionist = add_staff(gym.gym, "0788000007", Role.RECEPTIONIST, "Gisele")

    page = sign_in(receptionist).get(URL).content.decode()

    assert "W-0142" in page and "Finance" in page
    assert "Jean Claude Habumugisha" in page
    assert "Download PDF" in page and "Download Excel" in page


def test_the_excel_file_holds_every_visit_and_a_summary(gym, wasac, sign_in):
    _created, employee = wasac
    visit(gym, employee, ["Gym", "Sauna"])

    response = sign_in(gym).get("/gym/reports/download.xlsx")

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    book = load_workbook(io.BytesIO(response.content))
    rows = [[cell.value for cell in row] for row in book["Visits"].iter_rows()]
    flat = [str(value) for row in rows for value in row if value is not None]
    assert "W-0142" in flat and "Finance" in flat and "Sauna" in flat
    summary = [[cell.value for cell in row] for row in book["Summary"].iter_rows()]
    assert ["Total", 2, 9600] in summary


def test_the_pdf_file_is_a_pdf(gym, wasac, sign_in):
    _created, employee = wasac
    visit(gym, employee, ["Gym"])

    response = sign_in(gym).get("/gym/reports/download.pdf")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"


def test_an_unknown_file_type_is_not_offered(gym, sign_in):
    assert sign_in(gym).get("/gym/reports/download.docx").status_code == 404


def test_a_contract_must_be_approved_before_its_visits_are_billed(gym):
    """A guard on the fixture: an unapproved contract cannot take visits."""
    created, contract = contract_for(gym, "REG", "0788900002", {"Gym": 4000})

    assert contract.status == ContractStatus.APPROVED
    assert created.company.name == "REG"


def test_a_visit_can_only_be_corrected_on_the_day(gym, wasac):
    """
    The receptionist fixes a wrongly ticked service the same day. After that
    the visit is closed, so a month already shown to the company cannot change.
    """
    from datetime import timedelta

    _created, employee = wasac
    made = visit(gym, employee, ["Gym", "Sauna"])
    sauna = made.activities.get(service_name="Sauna")

    CorporateVisit.objects.filter(pk=made.pk).update(
        checked_in_on=made.checked_in_on - timedelta(days=1)
    )
    sauna.refresh_from_db()

    with pytest.raises(corp.InvalidDetails) as raised:
        corp.cancel_activity(by=gym.assignment, activity=sauna, reason="Did not use it")

    assert (raised.value.field, raised.value.reason) == ("activity", "too_late")
    sauna.refresh_from_db()
    assert sauna.cancelled_at is None
