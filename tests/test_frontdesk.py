"""
Tests for the front desk and the entrance tablet: connecting a tablet,
members and company staff scanning themselves in, problems for the desk,
shifts, and the company league. Tablet requests run as an anonymous visitor
with the restricted database role, as in real use.
"""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import CheckIn
from apps.billing import services as billing
from apps.billing.models import ShiftClose
from apps.billing.services import PaymentInput
from apps.corporate import services as corp
from apps.corporate.models import CorporateVisit
from apps.corporate.services import ContractTerms, EmployeeDetails
from apps.frontdesk import services
from apps.frontdesk.models import EntranceTablet, ScanProblem
from apps.frontdesk.views import COOKIE
from apps.gyms.models import Branch, Role
from apps.members import services as member_services
from apps.members.models import Membership
from apps.members.services import MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, CORPORATE_WELLNESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, CORPORATE_WELLNESS):
        module_services.enable_module(gym=staff.gym, code=code)
    return staff


@pytest.fixture
def branch(alpha):
    return Branch.objects.for_gym(alpha.gym).first()


@pytest.fixture
def monthly(alpha):
    return member_services.create_package(
        by=alpha.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )


def new_member(staff, package=None, name="Eric Mugisha", phone="0788111222"):
    member = member_services.register_member(
        by=staff.assignment, phone=phone, details=MemberDetails(full_name=name)
    )
    if package is not None:
        billing.sell_membership(
            by=staff.assignment,
            member=member,
            package=package,
            start_date=timezone.localdate(),
            idempotency_key=uuid.uuid4(),
            payment=PaymentInput(method="cash"),
        )
    member_services.create_card_link(by=staff.assignment, member=member)
    member.refresh_from_db()
    return member


@pytest.fixture
def tablet_client(alpha, branch):
    """A browser on a tablet that has been connected to Alpha's branch."""
    tablet = services.add_tablet(by=alpha.assignment, branch=branch, name="Front entrance")
    client = Client()
    response = client.post(reverse("frontdesk:kiosk_connect"), {"code": tablet.pairing_code})
    assert response.status_code == 302 and COOKIE in response.cookies
    return client


# --- Connecting -----------------------------------------------------------------------


def test_a_tablet_connects_once_with_its_code(alpha, branch, tablet_client):
    tablet = EntranceTablet.objects.get()
    assert tablet.is_connected and tablet.pairing_code == ""
    page = tablet_client.get(reverse("frontdesk:kiosk")).content.decode()
    assert "Scan your card" in page and "Alpha Fitness" in page
    # The code cannot be used a second time.
    other = Client()
    response = other.post(reverse("frontdesk:kiosk_connect"), {"code": "000000"})
    assert "not right" in response.content.decode()


def test_a_disconnected_tablet_shows_the_connect_screen(alpha, tablet_client):
    services.disconnect(by=alpha.assignment, tablet=EntranceTablet.objects.get())
    assert "Connect this tablet" in tablet_client.get(reverse("frontdesk:kiosk")).content.decode()
    response = tablet_client.post(reverse("frontdesk:kiosk_scan"), {"text": "x"})
    assert response.status_code == 403


def test_only_managers_add_tablets(alpha, branch, add_staff):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    with pytest.raises(PermissionDenied):
        services.add_tablet(by=desk.assignment, branch=branch, name="X")


# --- Members at the tablet -------------------------------------------------------------


def test_a_member_scans_and_walks_in(alpha, monthly, tablet_client):
    member = new_member(alpha, monthly)
    text = f"https://gym.example.rw/card/{member.card_token}/"
    response = tablet_client.post(reverse("frontdesk:kiosk_scan"), {"text": text})
    page = response.content.decode()
    assert "Murakaza neza" in page and "Eric" in page and "Your streak" in page
    visit = CheckIn.objects.get()
    assert visit.method == "tablet" and visit.admitted_by is None


def test_an_ended_membership_is_stopped_and_shows_on_the_desk(
    alpha, monthly, tablet_client, sign_in
):
    member = new_member(alpha, monthly)
    Membership.objects.filter(member=member).update(
        start_date=timezone.localdate() - timedelta(days=60),
        end_date=timezone.localdate() - timedelta(days=30),
    )
    token = _token(member)
    page = tablet_client.post(
        reverse("frontdesk:kiosk_scan"), {"text": f"/card/{token}/"}
    ).content.decode()
    assert "see reception" in page and not CheckIn.objects.exists()
    problem = ScanProblem.objects.get()
    assert problem.kind == "ended" and problem.member == member

    desk = sign_in(alpha).get(reverse("frontdesk:desk")).content.decode()
    assert "Needs you" in desk and "Eric Mugisha" in desk


def test_an_unknown_card_becomes_a_problem(alpha, tablet_client):
    tablet_client.post(reverse("frontdesk:kiosk_scan"), {"text": "/card/" + "z" * 43 + "/"})
    assert ScanProblem.objects.get().kind == "unknown"


def _token(member):
    return member.card_token


# --- Company staff at the tablet -------------------------------------------------------


@pytest.fixture
def company_setup(alpha, branch):
    gym_service = member_services.create_service(by=alpha.assignment, name="Gym", price_rwf=3000)
    sauna = member_services.create_service(by=alpha.assignment, name="Sauna", price_rwf=5000)
    created = corp.create_company(
        name="WASAC Test", tin="", officer_name="Aimée", officer_phone="0788900001", actor=None
    )
    contract = corp.propose_contract(
        by=alpha.assignment,
        company_code=created.company.code,
        terms=ContractTerms(
            timezone.localdate() - timedelta(days=1),
            timezone.localdate() + timedelta(days=300),
            30,
            {(gym_service.id, branch.id): 2500, (sauna.id, branch.id): 4000},
        ),
    )
    corp.decide_contract(by=created.officer, contract=contract, approve=True)
    employee = corp.add_employee(
        by=created.officer,
        details=EmployeeDetails(
            full_name="Eric Mugisha", phone="0788111999", employee_number="W-1"
        ),
    )
    return created, employee, gym_service, sauna


def test_company_staff_scan_choose_services_and_walk_in(alpha, company_setup, tablet_client):
    _, employee, gym_service, sauna = company_setup
    code = corp.card_code(employee)
    page = tablet_client.post(
        reverse("frontdesk:kiosk_scan"), {"text": f"https://x.rw/e/{employee.card_token}/?c={code}"}
    ).content.decode()
    assert "what are you using today" in page and "Sauna" in page
    page = tablet_client.post(
        reverse("frontdesk:kiosk_enter", args=[employee.public_id]),
        {"code": code, "service": [str(gym_service.public_id), str(sauna.public_id)]},
    ).content.decode()
    assert "Murakaza neza" in page and "WASAC Test" in page
    visit = CorporateVisit.objects.get()
    assert visit.method == "tablet" and visit.confirmed_at is not None
    assert visit.activities.count() == 2


def test_an_old_screenshot_is_refused_at_the_tablet(alpha, company_setup, tablet_client):
    _, employee, *_ = company_setup
    page = tablet_client.post(
        reverse("frontdesk:kiosk_scan"), {"text": f"/e/{employee.card_token}/?c=000000"}
    ).content.decode()
    assert "open your card on your phone" in page and not CorporateVisit.objects.exists()


def test_phone_number_and_pin_at_the_tablet(alpha, company_setup, tablet_client):
    created, employee, gym_service, _ = company_setup
    corp.set_pin(by=created.officer, employee=employee, pin="2468")
    wrong = tablet_client.post(
        reverse("frontdesk:kiosk_pin"), {"phone": "0788111999", "pin": "1111"}
    ).content.decode()
    assert "PIN is not right" in wrong
    page = tablet_client.post(
        reverse("frontdesk:kiosk_pin"), {"phone": "0788111999", "pin": "2468"}
    ).content.decode()
    assert "what are you using today" in page
    page = tablet_client.post(
        reverse("frontdesk:kiosk_enter", args=[employee.public_id]),
        {"pin": "2468", "service": [str(gym_service.public_id)]},
    ).content.decode()
    assert "Murakaza neza" in page
    assert CorporateVisit.objects.get().method == "tablet"


def test_a_removed_employee_is_stopped_and_shows_on_the_desk(alpha, company_setup, tablet_client):
    created, employee, *_ = company_setup
    corp.remove_employee(by=created.officer, employee=employee)
    page = tablet_client.post(
        reverse("frontdesk:kiosk_scan"),
        {"text": f"/e/{employee.card_token}/?c={corp.card_code(employee)}"},
    ).content.decode()
    assert "see reception" in page
    # Removed employees are still visible to the gym for its records.
    assert ScanProblem.objects.filter(kind__in=["removed", "unknown"]).exists()


# --- The desk and shifts --------------------------------------------------------------


def test_the_desk_lists_arrivals_and_problems_are_marked_done(
    alpha, monthly, tablet_client, sign_in
):
    member = new_member(alpha, monthly)
    tablet_client.post(reverse("frontdesk:kiosk_scan"), {"text": f"/card/{_token(member)}/"})
    tablet_client.post(reverse("frontdesk:kiosk_scan"), {"text": "/card/" + "q" * 43 + "/"})
    client = sign_in(alpha)
    page = client.get(reverse("frontdesk:desk")).content.decode()
    assert "Checked in today" in page and "Eric Mugisha" in page and "Tablet" in page
    problem = ScanProblem.objects.get()
    client.post(reverse("frontdesk:problem_done", args=[problem.public_id]))
    problem.refresh_from_db()
    assert problem.resolved_at is not None


def test_a_shift_adds_up_the_cash_and_closing_it_starts_again(alpha, monthly, add_staff, sign_in):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    member = new_member(alpha)
    billing.sell_membership(
        by=desk.assignment,
        member=member,
        package=monthly,
        start_date=timezone.localdate(),
        idempotency_key=uuid.uuid4(),
        payment=PaymentInput(method="cash"),
    )
    shift = billing.shift_summary(alpha.gym, desk.person)
    assert shift.cash_rwf == 30000 and shift.payments == 1 and shift.sales == 1

    client = sign_in(desk)
    page = client.get(reverse("billing:my_shift")).content.decode()
    assert "30,000" in page
    client.post(reverse("billing:my_shift"))
    closed = ShiftClose.objects.get()
    assert closed.cash_rwf == 30000
    assert billing.shift_summary(alpha.gym, desk.person).cash_rwf == 0
    owner_view = billing.cash_by_person_today(alpha.gym)
    assert owner_view[0]["cash_rwf"] == 30000 and owner_view[0]["closed_at"] is not None
    with pytest.raises(DatabaseError), transaction.atomic():
        ShiftClose.objects.filter(pk=closed.pk).update(cash_rwf=1)


def test_the_receptionist_menu_is_short_and_the_owner_menu_is_the_business(
    alpha, add_staff, sign_in
):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    settings_link = f'href="{reverse("gyms:settings")}"'
    money_link = f'href="{reverse("billing:payments_home")}"'
    page = sign_in(desk).get(reverse("frontdesk:desk")).content.decode()
    assert "My shift" in page and settings_link not in page and money_link not in page
    page = sign_in(alpha).get(reverse("frontdesk:desk")).content.decode()
    assert settings_link in page and money_link in page


# --- The league ---------------------------------------------------------------------


def test_the_league_ranks_companies_by_share_of_active_staff(alpha, company_setup, branch):
    created, employee, gym_service, _ = company_setup
    small = corp.create_company(
        name="Small Co", tin="", officer_name="S", officer_phone="0788900002", actor=None
    )
    rows = corp.league(timezone.localdate())
    assert [r.name for r in rows] == ["Small Co", "WASAC Test"] or [r.name for r in rows] == [
        "WASAC Test"
    ]
    # Four visits this month put WASAC's only employee (100%) on top.
    today = timezone.localdate()
    for days_ago in range(4):
        CorporateVisit.objects.create(
            gym=alpha.gym,
            company=created.company,
            contract=created.company.contracts.get(),
            employee=employee,
            branch=branch,
            checked_in_on=today.replace(day=1) + timedelta(days=days_ago)
            if today.day >= 4
            else today - timedelta(days=days_ago),
            method="phone",
        )
    rows = corp.league(today)
    assert rows[0].name == "WASAC Test" and rows[0].percent == 100
    assert small.company.name not in [r.name for r in rows]  # no staff listed yet


def test_the_officer_sees_each_gym_by_service(alpha, company_setup, tablet_client):
    created, employee, gym_service, sauna = company_setup
    code = corp.card_code(employee)
    tablet_client.post(
        reverse("frontdesk:kiosk_enter", args=[employee.public_id]),
        {"code": code, "service": [str(gym_service.public_id), str(sauna.public_id)]},
    )
    month = corp.company_month(created.company, timezone.localdate())
    [row] = month["gyms"]
    assert row.gym_name == "Alpha Fitness" and row.total_rwf == 6500
    assert row.cells["Gym"] == {"count": 1, "amount": 2500}
    assert month["services"] == ["Gym", "Sauna"]


# --- Reaching out -----------------------------------------------------------


def test_the_desk_offers_a_written_message_for_members_about_to_lapse(
    alpha, branch, monthly, add_staff, sign_in
):
    """Ending soon and gone quiet both appear, each with WhatsApp and SMS."""
    today = timezone.localdate()
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)

    leaving = new_member(alpha, monthly, name="Chantal Uwase", phone="0788400401")
    Membership.objects.filter(member=leaving).update(
        start_date=today - timedelta(days=28), end_date=today + timedelta(days=2)
    )

    quiet = new_member(alpha, monthly, name="Patrick Nsengimana", phone="0788400402")
    CheckIn.objects.create(
        gym=alpha.gym,
        branch=branch,
        member=quiet,
        membership=Membership.objects.filter(member=quiet).first(),
        checked_in_on=today - timedelta(days=4),
    )

    page = sign_in(desk).get(reverse("frontdesk:desk")).content.decode()

    assert "Reach out today" in page
    assert "Chantal Uwase" in page and "Patrick Nsengimana" in page
    assert "https://wa.me/250788400401?text=" in page
    assert "sms:%2B250788400402?body=" in page or "sms:+250788400402?body=" in page
    # The message names the member and the gym, so staff can send it as it is.
    assert "Muraho%20Chantal" in page and "missed%20you" in page


# --- The receptionist's working lists ----------------------------------------


def test_several_matches_are_short_rows_and_one_match_is_the_full_decision(
    alpha, monthly, add_staff, sign_in
):
    """A choice between people is a list; a single person is a decision."""
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    new_member(alpha, monthly, name="Alice Mutoni", phone="0788300301")
    new_member(alpha, monthly, name="Daniel Mutoni", phone="0788300302")
    client = sign_in(desk)

    many = client.get(reverse("attendance:home"), {"q": "Mutoni"}).content.decode()
    assert "people match" in many
    assert "Alice Mutoni" in many and "Daniel Mutoni" in many
    # The long explanations belong to the single-result card, not the list.
    assert "valid until" not in many

    one = client.get(reverse("attendance:home"), {"q": "Alice Mut"}).content.decode()
    assert "people match" not in one and "valid until" in one


def test_the_member_list_shows_where_each_membership_stands_and_can_be_narrowed(
    alpha, monthly, add_staff, sign_in
):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    today = timezone.localdate()
    active = new_member(alpha, monthly, name="Active Person", phone="0788300311")
    ended = new_member(alpha, monthly, name="Ended Person", phone="0788300312")
    Membership.objects.filter(member=ended).update(
        start_date=today - timedelta(days=40), end_date=today - timedelta(days=10)
    )
    never = new_member(alpha, None, name="Never Person", phone="0788300313")
    client = sign_in(desk)

    page = client.get(reverse("frontdesk:people")).content.decode()
    assert "Monthly" in page and "Ended" in page and "None yet" in page

    only_active = client.get(reverse("frontdesk:people"), {"show": "active"}).content.decode()
    assert active.full_name in only_active
    assert ended.full_name not in only_active and never.full_name not in only_active

    only_lapsed = client.get(reverse("frontdesk:people"), {"show": "lapsed"}).content.decode()
    assert ended.full_name in only_lapsed and active.full_name not in only_lapsed

    everyone = client.get(reverse("frontdesk:people")).content.decode()
    assert never.full_name in everyone


def test_the_member_list_asks_the_database_the_same_few_times_however_long_it_is(
    alpha, monthly, add_staff, sign_in, django_capture_on_commit_callbacks
):
    """
    Status for a page of members must not cost one query per member, or a gym
    with thousands of them would crawl.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    client = sign_in(desk)

    for index in range(4):
        new_member(alpha, monthly, name=f"Small {index}", phone=f"07883105{index:02d}")
    with CaptureQueriesContext(connection) as few:
        client.get(reverse("frontdesk:people"))

    for index in range(20):
        new_member(alpha, monthly, name=f"Large {index}", phone=f"07883106{index:02d}")
    with CaptureQueriesContext(connection) as many:
        client.get(reverse("frontdesk:people"))

    assert len(many) == len(few)
