"""
Tests for the owner's figures and the renewals list.

Each figure is checked against a small, known set of members, payments and
visits, so that a wrong number would show up as a failing test.
"""

import uuid
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance import services as attendance
from apps.attendance.models import CheckIn
from apps.billing import services as billing
from apps.billing.services import PaymentInput
from apps.dashboard import services
from apps.dashboard.services import month_start, previous_month
from apps.gyms.models import Role
from apps.members import services as members
from apps.members.models import Member, Membership
from apps.members.services import MemberDetails
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


def today():
    return timezone.localdate()


def days(n):
    return today() + timedelta(days=n)


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    modules.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    modules.enable_module(gym=staff.gym, code=ATTENDANCE_ACCESS)
    return staff


@pytest.fixture
def packages(alpha):
    def make(name, price):
        return members.create_package(
            by=alpha.assignment, name=name, price_rwf=price, duration_count=1, duration_unit="month"
        )

    return {"monthly": make("Monthly", 30000), "student": make("Student", 20000)}


def member(staff, name, phone):
    return members.register_member(
        by=staff.assignment, phone=phone, details=MemberDetails(full_name=name)
    )


def sell(staff, who, package, payment=None):
    return billing.sell_membership(
        by=staff.assignment,
        member=who,
        package=package,
        start_date=today(),
        idempotency_key=uuid.uuid4(),
        payment=payment,
    )


def set_dates(membership, start, end):
    """Pretend a membership was sold at another time (tests cannot wait)."""
    Membership.objects.filter(pk=membership.pk).update(start_date=start, end_date=end)


def set_joined(who, day):
    Member.objects.filter(pk=who.pk).update(joined_on=day)


# --- Dates ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 16), (date(2026, 10, 1), date(2026, 10, 31))),
        (date(2026, 1, 5), (date(2025, 12, 1), date(2025, 12, 31))),  # across a new year
        (date(2026, 3, 31), (date(2026, 2, 1), date(2026, 2, 28))),
    ],
)
def test_previous_month(day, expected):
    assert previous_month(day) == expected
    assert month_start(day) == day.replace(day=1)


# --- Members ----------------------------------------------------------------


def test_active_members_count_people_with_a_membership_valid_today(alpha, packages):
    valid = member(alpha, "Valid", "0788100001")
    sell(alpha, valid, packages["monthly"])
    expired = member(alpha, "Expired", "0788100002")
    set_dates(sell(alpha, expired, packages["monthly"]).membership, days(-40), days(-10))
    future = member(alpha, "Future", "0788100003")
    set_dates(sell(alpha, future, packages["monthly"]).membership, days(5), days(35))
    member(alpha, "Never bought", "0788100004")

    figures = services.overview(alpha.gym, today())

    assert figures.active_members == 1


def test_a_renewed_member_is_counted_once(alpha, packages):
    who = member(alpha, "Renewer", "0788100001")
    first = sell(alpha, who, packages["monthly"]).membership
    billing.sell_membership(
        by=alpha.assignment,
        member=who,
        package=packages["monthly"],
        start_date=first.end_date + timedelta(days=1),
        idempotency_key=uuid.uuid4(),
    )

    assert services.overview(alpha.gym, today()).active_members == 1


def test_new_members_this_month(alpha):
    member(alpha, "Joined today", "0788100001")
    old = member(alpha, "Joined long ago", "0788100002")
    set_joined(old, month_start(today()) - timedelta(days=1))

    assert services.overview(alpha.gym, today()).new_members_this_month == 1


def test_ending_soon_leaves_out_renewed_members_and_later_endings(alpha, packages):
    ends_today = member(alpha, "Ends today", "0788100001")
    set_dates(sell(alpha, ends_today, packages["monthly"]).membership, days(-29), today())

    ends_in_7 = member(alpha, "Ends in 7 days", "0788100002")  # day 7 counts
    set_dates(sell(alpha, ends_in_7, packages["monthly"]).membership, days(-20), days(6))

    ends_in_8 = member(alpha, "Ends in 8 days", "0788100003")
    set_dates(sell(alpha, ends_in_8, packages["monthly"]).membership, days(-20), days(7))

    renewed = member(alpha, "Already renewed", "0788100004")
    current = sell(alpha, renewed, packages["monthly"]).membership
    set_dates(current, days(-27), days(2))
    billing.sell_membership(
        by=alpha.assignment,
        member=renewed,
        package=packages["monthly"],
        start_date=days(3),
        idempotency_key=uuid.uuid4(),
    )

    soon = [m.member.full_name for m in members.ending_soon(alpha.gym, today(), 7)]
    later = [m.member.full_name for m in members.ending_soon(alpha.gym, today(), 30)]

    assert soon == ["Ends today", "Ends in 7 days"]
    assert later == ["Ends today", "Ends in 7 days", "Ends in 8 days"]
    assert services.overview(alpha.gym, today()).ending_soon == 2


def test_lapsed_members_are_those_whose_last_membership_ended_recently(alpha, packages):
    lapsed = member(alpha, "Lapsed", "0788100001")
    set_dates(sell(alpha, lapsed, packages["monthly"]).membership, days(-40), days(-10))

    long_gone = member(alpha, "Long gone", "0788100002")
    set_dates(sell(alpha, long_gone, packages["monthly"]).membership, days(-90), days(-60))

    came_back = member(alpha, "Came back", "0788100003")
    old = sell(alpha, came_back, packages["monthly"]).membership
    set_dates(old, days(-40), days(-10))
    billing.sell_membership(
        by=alpha.assignment,
        member=came_back,
        package=packages["monthly"],
        start_date=today(),
        idempotency_key=uuid.uuid4(),
    )

    two_passes = member(alpha, "Two day passes", "0788100004")
    first = sell(alpha, two_passes, packages["monthly"]).membership
    set_dates(first, days(-8), days(-8))
    second = billing.sell_membership(
        by=alpha.assignment,
        member=two_passes,
        package=packages["student"],
        start_date=today(),
        idempotency_key=uuid.uuid4(),
    ).membership
    set_dates(second, days(-5), days(-5))

    names = [m.member.full_name for m in members.lapsed(alpha.gym, today(), 30)]

    assert names == ["Two day passes", "Lapsed"]  # most recent first, each person once


# --- Money ------------------------------------------------------------------


def test_money_figures(alpha, packages):
    a, b, c = (
        member(alpha, n, p)
        for n, p in [("A", "0788100001"), ("B", "0788100002"), ("C", "0788100003")]
    )
    sell(alpha, a, packages["monthly"], PaymentInput(method="cash"))
    sell(alpha, b, packages["student"], PaymentInput(method="mtn_momo", reference="TX1"))
    part = sell(alpha, c, packages["monthly"], PaymentInput(method="cash", amount_rwf=5000))
    billing.reverse_payment(by=alpha.assignment, payment=part.payment, reason="Wrong member")

    money = services.overview(alpha.gym, today()).money

    assert money.collected_today == 50000  # 30,000 + 20,000; the reversed 5,000 is left out
    assert money.collected_this_month == 50000
    assert money.owed_by_members == 30000  # C owes the full price again
    assert [(r["label"], r["amount"], r["share"]) for r in money.by_method] == [
        ("Cash", 30000, 100),
        ("MTN Mobile Money", 20000, 67),
    ]
    assert [(r["label"], r["count"], r["amount"]) for r in money.by_package] == [
        ("Monthly", 2, 60000),  # sold, whether or not paid
        ("Student", 1, 20000),
    ]


def test_last_months_money_is_kept_apart_from_this_months(alpha, packages):
    who = member(alpha, "A", "0788100001")
    sell(alpha, who, packages["monthly"], PaymentInput(method="cash"))
    # Ask for the figures as they will look next month, rather than changing
    # the payment's date: money records are permanent, even in tests.
    next_month = month_start(month_start(today()) + timedelta(days=32)) + timedelta(days=4)

    money = services.overview(alpha.gym, next_month).money

    assert money.collected_this_month == 0
    assert money.collected_last_month == 30000
    assert money.collected_today == 0


def test_balances_for_several_members_at_once(alpha, packages):
    owes = member(alpha, "Owes", "0788100001")
    paid = member(alpha, "Paid", "0788100002")
    sell(alpha, owes, packages["monthly"], PaymentInput(method="cash", amount_rwf=10000))
    sell(alpha, paid, packages["monthly"], PaymentInput(method="cash"))

    assert billing.balances_for(alpha.gym, [owes.id, paid.id]) == {owes.id: 20000}


# --- Visits -----------------------------------------------------------------


def test_visit_figures_and_members_not_seen_for_two_weeks(alpha, packages):
    regular = member(alpha, "Regular", "0788100001")
    absent = member(alpha, "Absent", "0788100002")
    newcomer = member(alpha, "Newcomer", "0788100003")
    for who in (regular, absent, newcomer):
        sell(alpha, who, packages["monthly"])
    set_joined(regular, days(-60))
    set_joined(absent, days(-60))
    attendance.check_in(by=alpha.assignment, member=regular)
    old_visit = CheckIn.objects.create(
        gym=alpha.gym,
        member=absent,
        branch=CheckIn.objects.get().branch,
        membership=Membership.objects.get(member=absent),
        checked_in_on=days(-20),
    )

    visits = services.overview(alpha.gym, today()).visits

    assert visits.today == 1
    assert visits.this_month == 1 + (old_visit.checked_in_on >= month_start(today()))
    assert visits.not_visiting == 1  # Absent; the newcomer joined too recently to judge
    assert list(attendance.not_visiting(alpha.gym, today())) == [absent]


# --- Applications switched off ----------------------------------------------


def test_figures_for_applications_the_gym_does_not_have_are_left_out(make_gym):
    staff = make_gym("Basic Gym", "0788000009")

    figures = services.overview(staff.gym, today())

    assert figures.money is None and figures.visits is None
    assert figures.active_members == 0


# --- Other gyms -------------------------------------------------------------


def test_another_gyms_activity_never_appears_in_the_figures(alpha, packages, make_gym):
    sell(
        alpha,
        member(alpha, "Alpha member", "0788100001"),
        packages["monthly"],
        PaymentInput(method="cash"),
    )
    bravo = make_gym("Bravo Fitness", "0788000002")
    modules.enable_module(gym=bravo.gym, code=PAYMENTS_FINANCE)
    modules.enable_module(gym=bravo.gym, code=ATTENDANCE_ACCESS)

    figures = services.overview(bravo.gym, today())

    assert figures.active_members == 0 and figures.new_members_this_month == 0
    assert figures.money.collected_today == 0 and figures.money.owed_by_members == 0
    assert figures.money.by_method == [] and figures.money.by_package == []


# --- Screens ----------------------------------------------------------------


def test_the_owner_sees_the_figures_on_the_overview_page(alpha, packages, sign_in):
    who = member(alpha, "Eric Mugisha", "0788111222")
    set_dates(
        sell(alpha, who, packages["monthly"], PaymentInput(method="cash")).membership,
        days(-25),
        days(3),
    )
    client = sign_in(alpha)

    home = client.get(reverse("gyms:home")).content.decode()
    assert reverse("dashboard:figures") in home  # loaded into the page by HTMX

    part = client.get(reverse("dashboard:figures"), headers={"HX-Request": "true"}).content.decode()
    assert "<html" not in part
    assert "Clients today" in part and "Collected today" in part and "Worth knowing" in part
    assert "RWF 30,000" in part
    assert "Eric Mugisha" in part  # in the "ending within 7 days" list

    full = client.get(reverse("dashboard:figures")).content.decode()
    assert "<html" in full and "Active members" in full


@pytest.mark.parametrize("role", [Role.RECEPTIONIST, Role.SALES_REP, Role.TRAINER])
def test_only_owners_and_managers_see_the_figures(alpha, add_staff, sign_in, role):
    client = sign_in(add_staff(alpha.gym, "0788000005", role))

    assert client.get(reverse("dashboard:figures")).status_code == 403
    assert reverse("dashboard:figures") not in client.get(reverse("gyms:home")).content.decode()


def test_a_branch_manager_sees_the_figures(alpha, add_staff, sign_in):
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.BRANCH_MANAGER))

    assert client.get(reverse("dashboard:figures")).status_code == 200


def test_the_renewals_page_lists_members_by_tab(alpha, packages, sign_in, add_staff):
    soon = member(alpha, "Ends soon", "0788100001")
    set_dates(
        sell(
            alpha, soon, packages["monthly"], PaymentInput(method="cash", amount_rwf=1000)
        ).membership,
        days(-25),
        days(2),
    )
    later = member(alpha, "Ends later", "0788100002")
    set_dates(sell(alpha, later, packages["monthly"]).membership, days(-10), days(20))
    gone = member(alpha, "Already gone", "0788100003")
    set_dates(sell(alpha, gone, packages["monthly"]).membership, days(-40), days(-5))

    client = sign_in(add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST))
    url = reverse("dashboard:renewals")

    first = client.get(url).content.decode()
    assert "Ends soon" in first and "Ends later" not in first and "Already gone" not in first
    assert "Owes RWF 29,000" in first and "(in 2 days)" in first  # ends the day after tomorrow
    assert reverse("billing:sell", args=[soon.public_id]) in first

    within_30 = client.get(url, {"show": "later"}).content.decode()
    assert "Ends soon" in within_30 and "Ends later" in within_30

    lapsed = client.get(url, {"show": "lapsed"}).content.decode()
    assert "Already gone" in lapsed and "Ends soon" not in lapsed

    unknown = client.get(url, {"show": "<script>"}).content.decode()
    assert "Ends soon" in unknown and "<script>" not in unknown


def test_a_trainer_cannot_open_renewals(alpha, add_staff, sign_in):
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.TRAINER))

    assert client.get(reverse("dashboard:renewals")).status_code == 403


def test_empty_figures_for_a_new_gym_explain_themselves(alpha, sign_in):
    page = sign_in(alpha).get(reverse("dashboard:figures")).content.decode()

    assert "Nothing collected yet this month." in page
    assert "No memberships sold yet this month." in page
    assert "No memberships end in the next 7 days without a renewal." in page


def test_day_and_week_passes_are_not_chased_for_renewal(alpha):
    day_pass = members.create_package(
        by=alpha.assignment,
        name="Daily pass",
        price_rwf=3000,
        duration_count=1,
        duration_unit="day",
    )
    weekly = members.create_package(
        by=alpha.assignment, name="Weekly", price_rwf=8000, duration_count=1, duration_unit="week"
    )
    visitor = member(alpha, "Drop-in visitor", "0788100001")
    sell(alpha, visitor, day_pass)
    weekly_member = member(alpha, "Weekly member", "0788100002")
    set_dates(sell(alpha, weekly_member, weekly).membership, days(-20), days(-14))

    figures = services.overview(alpha.gym, today())

    assert figures.active_members == 1  # the day pass is valid today
    assert figures.ending_soon == 0 and figures.lapsed == 0
    from apps.attendance import services as desk

    assert desk.assess(visitor).allowed and not desk.assess(visitor).expiring_soon
