"""
What a member says they are doing when they come in.

A package lists the services it includes, so somebody who bought swimming
only cannot tick the gym. Where a service comes out of a book of prepaid
visits, one visit is taken straight away, so the count never drifts.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.attendance import services as attendance
from apps.attendance.models import CheckInActivity
from apps.frontdesk import services as frontdesk
from apps.gyms.models import Role
from apps.members import services as member_services
from apps.members.models import PackageKind
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


@pytest.fixture
def gym(make_gym):
    staff = make_gym("Umucyo Wellness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS):
        module_services.enable_module(gym=staff.gym, code=code)
    return staff


@pytest.fixture
def made(gym):
    """The services this gym sells, by name."""
    return {
        name: member_services.create_service(by=gym.assignment, name=name, price_rwf=price)
        for name, price in (("Gym", 5000), ("Swimming", 6000), ("Sauna", 7000))
    }


def member_with(gym, package, *, name="Grace Kamanzi", phone="0788111222"):
    member = member_services.register_member(
        by=gym.assignment, phone=phone, details=MemberDetails(full_name=name)
    )
    if package.is_count:
        member_services.create_ticketbook(by=gym.assignment, member=member, package=package)
    else:
        member_services.create_membership(
            by=gym.assignment,
            member=member,
            package=package,
            start_date=timezone.localdate(),
        )
    return member


def monthly(gym, name, services_included, price=50000):
    return member_services.create_package(
        by=gym.assignment,
        name=name,
        price_rwf=price,
        services=services_included,
        duration_count=1,
        duration_unit="month",
    )


# --- What a member may tick --------------------------------------------------


def test_a_swimming_membership_offers_swimming_and_nothing_else(gym, made):
    member = member_with(gym, monthly(gym, "Swimming only, monthly", [made["Swimming"]]))

    offered = [item.service.name for item in attendance.choices_for(member)]

    assert offered == ["Swimming"]


def test_a_combined_membership_offers_everything_it_includes(gym, made):
    package = monthly(gym, "Gym + swimming", [made["Gym"], made["Swimming"]], price=65000)
    member = member_with(gym, package)

    offered = [item.service.name for item in attendance.choices_for(member)]

    assert offered == ["Gym", "Swimming"]


def test_a_member_with_nothing_is_offered_nothing(gym, made):
    member = member_services.register_member(
        by=gym.assignment, phone="0788999888", details=MemberDetails(full_name="Nobody Yet")
    )

    assert attendance.choices_for(member) == []


# --- Writing it down ---------------------------------------------------------


def test_what_a_member_ticks_is_written_down(gym, made):
    package = monthly(gym, "Gym + swimming", [made["Gym"], made["Swimming"]])
    member = member_with(gym, package)
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)

    attendance.record_activities(
        by=gym.assignment,
        check_in=check_in,
        services_chosen=[made["Gym"], made["Swimming"]],
    )

    assert attendance.activities_of(check_in) == ["Gym", "Swimming"]


def test_a_service_the_package_does_not_include_is_refused(gym, made):
    member = member_with(gym, monthly(gym, "Swimming only", [made["Swimming"]]))
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)

    with pytest.raises(InvalidDetails) as raised:
        attendance.record_activities(
            by=gym.assignment, check_in=check_in, services_chosen=[made["Sauna"]]
        )

    assert (raised.value.field, raised.value.reason) == ("services", "not_included")
    assert not CheckInActivity.objects.exists()


def test_ticking_the_same_service_twice_records_it_once(gym, made):
    member = member_with(gym, monthly(gym, "Gym only", [made["Gym"]]))
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)

    attendance.record_activities(
        by=gym.assignment, check_in=check_in, services_chosen=[made["Gym"]]
    )
    attendance.record_activities(
        by=gym.assignment, check_in=check_in, services_chosen=[made["Gym"]]
    )

    assert check_in.activities.count() == 1


# --- Prepaid visits ----------------------------------------------------------


def test_using_a_prepaid_visit_takes_one_from_the_book(gym, made):
    tickets = member_services.create_package(
        by=gym.assignment,
        name="10 swims",
        price_rwf=40000,
        kind=PackageKind.COUNT,
        services=[made["Swimming"]],
        visit_count=10,
    )
    member = member_with(gym, tickets)
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)

    attendance.record_activities(
        by=gym.assignment, check_in=check_in, services_chosen=[made["Swimming"]]
    )

    book = member_services.ticketbooks_for(member).get()
    assert book.visits_left == 9
    assert check_in.activities.get().ticket_use is not None


def test_a_book_with_nothing_left_stops_offering_the_service(gym, made):
    tickets = member_services.create_package(
        by=gym.assignment,
        name="1 swim",
        price_rwf=5000,
        kind=PackageKind.COUNT,
        services=[made["Swimming"]],
        visit_count=1,
    )
    member = member_with(gym, tickets)
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)
    attendance.record_activities(
        by=gym.assignment, check_in=check_in, services_chosen=[made["Swimming"]]
    )

    assert attendance.choices_for(member) == []


# --- Putting a mistake right -------------------------------------------------


def test_a_service_ticked_by_mistake_is_taken_off_and_the_visit_comes_back(gym, made):
    tickets = member_services.create_package(
        by=gym.assignment,
        name="5 swims",
        price_rwf=20000,
        kind=PackageKind.COUNT,
        services=[made["Swimming"]],
        visit_count=5,
    )
    member = member_with(gym, tickets)
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)
    activity = attendance.record_activities(
        by=gym.assignment, check_in=check_in, services_chosen=[made["Swimming"]]
    )[0]

    attendance.cancel_activity(
        by=gym.assignment, activity=activity, reason="Ticked the wrong person"
    )

    activity.refresh_from_db()
    assert activity.cancelled_at is not None
    assert activity.cancel_reason == "Ticked the wrong person"
    assert attendance.activities_of(check_in) == []  # not shown any more
    assert check_in.activities.count() == 1  # but not deleted either
    assert member_services.ticketbooks_for(member).get().visits_left == 5


def test_taking_a_service_off_needs_a_reason(gym, made):
    member = member_with(gym, monthly(gym, "Gym only", [made["Gym"]]))
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)
    activity = attendance.record_activities(
        by=gym.assignment, check_in=check_in, services_chosen=[made["Gym"]]
    )[0]

    with pytest.raises(InvalidDetails) as raised:
        attendance.cancel_activity(by=gym.assignment, activity=activity, reason=" ")

    assert raised.value.field == "reason"


def test_a_visit_from_another_day_can_no_longer_be_changed(gym, made):
    member = member_with(gym, monthly(gym, "Gym only", [made["Gym"]]))
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)
    activity = attendance.record_activities(
        by=gym.assignment, check_in=check_in, services_chosen=[made["Gym"]]
    )[0]
    type(check_in).objects.filter(pk=check_in.pk).update(
        checked_in_on=timezone.localdate() - timedelta(days=1)
    )
    activity.refresh_from_db()

    with pytest.raises(InvalidDetails) as raised:
        attendance.cancel_activity(by=gym.assignment, activity=activity, reason="Too late")

    assert (raised.value.field, raised.value.reason) == ("activity", "too_late")


def test_another_gym_cannot_write_on_a_visit(gym, made, make_gym):
    member = member_with(gym, monthly(gym, "Gym only", [made["Gym"]]))
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)
    other = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")

    with pytest.raises(PermissionDenied):
        attendance.record_activities(
            by=other.assignment, check_in=check_in, services_chosen=[made["Gym"]]
        )


# --- The desk's screens ------------------------------------------------------


def test_the_desk_ticks_what_a_member_is_doing_while_checking_them_in(
    gym, made, add_staff, sign_in
):
    package = monthly(gym, "Gym + swimming", [made["Gym"], made["Swimming"]])
    member = member_with(gym, package)
    desk = add_staff(gym.gym, "0788000007", Role.RECEPTIONIST, "Gisele Mugwaneza")
    client = sign_in(desk)

    found = client.get(
        reverse("attendance:home"), {"q": "Grace"}, headers={"HX-Request": "true"}
    ).content.decode()
    assert "What are they doing today?" in found
    assert "Swimming" in found

    client.post(
        reverse("attendance:check_in", args=[member.public_id]),
        {"services": [str(made["Swimming"].public_id)]},
    )

    home = client.get(reverse("frontdesk:desk")).content.decode()
    assert "Grace Kamanzi" in home
    assert "Swimming" in home


def test_home_shows_what_each_member_used(gym, made, add_staff, sign_in):
    package = monthly(gym, "Gym + swimming", [made["Gym"], made["Swimming"]])
    member = member_with(gym, package)
    check_in, _new = attendance.check_in(by=gym.assignment, member=member)
    attendance.record_activities(
        by=gym.assignment,
        check_in=check_in,
        services_chosen=[made["Gym"], made["Swimming"]],
    )

    arrivals = frontdesk.arrivals(gym.gym, timezone.localdate())

    assert arrivals[0].name == "Grace Kamanzi"
    assert "Gym + Swimming" in arrivals[0].detail
    assert "Gym + swimming" in arrivals[0].detail  # the package name is there too
