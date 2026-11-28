"""
Massage: types, free times, booking, calling off, and the masseuse's notes.

The rules that matter here are the ones that cost somebody money or privacy:
two people can never hold the same slot; a company pays the price it agreed;
calling off late is still charged; and a masseuse's note is hers alone.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.classes import services as classes
from apps.corporate import services as corp
from apps.corporate.services import ContractTerms, EmployeeDetails
from apps.gyms.models import Branch, Role
from apps.massage import services
from apps.massage.models import BookingStatus, MassageBooking
from apps.members import services as member_services
from apps.members.models import Service
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import (
    ATTENDANCE_ACCESS,
    CORPORATE_WELLNESS,
    PAYMENTS_FINANCE,
    TRAINING_CLASSES,
)

pytestmark = pytest.mark.django_db

KIGALI = ZoneInfo("Africa/Kigali")


@pytest.fixture(autouse=True)
def fixed_clock():
    """
    A Monday morning at 09:00. Free times, cut-offs and "today" all depend on
    the clock, so these tests hold it still rather than hoping for a good hour.
    """
    with time_machine.travel(datetime(2027, 3, 1, 9, 0, tzinfo=KIGALI), tick=False):
        yield


@pytest.fixture
def gym(make_gym):
    staff = make_gym("Umucyo Wellness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, TRAINING_CLASSES, CORPORATE_WELLNESS):
        module_services.enable_module(gym=staff.gym, code=code)
    member_services.create_service(by=staff.assignment, name="Gym", price_rwf=5000)
    return staff


@pytest.fixture
def branch(gym):
    return Branch.objects.for_gym(gym.gym).first()


@pytest.fixture
def claudine(gym, branch, add_staff):
    staff = add_staff(gym.gym, "0788000011", Role.TRAINER, "Claudine Uwase")
    profile = classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=True)
    classes.set_trainer_hours(
        by=gym.assignment,
        trainer=profile,
        windows=[(day, time(8, 0), time(18, 0), branch) for day in range(7)],
    )
    return profile


@pytest.fixture
def deep_tissue(gym):
    return services.create_type(by=gym.assignment, name="Deep tissue", minutes=60, price_rwf=20000)


@pytest.fixture
def member(gym):
    return member_services.register_member(
        by=gym.assignment,
        phone="0788111222",
        details=MemberDetails(full_name="Grace Kamanzi"),
    )


def tomorrow():
    return timezone.localdate() + timedelta(days=1)


# --- The types a gym offers --------------------------------------------------


def test_a_massage_type_is_also_a_service_so_companies_can_price_it(gym, deep_tissue):
    assert deep_tissue.minutes == 60
    assert deep_tissue.price_rwf == 20000
    assert Service.objects.for_gym(gym.gym).filter(name="Deep tissue").exists()


def test_two_massages_cannot_share_a_name(gym, deep_tissue):
    with pytest.raises(InvalidDetails) as raised:
        services.create_type(by=gym.assignment, name="deep tissue", minutes=30, price_rwf=1000)

    assert (raised.value.field, raised.value.reason) == ("name", "duplicate")


def test_only_the_owner_sets_the_types(gym, add_staff):
    desk = add_staff(gym.gym, "0788000007", Role.RECEPTIONIST)

    with pytest.raises(PermissionDenied):
        services.create_type(by=desk.assignment, name="Swedish", minutes=30, price_rwf=12000)


def test_stopping_a_massage_stops_selling_the_service_too(gym, deep_tissue):
    services.set_type_active(by=gym.assignment, massage_type=deep_tissue, active=False)

    deep_tissue.refresh_from_db()
    deep_tissue.service.refresh_from_db()
    assert deep_tissue.is_active is False
    assert deep_tissue.service.is_active is False


# --- Free times --------------------------------------------------------------


def test_free_times_follow_the_masseuses_hours_and_the_length(gym, branch, claudine, deep_tissue):
    slots = services.free_slots(gym.gym, branch=branch, massage_type=deep_tissue, day=tomorrow())

    assert slots[0].at == time(8, 0)
    # She works until 18:00 and a deep tissue takes an hour, so 17:00 is the last.
    assert slots[-1].at == time(17, 0)


def test_a_booked_time_and_the_break_after_it_are_not_offered_again(
    gym, branch, claudine, deep_tissue, member
):
    day = tomorrow()
    services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=day,
        at=time(10, 0),
        person=member.person,
        member=member,
    )

    offered = {
        slot.at
        for slot in services.free_slots(gym.gym, branch=branch, massage_type=deep_tissue, day=day)
    }

    assert time(10, 0) not in offered  # taken
    assert time(10, 30) not in offered  # still in the massage
    assert time(9, 30) not in offered  # would run into it
    assert time(11, 0) not in offered  # inside the ten-minute break
    assert time(11, 30) in offered


def test_a_shorter_massage_fits_where_a_longer_one_does_not(gym, branch, claudine, deep_tissue):
    swedish = services.create_type(by=gym.assignment, name="Swedish", minutes=30, price_rwf=12000)

    long_ones = {
        s.at
        for s in services.free_slots(
            gym.gym, branch=branch, massage_type=deep_tissue, day=tomorrow()
        )
    }
    short_ones = {
        s.at
        for s in services.free_slots(gym.gym, branch=branch, massage_type=swedish, day=tomorrow())
    }

    assert time(17, 30) in short_ones and time(17, 30) not in long_ones


def test_nobody_is_offered_when_no_masseuse_works_that_day(gym, branch, deep_tissue, add_staff):
    staff = add_staff(gym.gym, "0788000012", Role.TRAINER, "Eric Niyonsaba")
    classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=True)

    assert (
        services.free_slots(gym.gym, branch=branch, massage_type=deep_tissue, day=tomorrow()) == []
    )


# --- Booking -----------------------------------------------------------------


def test_two_people_cannot_hold_the_same_slot(gym, branch, claudine, deep_tissue, member):
    day = tomorrow()
    other = member_services.register_member(
        by=gym.assignment,
        phone="0788333444",
        details=MemberDetails(full_name="Alice Mutoni"),
    )
    services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=day,
        at=time(9, 0),
        person=member.person,
        member=member,
    )

    with pytest.raises(InvalidDetails) as raised:
        services.book(
            by=gym.assignment,
            massage_type=deep_tissue,
            masseuse=claudine,
            branch=branch,
            day=day,
            at=time(9, 0),
            person=other.person,
            member=other,
        )

    assert (raised.value.field, raised.value.reason) == ("starts_at", "taken")


def test_a_request_does_not_hold_the_slot_until_it_is_accepted(
    gym, branch, claudine, deep_tissue, member
):
    day = tomorrow()
    asked = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=day,
        at=time(9, 0),
        person=member.person,
        member=member,
        accepted=False,
    )

    assert asked.status == BookingStatus.REQUESTED
    offered = {
        s.at for s in services.free_slots(gym.gym, branch=branch, massage_type=deep_tissue, day=day)
    }
    assert time(9, 0) in offered  # still anybody's

    services.accept(by=gym.assignment, booking=asked)
    asked.refresh_from_db()
    assert asked.status == BookingStatus.ACCEPTED
    offered = {
        s.at for s in services.free_slots(gym.gym, branch=branch, massage_type=deep_tissue, day=day)
    }
    assert time(9, 0) not in offered


def test_a_request_can_be_declined_with_a_reason(gym, branch, claudine, deep_tissue, member):
    asked = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=member.person,
        member=member,
        accepted=False,
    )

    services.decline(by=gym.assignment, booking=asked, reason="Claudine is away")

    asked.refresh_from_db()
    assert asked.status == BookingStatus.DECLINED
    assert asked.cancel_reason == "Claudine is away"


def test_the_closest_free_times_are_offered_when_one_has_gone(
    gym, branch, claudine, deep_tissue, member
):
    day = tomorrow()
    services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=day,
        at=time(10, 0),
        person=member.person,
        member=member,
    )

    nearest = services.nearest_free(
        gym.gym, branch=branch, massage_type=deep_tissue, day=day, wanted=time(10, 0)
    )

    assert len(nearest) == 3
    assert time(10, 0) not in [slot.at for slot in nearest]


# --- Who pays ----------------------------------------------------------------


def company_with(gym, branch, prices, phone="0788900001"):
    created = corp.create_company(
        name="WASAC", tin="", officer_name="WASAC officer", officer_phone=phone, actor=None
    )
    contract = corp.propose_contract(
        by=gym.assignment,
        company_code=created.company.code,
        terms=ContractTerms(
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=365),
            payment_days=30,
            prices={(service.id, branch.id): price for service, price in prices.items()},
        ),
    )
    corp.decide_contract(by=created.officer, contract=contract, approve=True)
    employee = corp.add_employee(
        by=created.officer,
        details=EmployeeDetails(
            full_name="Jean Claude Habumugisha",
            phone="0788555666",
            employee_number="W-0142",
            department="Finance",
        ),
    )
    return created, employee


def test_a_company_employee_is_charged_the_price_their_company_agreed(
    gym, branch, claudine, deep_tissue
):
    gym_service = Service.objects.for_gym(gym.gym).get(name="Gym")
    _created, employee = company_with(gym, branch, {gym_service: 4000, deep_tissue.service: 16000})

    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=employee.person,
        employee=employee,
    )

    assert booking.price_rwf == 16000  # not the 20,000 door price
    assert booking.paid_by == "WASAC"


def test_a_massage_the_contract_does_not_cover_is_paid_by_the_employee(
    gym, branch, claudine, deep_tissue
):
    gym_service = Service.objects.for_gym(gym.gym).get(name="Gym")
    _created, employee = company_with(gym, branch, {gym_service: 4000})

    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=employee.person,
        employee=employee,
    )

    assert booking.price_rwf == 20000  # the door price
    assert booking.paid_by == ""  # nobody else is paying


# --- Calling off -------------------------------------------------------------


def test_calling_off_in_good_time_costs_nothing(gym, branch, claudine, deep_tissue, member):
    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=member.person,
        member=member,
    )

    services.cancel(by=gym.assignment, booking=booking, reason="Asked to move it")

    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED
    assert booking.charged_anyway is False


def test_calling_off_after_the_cut_off_is_still_charged(gym, branch, claudine, deep_tissue, member):
    """
    The masseuse held the hour and turned other people away, so the slot is
    paid for whether or not anybody comes.
    """
    # It is 09:00 and the cut-off is two hours, so 10:30 is already too late.
    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=timezone.localdate(),
        at=time(10, 30),
        person=member.person,
        member=member,
    )

    services.cancel(by=gym.assignment, booking=booking, reason="Did not turn up")

    booking.refresh_from_db()
    assert booking.status == BookingStatus.NO_SHOW
    assert booking.charged_anyway is True
    assert booking.price_rwf == 20000


def test_calling_off_needs_a_reason(gym, branch, claudine, deep_tissue, member):
    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=member.person,
        member=member,
    )

    with pytest.raises(InvalidDetails) as raised:
        services.cancel(by=gym.assignment, booking=booking, reason="   ")

    assert raised.value.field == "reason"


# --- The masseuse's note -----------------------------------------------------


def test_only_the_masseuse_who_gave_the_massage_may_write_the_note(
    gym, branch, claudine, deep_tissue, member, add_staff
):
    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=member.person,
        member=member,
    )
    desk = add_staff(gym.gym, "0788000007", Role.RECEPTIONIST, "Gisele")

    with pytest.raises(PermissionDenied):
        services.write_note(by=desk.assignment, booking=booking, note="Nosy")
    with pytest.raises(PermissionDenied):
        services.write_note(by=gym.assignment, booking=booking, note="Also nosy")

    services.write_note(by=claudine.staff, booking=booking, note="Tight shoulders")
    booking.refresh_from_db()
    assert booking.note == "Tight shoulders"


def test_an_empty_note_leaves_nothing_behind(gym, branch, claudine, deep_tissue, member):
    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=member.person,
        member=member,
    )

    services.write_note(by=claudine.staff, booking=booking, note="   ")

    booking.refresh_from_db()
    assert booking.note == ""
    assert booking.note_at is None


# --- Keeping gyms apart ------------------------------------------------------


def test_another_gym_cannot_touch_a_booking(gym, branch, claudine, deep_tissue, member, make_gym):
    other = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")
    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(9, 0),
        person=member.person,
        member=member,
    )

    with pytest.raises(PermissionDenied):
        services.cancel(by=other.assignment, booking=booking, reason="Mischief")
    with pytest.raises(PermissionDenied):
        services.accept(by=other.assignment, booking=booking)


# --- The screens -------------------------------------------------------------


def test_the_desk_sees_the_diary_and_the_requests_waiting(
    gym, branch, claudine, deep_tissue, member, add_staff, sign_in
):
    desk = add_staff(gym.gym, "0788000007", Role.RECEPTIONIST, "Gisele Mugwaneza")
    day = tomorrow()
    services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=day,
        at=time(9, 0),
        person=member.person,
        member=member,
        accepted=False,
    )

    page = sign_in(desk).get("/gym/massage/", {"day": day.isoformat()}).content.decode()

    assert "Request waiting (1)" in page
    assert "Grace Kamanzi" in page and "Deep tissue" in page
    assert "Accept" in page and "Suggest another time" in page


def test_the_masseuse_sees_her_own_day_and_nobody_elses(
    gym, branch, claudine, deep_tissue, member, sign_in
):
    services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=timezone.localdate(),
        at=time(10, 30),
        person=member.person,
        member=member,
    )
    from apps.accounts.models import User

    user = User.objects.get(person=claudine.staff.person)
    user.must_change_password = False
    user.save()

    from django.test import Client
    from django.urls import reverse

    client = Client()
    client.force_login(user)
    client.get(reverse("gyms:enter"))
    page = client.get("/gym/my-day/").content.decode()

    assert "My day" in page and "Grace Kamanzi" in page


# --- The masseuse's own home --------------------------------------------------


def done_massage(gym, branch, masseuse, deep_tissue, person_member, *, days_ago):
    """A massage that actually happened, however long ago."""
    held = timezone.localdate() - timedelta(days=days_ago)
    return MassageBooking.objects.create(
        gym=gym.gym,
        branch=branch,
        masseuse=masseuse,
        person=person_member.person,
        member=person_member,
        massage_type=deep_tissue,
        type_name=deep_tissue.name,
        minutes=deep_tissue.minutes,
        price_rwf=deep_tissue.price_rwf,
        held_on=held,
        starts_at=time(10, 0),
        status=BookingStatus.DONE,
    )


def a_member(gym, name, phone):
    return member_services.register_member(
        by=gym.assignment, phone=phone, details=MemberDetails(full_name=name)
    )


def test_a_client_who_stopped_coming_is_shown_to_her_masseuse(gym, branch, claudine, deep_tissue):
    grace = a_member(gym, "Grace Kamanzi", "0788111222")
    done_massage(gym, branch, claudine, deep_tissue, grace, days_ago=70)

    lapsed = services.lapsed_clients(claudine)

    assert [client.full_name for client in lapsed] == ["Grace Kamanzi"]
    assert lapsed[0].weeks_away == 10


def test_a_client_seen_last_week_is_not_chased(gym, branch, claudine, deep_tissue):
    grace = a_member(gym, "Grace Kamanzi", "0788111222")
    done_massage(gym, branch, claudine, deep_tissue, grace, days_ago=7)

    assert services.lapsed_clients(claudine) == []


def test_a_client_with_a_massage_already_booked_is_left_alone(gym, branch, claudine, deep_tissue):
    """She is about to see them. Chasing them would be embarrassing."""
    grace = a_member(gym, "Grace Kamanzi", "0788111222")
    done_massage(gym, branch, claudine, deep_tissue, grace, days_ago=70)
    services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(10, 0),
        person=grace.person,
        member=grace,
    )

    assert services.lapsed_clients(claudine) == []


def test_somebody_who_only_ever_called_off_was_never_her_client(gym, branch, claudine, deep_tissue):
    grace = a_member(gym, "Grace Kamanzi", "0788111222")
    booking = services.book(
        by=gym.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=tomorrow(),
        at=time(10, 0),
        person=grace.person,
        member=grace,
    )
    services.cancel(by=gym.assignment, booking=booking, reason="ill")

    assert services.lapsed_clients(claudine) == []


def test_she_is_shown_her_own_clients_and_not_another_masseuses(
    gym, branch, claudine, deep_tissue, add_staff
):
    other = add_staff(gym.gym, "0788000099", Role.TRAINER, "Alice Mugisha")
    alice = classes.save_trainer(by=gym.assignment, staff=other.assignment, gives_massages=True)
    grace = a_member(gym, "Grace Kamanzi", "0788111222")
    done_massage(gym, branch, alice, deep_tissue, grace, days_ago=70)

    assert services.lapsed_clients(claudine) == []
    assert [client.full_name for client in services.lapsed_clients(alice)] == ["Grace Kamanzi"]


def test_the_list_stays_short_enough_to_act_on(gym, branch, claudine, deep_tissue):
    for number in range(14):
        person = a_member(gym, f"Client {number}", f"078820{number:04d}")
        done_massage(gym, branch, claudine, deep_tissue, person, days_ago=70 + number)

    lapsed = services.lapsed_clients(claudine)

    assert len(lapsed) == services.LAPSED_MOST
    # Longest away first, so the ones most likely to be lost come up.
    assert lapsed[0].weeks_away >= lapsed[-1].weeks_away


def test_the_message_is_written_for_her_to_send_herself(gym, branch, claudine, deep_tissue):
    grace = a_member(gym, "Grace Kamanzi", "0788111222")
    done_massage(gym, branch, claudine, deep_tissue, grace, days_ago=70)
    client = services.lapsed_clients(claudine)[0]

    text = services.come_back_message(gym.gym, client)

    assert "Grace" in text
    assert gym.gym.name in text


# --- Who may see massage at all -----------------------------------------------


def test_a_trainer_who_only_takes_classes_is_kept_out_of_the_diary(gym, add_staff, sign_in, branch):
    staff = add_staff(gym.gym, "0788000077", Role.TRAINER, "Kevin Bizimana")
    classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=False)

    response = sign_in(staff).get(reverse("massage:diary"))

    assert response.status_code == 403


def test_a_masseuse_reaches_her_own_day(gym, add_staff, sign_in, branch):
    staff = add_staff(gym.gym, "0788000079", Role.TRAINER, "Olivier Uwase")
    classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=True)

    response = sign_in(staff).get(reverse("massage:my_day"))

    assert response.status_code == 200


def test_a_class_only_trainer_is_sent_to_their_timetable_instead(gym, add_staff, sign_in):
    staff = add_staff(gym.gym, "0788000078", Role.TRAINER, "Kevin Bizimana")
    classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=False)

    response = sign_in(staff).get(reverse("massage:my_day"))

    assert response.status_code == 302
    assert response.url == reverse("classes:timetable")


def test_a_trainer_lands_on_their_own_page_not_the_receptionists_desk(gym, add_staff, sign_in):
    """
    The desk page shows arrivals and follow-ups, which are the receptionist's
    job and nobody else's.
    """
    staff = add_staff(gym.gym, "0788000080", Role.TRAINER, "Olivier Uwase")
    classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=True)

    response = sign_in(staff).get(reverse("gyms:home"))

    assert response.status_code == 302
    assert response.url == reverse("massage:my_day")


def test_a_masseuse_is_not_shown_the_desk_in_her_menu(gym, add_staff, sign_in, branch):
    """
    Who arrived and who to chase is the receptionist's work. Her menu should
    offer her own day, not somebody else's job.
    """
    staff = add_staff(gym.gym, "0788000081", Role.TRAINER, "Olivier Uwase")
    classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=True)

    page = sign_in(staff).get(reverse("massage:my_day")).content.decode()

    assert reverse("frontdesk:desk") not in page
    assert reverse("massage:my_day") in page


def test_even_at_its_address_the_desk_shows_a_trainer_no_arrivals(gym, add_staff, sign_in, branch):
    staff = add_staff(gym.gym, "0788000082", Role.TRAINER, "Olivier Uwase")
    classes.save_trainer(by=gym.assignment, staff=staff.assignment, gives_massages=True)

    page = sign_in(staff).get(reverse("frontdesk:desk")).content.decode()

    assert "Checked in today" not in page
    assert "Reach out today" not in page
