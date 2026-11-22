"""Tests for corporate wellness: companies, employees, contracts, the desk, warnings and bills."""

import uuid
from datetime import date, timedelta

import pytest
import time_machine
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, ProgrammingError, connection, transaction
from django.utils import timezone

from apps.core import tenant_db
from apps.corporate import services as corp
from apps.corporate.models import (
    BillLine,
    CompanyOfficer,
    Contract,
    CorporateBill,
    CorporateVisit,
    Employee,
    VisitActivity,
)
from apps.corporate.services import ContractTerms, EmployeeDetails
from apps.gyms import services as gym_services
from apps.gyms.models import Branch, Role
from apps.members import services as member_services
from apps.members.services import InvalidDetails
from apps.modules import services as module_services
from apps.modules.catalog import (
    ATTENDANCE_ACCESS,
    CORPORATE_WELLNESS,
    PAYMENTS_FINANCE,
)

pytestmark = pytest.mark.django_db

KIGALI = timezone.get_fixed_timezone(120)


def switch_on(staff):
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, CORPORATE_WELLNESS):
        module_services.enable_module(gym=staff.gym, code=code)


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    switch_on(staff)
    return staff


@pytest.fixture
def bravo(make_gym):
    staff = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")
    switch_on(staff)
    return staff


def add_services(staff):
    gym = member_services.create_service(by=staff.assignment, name="Gym", price_rwf=3000)
    sauna = member_services.create_service(by=staff.assignment, name="Sauna", price_rwf=5000)
    return gym, sauna


@pytest.fixture
def company():
    return corp.create_company(
        name="Bank of Kigali Test",
        tin="100000001",
        officer_name="Diane Mukamana",
        officer_phone="0788900001",
        actor=None,
    )


@pytest.fixture
def officer(company):
    return company.officer


@pytest.fixture
def employee(officer):
    return corp.add_employee(
        by=officer,
        details=EmployeeDetails(
            full_name="Eric Mugisha",
            phone="0788111222",
            employee_number="BK-001",
            department="Finance",
        ),
    )


def make_contract(staff, officer, prices=None, start=None, end=None, approve=True):
    gym_service, sauna = add_services(staff)
    branch = Branch.objects.for_gym(staff.gym).first()
    today = timezone.localdate()
    terms = ContractTerms(
        start_date=start or today.replace(day=1),
        end_date=end or today + timedelta(days=365),
        payment_days=30,
        prices=prices or {(gym_service.id, branch.id): 2500, (sauna.id, branch.id): 4000},
    )
    contract = corp.propose_contract(
        by=staff.assignment, company_code=officer.company.code, terms=terms
    )
    if approve:
        corp.decide_contract(by=officer, contract=contract, approve=True)
    return contract, gym_service, sauna


def scan(staff, employee, fresh=True):
    link = f"https://x.rw/e/{employee.card_token}/"
    if fresh:
        link += f"?c={corp.card_code(employee)}"
    return corp.find_for_desk(staff.gym, link)


# --- Companies and employees ------------------------------------------------------


def test_a_company_gets_a_code_and_its_officer_a_temporary_login(company):
    assert company.company.code.startswith("CO-")
    assert company.temporary_password
    assert company.officer.person.user_account.must_change_password


def test_employees_are_added_numbered_uniquely_and_removed(officer, employee):
    assert employee.card_token and employee.is_active
    with pytest.raises(InvalidDetails) as error:
        corp.add_employee(
            by=officer,
            details=EmployeeDetails(
                full_name="Other", phone="0788111333", employee_number="bk-001"
            ),
        )
    assert error.value.field == "employee_number"
    corp.remove_employee(by=officer, employee=employee)
    employee.refresh_from_db()
    assert not employee.is_active and employee.removed_at


def test_an_officer_cannot_touch_another_companys_employee(employee):
    other = corp.create_company(
        name="Other Co", tin="", officer_name="Olivier", officer_phone="0788900009", actor=None
    )
    with pytest.raises(PermissionDenied):
        corp.remove_employee(by=other.officer, employee=employee)


def test_importing_the_excel_list_shows_new_changed_missing_and_problems(officer, employee):
    corp.add_employee(
        by=officer,
        details=EmployeeDetails(full_name="Leaver", phone="0788111444", employee_number="BK-009"),
    )
    rows = corp.read_employee_rows(
        [
            ["Name", "Phone", "Employee number", "Department"],
            ["Eric Mugisha", "0788111222", "BK-001", "Audit"],  # department changed
            ["Grace Uwera", "0788 111 555", "BK-002", "IT"],  # new
            ["No Phone", "12", "BK-003", ""],  # problem
        ]
    )
    plan = corp.plan_import(officer, rows)
    assert [r.details.full_name for r in plan.new] == ["Grace Uwera"]
    assert plan.changed[0].changes == ["department"]
    assert [r.problem for r in plan.problems] == ["phone"]
    assert [e.full_name for e in plan.missing] == ["Leaver"]

    result = corp.apply_import(by=officer, plan=plan, remove_missing=True)
    assert (result.added, result.updated, result.removed) == (1, 1, 1)
    employee.refresh_from_db()
    assert employee.department == "Audit"


def test_a_file_without_the_needed_headings_is_refused():
    with pytest.raises(InvalidDetails):
        corp.read_employee_rows([["Something", "Else"], ["a", "b"]])


# --- Contracts ---------------------------------------------------------------------


def test_a_gym_proposes_a_contract_with_the_code_and_the_company_approves(alpha, officer):
    contract, *_ = make_contract(alpha, officer, approve=False)
    assert contract.status == "proposed"
    assert corp.running_contract(alpha.gym, officer.company_id, timezone.localdate()) is None
    corp.decide_contract(by=officer, contract=contract, approve=True)
    assert corp.running_contract(alpha.gym, officer.company_id, timezone.localdate()) == contract


def test_a_wrong_code_and_other_gyms_services_are_refused(alpha, bravo, officer):
    with pytest.raises(InvalidDetails):
        corp.propose_contract(
            by=alpha.assignment,
            company_code="CO-NOPE00",
            terms=ContractTerms(date.today(), date.today(), 30, {(1, 1): 100}),
        )
    bravo_service = member_services.create_service(by=bravo.assignment, name="Pool", price_rwf=1)
    branch = Branch.objects.for_gym(alpha.gym).first()
    with pytest.raises(PermissionDenied):
        corp.propose_contract(
            by=alpha.assignment,
            company_code=officer.company.code,
            terms=ContractTerms(
                date.today(), date.today(), 30, {(bravo_service.id, branch.id): 100}
            ),
        )


def test_only_managers_propose_contracts(alpha, officer, add_staff):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    with pytest.raises(PermissionDenied):
        corp.propose_contract(
            by=desk.assignment,
            company_code=officer.company.code,
            terms=ContractTerms(date.today(), date.today(), 30, {}),
        )


# --- The desk ---------------------------------------------------------------------


def test_a_fresh_card_from_the_phone_checks_the_employee_in(alpha, officer, employee):
    contract, gym_service, sauna = make_contract(alpha, officer)
    [found] = scan(alpha, employee)
    assert found.allowed and found.phone_code_ok
    result = corp.check_in_employee(
        by=alpha.assignment,
        employee=employee,
        service_ids=[gym_service.id, sauna.id],
        phone_code_ok=True,
    )
    assert sorted(a.price_rwf for a in result.added) == [2500, 4000]  # contract prices
    # Scanning the card from the phone is the employee's signature.
    assert result.visit.confirmed_at is not None


def test_an_old_screenshot_or_printed_card_needs_the_pin(alpha, officer, employee):
    _, gym_service, _ = make_contract(alpha, officer)
    [found] = scan(alpha, employee, fresh=False)
    assert not found.phone_code_ok
    with pytest.raises(corp.NeedsProof):
        corp.check_in_employee(by=alpha.assignment, employee=employee, service_ids=[gym_service.id])
    corp.set_pin(by=officer, employee=employee, pin="4821")
    employee.refresh_from_db()
    with pytest.raises(InvalidDetails):
        corp.check_in_employee(
            by=alpha.assignment, employee=employee, service_ids=[gym_service.id], pin="0000"
        )
    result = corp.check_in_employee(
        by=alpha.assignment, employee=employee, service_ids=[gym_service.id], pin="4821"
    )
    assert result.visit.method == "pin" and result.visit.confirmed_at is not None


def test_codes_expire_after_about_two_minutes(employee):
    code = corp.card_code(employee)
    assert corp.code_is_fresh(employee, code)
    with time_machine.travel(timezone.now() + timedelta(minutes=3)):
        assert not corp.code_is_fresh(employee, code)


def test_too_many_wrong_pins_lock_the_pin(alpha, officer, employee):
    _, gym_service, _ = make_contract(alpha, officer)
    corp.set_pin(by=officer, employee=employee, pin="4821")
    employee.refresh_from_db()
    for _ in range(corp.PIN_MAX_FAILURES):
        with pytest.raises(InvalidDetails):
            corp.check_in_employee(
                by=alpha.assignment, employee=employee, service_ids=[gym_service.id], pin="1111"
            )
    with pytest.raises(corp.PinLocked):
        corp.check_in_employee(
            by=alpha.assignment, employee=employee, service_ids=[gym_service.id], pin="4821"
        )


def test_removed_employees_uncovered_services_and_no_contract_are_refused(alpha, officer, employee):
    with pytest.raises(InvalidDetails) as error:
        corp.check_in_employee(by=alpha.assignment, employee=employee, service_ids=[1])
    assert error.value.reason == "no_contract"

    branch = Branch.objects.for_gym(alpha.gym).first()
    gym_service, sauna = add_services(alpha)
    contract = corp.propose_contract(
        by=alpha.assignment,
        company_code=officer.company.code,
        terms=ContractTerms(
            timezone.localdate(), timezone.localdate(), 30, {(gym_service.id, branch.id): 2000}
        ),
    )
    corp.decide_contract(by=officer, contract=contract, approve=True)
    with pytest.raises(InvalidDetails) as error:
        corp.check_in_employee(
            by=alpha.assignment, employee=employee, service_ids=[sauna.id], phone_code_ok=True
        )
    assert error.value.reason == "not_covered"

    corp.remove_employee(by=officer, employee=employee)
    employee.refresh_from_db()
    with pytest.raises(InvalidDetails) as error:
        corp.check_in_employee(
            by=alpha.assignment, employee=employee, service_ids=[gym_service.id], phone_code_ok=True
        )
    assert error.value.reason == "removed"


def test_adding_a_service_later_the_same_day_adds_to_the_same_visit(alpha, officer, employee):
    _, gym_service, sauna = make_contract(alpha, officer)
    first = corp.check_in_employee(
        by=alpha.assignment, employee=employee, service_ids=[gym_service.id], phone_code_ok=True
    )
    second = corp.check_in_employee(
        by=alpha.assignment, employee=employee, service_ids=[sauna.id, gym_service.id]
    )
    assert second.visit == first.visit and [a.service_name for a in second.added] == ["Sauna"]
    second.visit.refresh_from_db()
    assert second.visit.confirmed_at is not None
    assert CorporateVisit.objects.count() == 1


def test_checking_in_at_two_gyms_within_an_hour_raises_a_warning(alpha, bravo, officer, employee):
    _, alpha_gym, _ = make_contract(alpha, officer)
    _, bravo_gym, _ = make_contract(bravo, officer)
    corp.check_in_employee(
        by=alpha.assignment, employee=employee, service_ids=[alpha_gym.id], phone_code_ok=True
    )
    result = corp.check_in_employee(
        by=bravo.assignment, employee=employee, service_ids=[bravo_gym.id], phone_code_ok=True
    )
    assert result.warned_two_places
    assert list(corp.open_flags_for_company(officer.company))[0].gym == bravo.gym


def test_desk_staff_cancel_a_mistaken_service_with_a_reason(alpha, officer, employee):
    _, gym_service, _ = make_contract(alpha, officer)
    result = corp.check_in_employee(
        by=alpha.assignment, employee=employee, service_ids=[gym_service.id], phone_code_ok=True
    )
    activity = result.added[0]
    with pytest.raises(InvalidDetails):
        corp.cancel_activity(by=alpha.assignment, activity=activity, reason=" ")
    corp.cancel_activity(by=alpha.assignment, activity=activity, reason="Wrong person")
    activity.refresh_from_db()
    assert activity.cancelled_at and activity.cancel_reason == "Wrong person"


# --- Warnings and bills ------------------------------------------------------------


def in_month(day):
    return time_machine.travel(timezone.datetime(2026, 10, day, 9, 0, tzinfo=KIGALI), tick=False)


def test_the_monthly_bill_lists_each_service_and_holds_back_questioned_visits(
    alpha, officer, employee
):
    with in_month(1):
        contract, gym_service, sauna = make_contract(
            alpha, officer, start=date(2026, 10, 1), end=date(2027, 9, 30)
        )
        other = corp.add_employee(
            by=officer,
            details=EmployeeDetails(
                full_name="Grace Uwera", phone="0788111555", employee_number="2"
            ),
        )
    with in_month(5):
        corp.check_in_employee(
            by=alpha.assignment,
            employee=employee,
            service_ids=[gym_service.id, sauna.id],
            phone_code_ok=True,
        )
        questioned = corp.check_in_employee(
            by=alpha.assignment, employee=other, service_ids=[gym_service.id], phone_code_ok=True
        ).visit
        flag = corp.question_visit(by=officer, visit=questioned, message="Grace was on leave")

    with in_month(20):
        with pytest.raises(InvalidDetails):  # the month is not over yet
            corp.issue_bill(contract=contract, month=date(2026, 10, 1), by=alpha.assignment)

    with in_month(31):
        [bill] = corp.issue_due_bills()
    assert bill.total_rwf == 6500 and bill.due_on == date(2026, 11, 30)
    groups = {name: total for name, _, total in corp.lines_by_service(bill)}
    assert groups == {"Gym": 2500, "Sauna": 4000}
    line = bill.lines.first()
    assert line.employee_number == "BK-001" and line.department == "Finance"
    assert line.check_in_time.hour == 9

    # The company accepts the questioned visit: it goes on the next bill.
    with time_machine.travel(timezone.datetime(2026, 11, 3, 10, tzinfo=KIGALI), tick=False):
        corp.settle_flag(by=officer, flag=flag)
    with time_machine.travel(timezone.datetime(2026, 11, 30, 21, tzinfo=KIGALI), tick=False):
        [november] = corp.issue_due_bills()
    assert november.total_rwf == 2500
    assert november.lines.get().visit_date == date(2026, 10, 5)


def test_the_gym_settles_a_warning_by_taking_the_visit_off(alpha, officer, employee):
    _, gym_service, _ = make_contract(alpha, officer)
    visit = corp.check_in_employee(
        by=alpha.assignment, employee=employee, service_ids=[gym_service.id], phone_code_ok=True
    ).visit
    flag = corp.question_visit(by=officer, visit=visit, message="Not him")
    with pytest.raises(InvalidDetails):
        corp.settle_flag(by=alpha.assignment, flag=flag)  # needs a note
    corp.settle_flag(by=alpha.assignment, flag=flag, note="Agreed, desk mistake")
    flag.refresh_from_db()
    assert flag.outcome == "removed"
    assert not VisitActivity.objects.filter(visit=visit, cancelled_at__isnull=True).exists()


def make_bill(alpha, officer, employee):
    with in_month(1):
        contract, gym_service, sauna = make_contract(
            alpha, officer, start=date(2026, 10, 1), end=date(2027, 9, 30)
        )
    with in_month(6):
        corp.check_in_employee(
            by=alpha.assignment,
            employee=employee,
            service_ids=[gym_service.id, sauna.id],
            phone_code_ok=True,
        )
    with in_month(31):
        return corp.issue_bill(contract=contract, month=date(2026, 10, 1), by=alpha.assignment)


def test_payments_are_recorded_reversed_and_never_more_than_owed(alpha, officer, employee):
    bill = make_bill(alpha, officer, employee)
    with pytest.raises(InvalidDetails):
        corp.record_bill_payment(
            by=alpha.assignment,
            bill=bill,
            amount_rwf=7000,
            reference="TRF1",
            received_on=date(2026, 10, 31),
            idempotency_key=uuid.uuid4(),
        )
    key = uuid.uuid4()
    for _ in range(2):  # the same form twice records once
        payment = corp.record_bill_payment(
            by=alpha.assignment,
            bill=bill,
            amount_rwf=4000,
            reference="TRF1",
            received_on=date(2026, 10, 31),
            idempotency_key=key,
        )
    state = corp.bill_state(bill, today=date(2026, 12, 15))
    assert (state.paid_rwf, state.owed_rwf, state.status) == (4000, 2500, "overdue")
    corp.reverse_bill_payment(by=alpha.assignment, payment=payment, reason="Wrong bill")
    assert corp.bill_state(bill).paid_rwf == 0


def test_a_credit_appears_as_a_minus_line_on_the_next_bill(alpha, officer, employee):
    bill = make_bill(alpha, officer, employee)
    sauna_line = bill.lines.get(service_name="Sauna")
    with time_machine.travel(timezone.datetime(2026, 11, 2, 9, tzinfo=KIGALI), tick=False):
        corp.credit_line(by=alpha.assignment, line=sauna_line, reason="Sauna was broken")
        with pytest.raises(InvalidDetails):
            corp.credit_line(by=alpha.assignment, line=sauna_line, reason="Again")
    with time_machine.travel(timezone.datetime(2026, 11, 30, 22, tzinfo=KIGALI), tick=False):
        [november] = corp.issue_due_bills()
    assert november.total_rwf == -4000
    assert november.lines.get().service_name == "Credit: Sauna"


def test_bills_and_their_lines_are_permanent(alpha, officer, employee):
    bill = make_bill(alpha, officer, employee)
    with pytest.raises(DatabaseError), transaction.atomic():
        CorporateBill.objects.filter(pk=bill.pk).update(total_rwf=1)
    with pytest.raises(DatabaseError), transaction.atomic():
        BillLine.objects.filter(bill=bill).delete()


# --- Who sees what (the database's own rules) -----------------------------------------


@pytest.fixture
def two_gyms_one_visit_each(alpha, bravo, officer, employee):
    _, alpha_gym, _ = make_contract(alpha, officer)
    _, bravo_gym, _ = make_contract(bravo, officer)
    corp.check_in_employee(
        by=alpha.assignment, employee=employee, service_ids=[alpha_gym.id], phone_code_ok=True
    )
    with time_machine.travel(timezone.now() + timedelta(hours=3)):
        corp.check_in_employee(
            by=bravo.assignment, employee=employee, service_ids=[bravo_gym.id], phone_code_ok=True
        )


def as_app(person_id=None, gym=None, company=None):
    tenant_db.enter_app_role(person_id=person_id)
    if gym:
        tenant_db.set_current_gym(gym.id)
    if company:
        tenant_db.set_current_company(company.id)


def test_the_company_sees_its_visits_at_every_gym_and_a_gym_only_its_own(
    alpha, bravo, officer, two_gyms_one_visit_each
):
    try:
        as_app(company=officer.company)
        assert CorporateVisit.objects.count() == 2
        assert Employee.objects.count() == 1
        tenant_db.leave_app_role()

        as_app(gym=alpha.gym)
        assert set(CorporateVisit.objects.values_list("gym_id", flat=True)) == {alpha.gym.id}
        assert Employee.objects.count() == 1  # approved contract with the company
    finally:
        tenant_db.leave_app_role()


def test_another_company_and_a_gym_without_a_contract_see_nothing(
    alpha, officer, two_gyms_one_visit_each, make_gym
):
    stranger = corp.create_company(
        name="Stranger", tin="", officer_name="S", officer_phone="0788900008", actor=None
    )
    charlie = make_gym("Charlie Gym", "0788000003")
    try:
        as_app(company=stranger.company)
        assert CorporateVisit.objects.count() == 0 and Employee.objects.count() == 0
        tenant_db.leave_app_role()
        as_app(gym=charlie.gym)
        assert Employee.objects.count() == 0 and Contract.objects.count() == 0
    finally:
        tenant_db.leave_app_role()


def test_a_company_cannot_record_visits_and_a_gym_cannot_edit_employees(
    alpha, officer, employee, two_gyms_one_visit_each
):
    visit = (
        CorporateVisit.objects.select_related("contract", "branch").filter(gym=alpha.gym).first()
    )
    try:
        as_app(company=officer.company)
        with pytest.raises(ProgrammingError), transaction.atomic():
            CorporateVisit.objects.create(
                gym=alpha.gym,
                company=officer.company,
                contract=visit.contract,
                employee=employee,
                branch=visit.branch,
                checked_in_on=date(2020, 1, 1),
                method="phone",
            )
        tenant_db.leave_app_role()
        as_app(gym=alpha.gym)
        assert Employee.objects.filter(pk=employee.pk).update(department="Hacked") == 0
    finally:
        tenant_db.leave_app_role()


def test_a_person_sees_the_companies_they_manage(officer):
    try:
        as_app(person_id=officer.person_id)
        assert list(corp.officer_roles_for(officer.person)) == [
            CompanyOfficer.objects.get(pk=officer.pk)
        ]
    finally:
        tenant_db.leave_app_role()


def test_the_contract_code_lookup_works_under_the_restricted_role(alpha, officer):
    try:
        as_app(gym=alpha.gym)
        assert corp.company_for_code(officer.company.code.lower())[1] == "Bank of Kigali Test"
        assert corp.company_for_code("CO-XXXXXX") is None
    finally:
        tenant_db.leave_app_role()


def test_every_corporate_table_has_row_level_security():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname FROM pg_class WHERE relname LIKE 'corporate_%%' AND relkind = 'r'"
            " AND NOT relrowsecurity"
        )
        assert cursor.fetchall() == []


def test_branch_rule_for_multi_branch_gyms(alpha, officer, employee):
    gym_services.add_branch(gym=alpha.gym, name="Remera")
    contract, gym_service, _ = make_contract(alpha, officer)
    from apps.attendance.services import BranchNeeded

    with pytest.raises(BranchNeeded):
        corp.check_in_employee(
            by=alpha.assignment, employee=employee, service_ids=[gym_service.id], phone_code_ok=True
        )
