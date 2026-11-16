"""Tests for checking members in."""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.attendance import services
from apps.attendance.models import CheckIn
from apps.attendance.services import Reason
from apps.billing import services as billing_services
from apps.billing.services import PaymentInput
from apps.core import tenant_db
from apps.gyms import services as gym_services
from apps.gyms.models import Role
from apps.members import services as member_services
from apps.members.models import Membership
from apps.members.services import MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


def today():
    return timezone.localdate()


def open_gym(make_gym, name, phone, *, payments=True):
    staff = make_gym(name, phone, "Aline Uwase")
    if payments:
        module_services.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    module_services.enable_module(gym=staff.gym, code=ATTENDANCE_ACCESS)
    return staff


@pytest.fixture
def alpha(make_gym):
    return open_gym(make_gym, "Alpha Fitness", "0788000001")


@pytest.fixture
def bravo(make_gym):
    return open_gym(make_gym, "Bravo Fitness", "0788000002")


@pytest.fixture
def monthly(alpha):
    return member_services.create_package(
        by=alpha.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )


def make_member(staff, name="Eric Mugisha", phone="0788111222"):
    return member_services.register_member(
        by=staff.assignment, phone=phone, details=MemberDetails(full_name=name)
    )


def sell(staff, member, package, *, start=None, payment=None):
    return billing_services.sell_membership(
        by=staff.assignment,
        member=member,
        package=package,
        start_date=start or today(),
        idempotency_key=uuid.uuid4(),
        payment=payment,
    )


def move_membership(membership, *, start, end):
    """Pretend a membership was sold in the past (tests cannot wait a month)."""
    Membership.objects.filter(pk=membership.pk).update(start_date=start, end_date=end)


@pytest.fixture
def member(alpha):
    return make_member(alpha)


@pytest.fixture
def valid_member(alpha, member, monthly):
    sell(alpha, member, monthly, payment=PaymentInput(method="cash"))
    return member


# --- May this member come in? -----------------------------------------------


def test_a_member_with_no_membership_is_refused(member):
    result = services.assess(member)

    assert not result.allowed and result.reason == Reason.NO_MEMBERSHIP


def test_a_member_with_a_valid_membership_is_allowed(valid_member):
    result = services.assess(valid_member)

    assert result.allowed
    assert result.membership.package_name == "Monthly"
    assert not result.expiring_soon and result.owes_rwf == 0 and result.already_today is None


def test_an_expired_membership_is_refused_from_the_day_after_it_ends(alpha, member, monthly):
    sale = sell(alpha, member, monthly)
    move_membership(
        sale.membership, start=today() - timedelta(days=30), end=today() - timedelta(days=1)
    )

    result = services.assess(member)

    assert not result.allowed and result.reason == Reason.EXPIRED
    assert result.membership == sale.membership


def test_the_last_day_still_counts_and_gives_a_renewal_warning(alpha, member, monthly):
    sale = sell(alpha, member, monthly)
    move_membership(sale.membership, start=today() - timedelta(days=29), end=today())

    result = services.assess(member)

    assert result.allowed and result.days_left == 1 and result.expiring_soon


@pytest.mark.parametrize(("days_left", "warned"), [(7, True), (8, False)])
def test_the_renewal_warning_starts_seven_days_before_the_end(
    alpha, member, monthly, days_left, warned
):
    sale = sell(alpha, member, monthly)
    move_membership(
        sale.membership,
        start=today() - timedelta(days=10),
        end=today() + timedelta(days=days_left - 1),
    )

    assert services.assess(member).expiring_soon is warned


def test_a_membership_that_starts_later_is_not_valid_yet(alpha, member, monthly):
    sell(alpha, member, monthly, start=today() + timedelta(days=3))

    result = services.assess(member)

    assert not result.allowed and result.reason == Reason.NOT_STARTED


def test_owing_money_warns_but_does_not_block(alpha, member, monthly):
    sell(alpha, member, monthly, payment=PaymentInput(method="cash", amount_rwf=10000))

    result = services.assess(member)

    assert result.allowed and result.owes_rwf == 20000


def test_a_cancelled_membership_does_not_count(alpha, member, monthly):
    sale = sell(alpha, member, monthly)
    Membership.objects.filter(pk=sale.membership.pk).update(cancelled_at=timezone.now())

    result = services.assess(member)

    assert not result.allowed and result.reason == Reason.NO_MEMBERSHIP


# --- Checking in ------------------------------------------------------------


def test_checking_in_records_who_when_where_and_which_membership(alpha, valid_member):
    record, is_new = services.check_in(by=alpha.assignment, member=valid_member)

    assert is_new
    assert record.checked_in_on == today()
    assert record.admitted_by == alpha.person
    assert record.branch.name == "Main branch"
    assert record.membership.member == valid_member
    assert services.assess(valid_member).already_today == record


def test_a_second_check_in_the_same_day_returns_the_first(alpha, valid_member):
    first, _ = services.check_in(by=alpha.assignment, member=valid_member)

    second, is_new = services.check_in(by=alpha.assignment, member=valid_member)

    assert second == first and not is_new
    assert CheckIn.objects.count() == 1


def test_a_member_without_a_valid_membership_cannot_be_checked_in(alpha, member):
    with pytest.raises(services.NotAllowedIn) as problem:
        services.check_in(by=alpha.assignment, member=member)

    assert problem.value.assessment.reason == Reason.NO_MEMBERSHIP
    assert not CheckIn.objects.exists()


@pytest.mark.parametrize(
    ("role", "may"),
    [
        (Role.BRANCH_MANAGER, True),
        (Role.RECEPTIONIST, True),
        (Role.TRAINER, True),
        (Role.SALES_REP, False),
    ],
)
def test_who_may_check_members_in(alpha, valid_member, add_staff, role, may):
    staff = add_staff(alpha.gym, "0788000005", role)

    if may:
        assert services.check_in(by=staff.assignment, member=valid_member)[1] is True
    else:
        with pytest.raises(PermissionDenied):
            services.check_in(by=staff.assignment, member=valid_member)


def test_check_in_needs_the_attendance_application(make_gym):
    staff = make_gym("No Attendance Gym", "0788000009")
    member = make_member(staff)

    with pytest.raises(PermissionDenied, match="not switched on"):
        services.check_in(by=staff.assignment, member=member)


def test_it_works_for_a_gym_without_the_payments_application(make_gym):
    staff = open_gym(make_gym, "Attendance Only", "0788000009", payments=False)
    member = make_member(staff)
    package = member_services.create_package(
        by=staff.assignment, name="Monthly", price_rwf=1, duration_count=1, duration_unit="month"
    )
    member_services.create_membership(
        by=staff.assignment, member=member, package=package, start_date=today()
    )

    assert services.assess(member).owes_rwf == 0
    assert services.check_in(by=staff.assignment, member=member)[1] is True


def test_staff_cannot_check_in_another_gyms_member(alpha, bravo, valid_member):
    with pytest.raises(PermissionDenied):
        services.check_in(by=bravo.assignment, member=valid_member)


def test_with_several_branches_the_desk_must_say_where_it_is(alpha, valid_member, bravo):
    remera = gym_services.add_branch(gym=alpha.gym, name="Remera")

    with pytest.raises(services.BranchNeeded):
        services.check_in(by=alpha.assignment, member=valid_member)
    with pytest.raises(PermissionDenied):
        their_branch = gym_services.add_branch(gym=bravo.gym, name="Elsewhere")
        services.check_in(by=alpha.assignment, member=valid_member, branch=their_branch)

    record, _ = services.check_in(by=alpha.assignment, member=valid_member, branch=remera)
    assert record.branch == remera


def test_staff_tied_to_a_branch_check_in_at_that_branch(alpha, valid_member, add_staff):
    remera = gym_services.add_branch(gym=alpha.gym, name="Remera")
    receptionist = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    receptionist.assignment.branch = remera
    receptionist.assignment.save()

    record, _ = services.check_in(by=receptionist.assignment, member=valid_member)

    assert record.branch == remera


def test_searching_for_check_in(alpha, valid_member):
    make_member(alpha, "Claudine Mukamana", "0722333444")

    assert services.find_for_check_in(alpha.gym, "") == []
    assert services.find_for_check_in(alpha.gym, "e") == []  # too short to search
    by_phone = services.find_for_check_in(alpha.gym, "0788 111")
    by_name = services.find_for_check_in(alpha.gym, "mu")

    assert [(r.member.full_name, r.allowed) for r in by_phone] == [("Eric Mugisha", True)]
    assert [(r.member.full_name, r.allowed) for r in by_name] == [
        ("Claudine Mukamana", False),
        ("Eric Mugisha", True),
    ]


def test_check_ins_are_hidden_from_other_gyms_by_the_database(alpha, bravo, valid_member):
    services.check_in(by=alpha.assignment, member=valid_member)

    tenant_db.enter_app_role(person_id=None)
    try:
        tenant_db.set_current_gym(bravo.gym.id)
        seen_by_bravo = CheckIn.objects.count()  # unfiltered on purpose
        tenant_db.set_current_gym(alpha.gym.id)
        seen_by_alpha = CheckIn.objects.count()
    finally:
        tenant_db.leave_app_role()

    assert (seen_by_bravo, seen_by_alpha) == (0, 1)


# --- Screens ----------------------------------------------------------------


def test_the_desk_finds_a_member_and_checks_them_in(alpha, valid_member, sign_in):
    client = sign_in(alpha)
    home = reverse("attendance:home")

    page = client.get(home).content.decode()
    assert "Nobody has checked in yet today." in page and "0 check-ins today" in page

    # Typing in the search box (HTMX) returns only the result cards.
    results = client.get(home, {"q": "0788"}, headers={"HX-Request": "true"}).content.decode()
    assert "<html" not in results
    assert "Eric Mugisha" in results and "Valid" in results and "Check in" in results
    assert "0788 ••• 222" in results and "0788 111 222" not in results  # number partly hidden

    response = client.post(
        reverse("attendance:check_in", args=[valid_member.public_id]), follow=True
    )
    page = response.content.decode()
    assert "Eric Mugisha checked in at" in page
    assert "1 check-in today" in page and "admitted by Aline Uwase" in page

    again = client.post(reverse("attendance:check_in", args=[valid_member.public_id]), follow=True)
    assert "was already checked in today at" in again.content.decode()
    assert CheckIn.objects.count() == 1

    results = client.get(home, {"q": "eric"}).content.decode()
    assert "Already checked in today at" in results


def test_the_desk_sees_why_a_member_is_refused_and_can_renew(alpha, member, monthly, sign_in):
    sale = sell(alpha, member, monthly, payment=PaymentInput(method="cash", amount_rwf=10000))
    move_membership(
        sale.membership, start=today() - timedelta(days=40), end=today() - timedelta(days=10)
    )
    client = sign_in(alpha)

    results = client.get(reverse("attendance:home"), {"q": "eric"}).content.decode()

    assert "Not valid" in results and "Entry is refused until renewed." in results
    assert "Still owes RWF 20,000." in results
    assert reverse("billing:sell", args=[member.public_id]) in results
    assert reverse("attendance:check_in", args=[member.public_id]) not in results  # no button

    forced = client.post(reverse("attendance:check_in", args=[member.public_id]), follow=True)
    assert "was NOT checked in" in forced.content.decode()
    assert not CheckIn.objects.exists()


def test_a_trainer_can_check_in_but_gets_no_links_into_member_records(
    alpha, valid_member, add_staff, sign_in
):
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.TRAINER))

    results = client.get(reverse("attendance:home"), {"q": "eric"}).content.decode()
    assert "Check in" in results
    assert reverse("members:member_detail", args=[valid_member.public_id]) not in results

    checked = client.post(reverse("attendance:check_in", args=[valid_member.public_id]))
    assert checked.status_code == 302 and CheckIn.objects.count() == 1
    visits = client.get(reverse("attendance:member_visits", args=[valid_member.public_id]))
    assert visits.status_code == 403


def test_a_sales_rep_has_no_check_in_screen(alpha, valid_member, add_staff, sign_in):
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.SALES_REP))

    assert client.get(reverse("attendance:home")).status_code == 403
    denied = client.post(reverse("attendance:check_in", args=[valid_member.public_id]))
    assert denied.status_code == 403
    assert reverse("attendance:home") not in client.get(reverse("gyms:home")).content.decode()


def test_a_gym_with_several_branches_chooses_the_desk_once(alpha, valid_member, sign_in):
    remera = gym_services.add_branch(gym=alpha.gym, name="Remera")
    client = sign_in(alpha)

    page = client.get(reverse("attendance:home")).content.decode()
    assert "Which branch is this desk at?" in page and 'name="q"' not in page

    client.post(reverse("attendance:set_branch"), {"branch": str(remera.public_id)})
    page = client.get(reverse("attendance:home")).content.decode()
    assert 'name="q"' in page and "Remera" in page

    client.post(reverse("attendance:check_in", args=[valid_member.public_id]))
    assert CheckIn.objects.get().branch == remera


def test_the_desk_cannot_be_set_to_another_gyms_branch(alpha, bravo, sign_in):
    gym_services.add_branch(gym=alpha.gym, name="Remera")
    theirs = gym_services.add_branch(gym=bravo.gym, name="Elsewhere")
    client = sign_in(alpha)

    response = client.post(reverse("attendance:set_branch"), {"branch": str(theirs.public_id)})

    assert response.status_code == 404


def test_the_member_page_shows_visits(alpha, valid_member, sign_in):
    services.check_in(by=alpha.assignment, member=valid_member)
    client = sign_in(alpha)

    member_page = client.get(reverse("members:member_detail", args=[valid_member.public_id]))
    visits_url = reverse("attendance:member_visits", args=[valid_member.public_id])
    assert visits_url in member_page.content.decode()

    part = client.get(visits_url, headers={"HX-Request": "true"}).content.decode()
    assert "<html" not in part
    assert "1 visit this month" in part and "Main branch" in part


def test_without_the_application_there_is_no_check_in(make_gym, sign_in):
    staff = make_gym("No Attendance Gym", "0788000009")
    member = make_member(staff)
    client = sign_in(staff)

    assert client.get(reverse("attendance:home")).status_code == 403
    assert client.post(reverse("attendance:check_in", args=[member.public_id])).status_code == 403
    page = client.get(reverse("members:member_detail", args=[member.public_id])).content.decode()
    assert reverse("attendance:home") not in page
    assert reverse("attendance:member_visits", args=[member.public_id]) not in page


def test_another_gyms_members_cannot_be_found_or_checked_in(alpha, bravo, valid_member, sign_in):
    services.check_in(by=alpha.assignment, member=valid_member)
    client = sign_in(bravo)

    results = client.get(reverse("attendance:home"), {"q": "eric"}).content.decode()
    assert "Eric Mugisha" not in results
    assert "Eric Mugisha" not in client.get(reverse("attendance:home")).content.decode()

    for name in ("attendance:check_in", "attendance:member_visits"):
        url = reverse(name, args=[valid_member.public_id])
        response = client.post(url) if name.endswith("check_in") else client.get(url)
        assert response.status_code == 404, name
    assert CheckIn.objects.count() == 1


def test_phone_numbers_are_partly_hidden_at_the_desk():
    from apps.core.templatetags.phones import phone_masked

    assert phone_masked("+250788123456") == "0788 ••• 456"
    assert phone_masked("") == ""
