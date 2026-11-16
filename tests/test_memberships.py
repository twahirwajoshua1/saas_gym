"""Tests for membership dates, status and the rules for creating one."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.gyms.models import Role
from apps.members import services
from apps.members.durations import add_months, end_date_for
from apps.members.models import Membership, MembershipStatus
from apps.members.services import MemberDetails


@pytest.mark.parametrize(
    ("start", "count", "unit", "expected_end"),
    [
        (date(2026, 11, 16), 1, "day", date(2026, 11, 16)),  # a day pass ends the same day
        (date(2026, 11, 16), 3, "day", date(2026, 11, 18)),
        (date(2026, 11, 16), 1, "week", date(2026, 11, 22)),
        (date(2026, 11, 16), 1, "month", date(2026, 12, 15)),
        (date(2026, 11, 1), 1, "month", date(2026, 11, 30)),
        (date(2026, 11, 16), 3, "month", date(2027, 2, 15)),  # crosses the new year
        (date(2026, 1, 31), 1, "month", date(2026, 2, 28)),  # February is shorter
        (date(2026, 1, 30), 1, "month", date(2026, 2, 28)),
        (date(2028, 1, 31), 1, "month", date(2028, 2, 29)),  # leap year
        (date(2026, 12, 31), 2, "month", date(2027, 2, 28)),
        (date(2026, 11, 16), 1, "year", date(2027, 11, 15)),
        (date(2028, 2, 29), 1, "year", date(2029, 2, 28)),  # from a leap day
        (date(2026, 11, 16), 12, "month", date(2027, 11, 15)),  # 12 months equals 1 year
    ],
)
def test_the_last_day_of_a_membership(start, count, unit, expected_end):
    assert end_date_for(start, count, unit) == expected_end


def test_add_months_keeps_the_day_or_uses_the_months_last_day():
    assert add_months(date(2026, 3, 15), 1) == date(2026, 4, 15)
    assert add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_status_is_worked_out_from_the_dates():
    membership = Membership(start_date=date(2026, 11, 10), end_date=date(2026, 12, 9))

    assert membership.status_on(date(2026, 11, 9)) == MembershipStatus.UPCOMING
    assert membership.status_on(date(2026, 11, 10)) == MembershipStatus.ACTIVE
    assert membership.status_on(date(2026, 12, 9)) == MembershipStatus.ACTIVE  # last day counts
    assert membership.status_on(date(2026, 12, 10)) == MembershipStatus.EXPIRED

    membership.cancelled_at = timezone.now()
    assert membership.status_on(date(2026, 11, 20)) == MembershipStatus.CANCELLED


# --- Creating ---------------------------------------------------------------


@pytest.fixture
def alpha(make_gym):
    return make_gym("Alpha Fitness", "0788000001", "Aline Uwase")


@pytest.fixture
def member(alpha):
    return services.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric Mugisha")
    )


@pytest.fixture
def monthly(alpha):
    return services.create_package(
        by=alpha.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )


def today():
    return timezone.localdate()


@pytest.mark.django_db
def test_a_membership_copies_the_package_and_later_changes_do_not_touch_it(alpha, member, monthly):
    membership = services.create_membership(
        by=alpha.assignment, member=member, package=monthly, start_date=today()
    )
    services.update_package(
        by=alpha.assignment,
        package=monthly,
        name="Monthly Plus",
        price_rwf=45000,
        duration_count=2,
        duration_unit="month",
    )

    membership.refresh_from_db()
    assert (membership.package_name, membership.price_rwf) == ("Monthly", 30000)
    assert membership.end_date == end_date_for(today(), 1, "month")
    assert membership.status == MembershipStatus.ACTIVE
    assert services.current_membership(member) == membership


@pytest.mark.django_db
def test_memberships_cannot_overlap_and_a_renewal_starts_the_day_after(alpha, member, monthly):
    first = services.create_membership(
        by=alpha.assignment, member=member, package=monthly, start_date=today()
    )

    with pytest.raises(services.InvalidDetails) as problem:
        services.create_membership(
            by=alpha.assignment, member=member, package=monthly, start_date=first.end_date
        )
    assert (problem.value.field, problem.value.reason) == ("start_date", "overlaps")

    assert services.next_start_date(member) == first.end_date + timedelta(days=1)
    renewal = services.create_membership(
        by=alpha.assignment,
        member=member,
        package=monthly,
        start_date=services.next_start_date(member),
    )
    assert renewal.status == MembershipStatus.UPCOMING
    assert services.current_membership(member) == first


@pytest.mark.django_db
def test_next_start_is_today_for_a_new_member_or_after_an_expired_membership(
    alpha, member, monthly
):
    assert services.next_start_date(member) == today()

    old = services.create_membership(
        by=alpha.assignment, member=member, package=monthly, start_date=today()
    )
    Membership.objects.filter(pk=old.pk).update(
        start_date=today() - timedelta(days=60), end_date=today() - timedelta(days=30)
    )

    assert services.current_membership(member) is None
    assert services.next_start_date(member) == today()


@pytest.mark.django_db
@pytest.mark.parametrize("days_from_today", [-32, 367])
def test_start_dates_too_far_from_today_are_refused(alpha, member, monthly, days_from_today):
    with pytest.raises(services.InvalidDetails) as problem:
        services.create_membership(
            by=alpha.assignment,
            member=member,
            package=monthly,
            start_date=today() + timedelta(days=days_from_today),
        )
    assert (problem.value.field, problem.value.reason) == ("start_date", "out_of_range")


@pytest.mark.django_db
def test_a_sale_can_be_recorded_a_few_days_late(alpha, member, monthly):
    membership = services.create_membership(
        by=alpha.assignment, member=member, package=monthly, start_date=today() - timedelta(days=3)
    )
    assert membership.status == MembershipStatus.ACTIVE


@pytest.mark.django_db
def test_archived_packages_and_other_gyms_records_are_refused(alpha, member, monthly, make_gym):
    services.set_package_active(by=alpha.assignment, package=monthly, active=False)
    with pytest.raises(services.InvalidDetails):
        services.create_membership(
            by=alpha.assignment, member=member, package=monthly, start_date=today()
        )

    bravo = make_gym("Bravo Fitness", "0788000002")
    their_package = services.create_package(
        by=bravo.assignment, name="Monthly", price_rwf=1, duration_count=1, duration_unit="month"
    )
    with pytest.raises(PermissionDenied):
        services.create_membership(
            by=alpha.assignment, member=member, package=their_package, start_date=today()
        )
    with pytest.raises(PermissionDenied):
        services.create_membership(
            by=bravo.assignment, member=member, package=their_package, start_date=today()
        )


@pytest.mark.django_db
def test_a_trainer_cannot_create_memberships(alpha, member, monthly, add_staff):
    trainer = add_staff(alpha.gym, "0788000005", Role.TRAINER)

    with pytest.raises(PermissionDenied):
        services.create_membership(
            by=trainer.assignment, member=member, package=monthly, start_date=today()
        )
