"""Tests for freezing and extending memberships."""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.attendance import services as attendance
from apps.attendance.services import Reason
from apps.audit.models import AuditEntry
from apps.billing import services as billing
from apps.gyms.models import Role
from apps.members import services
from apps.members.models import Membership, MembershipFreeze
from apps.members.services import InvalidDetails, MemberDetails
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
def monthly(alpha):
    return services.create_package(
        by=alpha.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )


@pytest.fixture
def membership(alpha, monthly):
    member = services.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric Mugisha")
    )
    return billing.sell_membership(
        by=alpha.assignment,
        member=member,
        package=monthly,
        start_date=today(),
        idempotency_key=uuid.uuid4(),
    ).membership


def reload(m):
    return Membership.objects.get(pk=m.pk)


# --- Extending --------------------------------------------------------------


def test_extending_moves_the_end_and_is_recorded(alpha, membership):
    old_end = membership.end_date

    services.extend_membership(
        by=alpha.assignment, membership=membership, days=7, reason="Gym closed for repairs"
    )

    assert reload(membership).end_date == old_end + timedelta(days=7)
    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="membership.extended")
    assert entry.details["days"] == 7 and "repairs" in entry.summary


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [({"days": 0}, "days"), ({"days": 366}, "days"), ({"reason": "  "}, "reason")],
)
def test_extending_needs_sensible_days_and_a_reason(alpha, membership, kwargs, field):
    with pytest.raises(InvalidDetails) as problem:
        services.extend_membership(
            by=alpha.assignment, membership=membership, **{"days": 5, "reason": "ok", **kwargs}
        )

    assert problem.value.field == field


def test_an_expired_membership_cannot_be_extended(alpha, membership):
    Membership.objects.filter(pk=membership.pk).update(start_date=days(-40), end_date=days(-1))

    with pytest.raises(InvalidDetails) as problem:
        services.extend_membership(
            by=alpha.assignment, membership=reload(membership), days=5, reason="late"
        )

    assert problem.value.reason == "not_current"


def test_extending_cannot_run_into_the_next_membership(alpha, membership, monthly):
    billing.sell_membership(
        by=alpha.assignment,
        member=membership.member,
        package=monthly,
        start_date=membership.end_date + timedelta(days=1),
        idempotency_key=uuid.uuid4(),
    )

    with pytest.raises(InvalidDetails) as problem:
        services.extend_membership(by=alpha.assignment, membership=membership, days=1, reason="x")

    assert problem.value.reason == "runs_into_next"


@pytest.mark.parametrize("role", [Role.RECEPTIONIST, Role.TRAINER])
def test_only_owners_and_managers_extend_or_freeze(alpha, membership, add_staff, role):
    staff = add_staff(alpha.gym, "0788000005", role)

    with pytest.raises(PermissionDenied):
        services.extend_membership(by=staff.assignment, membership=membership, days=1, reason="x")
    with pytest.raises(PermissionDenied):
        services.freeze_membership(
            by=staff.assignment, membership=membership, start=today(), days=1, reason="x"
        )


def test_a_branch_manager_can_extend(alpha, membership, add_staff):
    manager = add_staff(alpha.gym, "0788000005", Role.BRANCH_MANAGER)

    services.extend_membership(by=manager.assignment, membership=membership, days=2, reason="ok")


# --- Freezing ---------------------------------------------------------------


def test_a_freeze_refuses_entry_and_moves_the_end(alpha, membership):
    old_end = membership.end_date

    freeze = services.freeze_membership(
        by=alpha.assignment, membership=membership, start=today(), days=14, reason="Travelling"
    )

    assert freeze.end_date == days(13)
    assert reload(membership).end_date == old_end + timedelta(days=14)
    result = attendance.assess(membership.member)
    assert not result.allowed and result.reason == Reason.FROZEN
    assert result.frozen_until == days(13)
    with pytest.raises(attendance.NotAllowedIn):
        attendance.check_in(by=alpha.assignment, member=membership.member)
    assert AuditEntry.objects.for_gym(alpha.gym).filter(action="membership.frozen").exists()


def test_a_future_freeze_does_not_block_today(alpha, membership):
    services.freeze_membership(
        by=alpha.assignment, membership=membership, start=days(5), days=3, reason="Exams"
    )

    assert attendance.assess(membership.member).allowed
    assert attendance.assess(membership.member, on=days(6)).reason == Reason.FROZEN
    assert attendance.assess(membership.member, on=days(8)).allowed


@pytest.mark.parametrize(
    ("start", "length", "reason"),
    [
        (-1, 3, "out_of_range"),  # in the past
        (40, 3, "after_end"),  # after the membership ends
    ],
)
def test_freeze_dates_must_make_sense(alpha, membership, start, length, reason):
    with pytest.raises(InvalidDetails) as problem:
        services.freeze_membership(
            by=alpha.assignment, membership=membership, start=days(start), days=length, reason="x"
        )

    assert problem.value.reason == reason


def test_freezes_cannot_overlap_and_are_limited_to_90_days(alpha, membership):
    services.freeze_membership(
        by=alpha.assignment, membership=membership, start=days(2), days=5, reason="first"
    )

    with pytest.raises(InvalidDetails) as overlap:
        services.freeze_membership(
            by=alpha.assignment, membership=membership, start=days(4), days=2, reason="second"
        )
    with pytest.raises(InvalidDetails) as too_long:
        services.freeze_membership(
            by=alpha.assignment, membership=membership, start=days(10), days=91, reason="long"
        )

    assert overlap.value.reason == "overlaps" and too_long.value.field == "days"


# --- Ending a freeze --------------------------------------------------------


def test_ending_a_running_freeze_returns_the_unused_days(alpha, membership):
    original_end = membership.end_date
    freeze = services.freeze_membership(
        by=alpha.assignment, membership=membership, start=today(), days=10, reason="Injury"
    )
    # Pretend it started three days ago (tests cannot wait three days).
    MembershipFreeze.objects.filter(pk=freeze.pk).update(start_date=days(-3), end_date=days(6))
    Membership.objects.filter(pk=membership.pk).update(start_date=days(-10))
    freeze.refresh_from_db()

    services.end_freeze(by=alpha.assignment, freeze=freeze)

    freeze.refresh_from_db()
    assert freeze.end_date == days(-1)  # frozen until yesterday
    assert reload(membership).end_date == original_end + timedelta(days=3)  # 3 days used
    assert attendance.assess(membership.member).allowed


def test_calling_off_a_freeze_that_starts_today_gives_back_every_day(alpha, membership):
    original_end = membership.end_date
    freeze = services.freeze_membership(
        by=alpha.assignment, membership=membership, start=today(), days=10, reason="Changed mind"
    )

    services.end_freeze(by=alpha.assignment, freeze=freeze)

    freeze.refresh_from_db()
    assert freeze.cancelled and freeze.ended_early_at is not None
    assert reload(membership).end_date == original_end
    assert attendance.assess(membership.member).allowed  # can come in today
    assert services.freeze_on(reload(membership), today()) is None
    with pytest.raises(InvalidDetails):
        services.end_freeze(by=alpha.assignment, freeze=freeze)


def test_another_gyms_freeze_cannot_be_ended(alpha, membership, make_gym):
    freeze = services.freeze_membership(
        by=alpha.assignment, membership=membership, start=days(1), days=2, reason="x"
    )
    bravo = make_gym("Bravo Fitness", "0788000002")

    with pytest.raises(PermissionDenied):
        services.end_freeze(by=bravo.assignment, freeze=freeze)


# --- Screens ----------------------------------------------------------------


def test_freeze_and_extend_through_the_screens(alpha, membership, sign_in):
    client = sign_in(alpha)
    member_page = client.get(reverse("members:member_detail", args=[membership.member.public_id]))
    url = reverse("members:membership_adjust", args=[membership.public_id])
    assert url in member_page.content.decode()

    bad = client.post(url, {"action": "extend", "days": "400", "reason": "x"})
    assert bad.status_code == 200 and 'role="alert"' in bad.content.decode()

    client.post(url, {"action": "extend", "days": "3", "reason": "Power cut"})
    client.post(
        url,
        {"action": "freeze", "start": today().isoformat(), "days": "5", "reason": "Travelling"},
    )

    page = client.get(
        reverse("members:member_detail", args=[membership.member.public_id])
    ).content.decode()
    assert "Frozen until" in page and "Travelling" in page
    desk = client.get(reverse("attendance:home"), {"q": "eric"}).content.decode()
    assert "is frozen until" in desk
    assert reverse("attendance:check_in", args=[membership.member.public_id]) not in desk

    freeze = MembershipFreeze.objects.get()
    client.post(reverse("members:freeze_end", args=[freeze.public_id]))
    desk = client.get(reverse("attendance:home"), {"q": "eric"}).content.decode()
    assert reverse("attendance:check_in", args=[membership.member.public_id]) in desk


def test_a_receptionist_has_no_freeze_button_or_page(alpha, membership, add_staff, sign_in):
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST))
    url = reverse("members:membership_adjust", args=[membership.public_id])

    page = client.get(reverse("members:member_detail", args=[membership.member.public_id]))
    assert url not in page.content.decode()
    assert client.get(url).status_code == 403
