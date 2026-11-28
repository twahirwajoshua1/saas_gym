"""
Selling at the door: somebody who is not a member, paying for one visit.

What matters here is that the money is right and nobody leaves owing. A walk-in
pays the whole amount, a massage is paid for before the time is held, and a
regular who comes back is the same person on the books rather than a new one
every week.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Invoice, Payment, ShiftClose
from apps.classes import services as classes
from apps.frontdesk import services
from apps.gyms.models import Branch, Role
from apps.massage import services as massage
from apps.massage.models import BookingStatus, MassageBooking
from apps.members import services as member_services
from apps.members.models import Member, Service
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE, TRAINING_CLASSES

pytestmark = pytest.mark.django_db

KIGALI = ZoneInfo("Africa/Kigali")


@pytest.fixture(autouse=True)
def fixed_clock():
    """A Monday at 09:00: free massage times depend on the hour."""
    with time_machine.travel(datetime(2027, 3, 1, 9, 0, tzinfo=KIGALI), tick=False):
        yield


@pytest.fixture
def gym(make_gym):
    staff = make_gym("Umucyo Wellness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, TRAINING_CLASSES):
        module_services.enable_module(gym=staff.gym, code=code)
    return staff


@pytest.fixture
def branch(gym):
    return Branch.objects.for_gym(gym.gym).first()


@pytest.fixture
def sauna(gym):
    return member_services.create_service(by=gym.assignment, name="Sauna", price_rwf=3000)


@pytest.fixture
def desk(gym, add_staff, branch):
    return add_staff(gym.gym, "0788000005", Role.RECEPTIONIST, "Gisele Mugwaneza", branch=branch)


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
    return massage.create_type(by=gym.assignment, name="Deep tissue", minutes=60, price_rwf=20000)


def key():
    import uuid

    return uuid.uuid4()


# --- A single visit sold at the desk -----------------------------------------


def test_a_walk_in_pays_for_a_sauna_and_owes_nothing(desk, sauna, branch):
    sale = services.sell_at_the_door(
        by=desk.assignment,
        full_name="Eric Habimana",
        phone="0788444555",
        service=sauna,
        method="cash",
        idempotency_key=key(),
        branch=branch,
    )

    assert sale.invoice.total_rwf == 3000
    assert sale.payment.amount_rwf == 3000
    assert billing.outstanding_on(sale.invoice) == 0
    assert sale.is_new is True


def test_a_walk_in_is_kept_on_the_books_but_joins_no_membership_list(desk, sauna, branch):
    """
    The gym needs a name against the receipt, so a plain record is kept. It is
    not a membership: they show in none of the active, ending-soon or lapsed
    lists, because they have not bought one.
    """
    sale = services.sell_at_the_door(
        by=desk.assignment,
        full_name="Eric Habimana",
        phone="0788444555",
        service=sauna,
        method="cash",
        idempotency_key=key(),
        branch=branch,
    )

    assert Member.objects.for_gym(desk.gym).count() == 1
    assert sale.member.memberships.count() == 0
    for state in member_services.MEMBER_STATES:
        _page, people = services.people_page(desk.gym, state=state)
        assert sale.member.full_name not in [person.full_name for person in people]


def test_a_regular_walk_in_is_the_same_person_each_time(desk, sauna, branch):
    first = services.sell_at_the_door(
        by=desk.assignment,
        full_name="Eric Habimana",
        phone="0788444555",
        service=sauna,
        method="cash",
        idempotency_key=key(),
        branch=branch,
    )
    again = services.sell_at_the_door(
        by=desk.assignment,
        full_name="Eric H",
        phone="078 844 4555",
        service=sauna,
        method="mtn_momo",
        idempotency_key=key(),
        branch=branch,
    )

    assert again.member.pk == first.member.pk
    assert again.is_new is False
    assert Member.objects.for_gym(desk.gym).count() == 1
    assert Payment.objects.for_gym(desk.gym).count() == 2


def test_a_bad_phone_number_sells_nothing(desk, sauna, branch):
    with pytest.raises(InvalidDetails) as raised:
        services.sell_at_the_door(
            by=desk.assignment,
            full_name="Eric Habimana",
            phone="12",
            service=sauna,
            method="cash",
            idempotency_key=key(),
            branch=branch,
        )

    assert (raised.value.field, raised.value.reason) == ("phone", "invalid")
    assert not Invoice.objects.for_gym(desk.gym).exists()
    assert not Member.objects.for_gym(desk.gym).exists()


def test_a_trainer_cannot_sell_at_the_door(gym, add_staff, sauna, branch):
    trainer = add_staff(gym.gym, "0788000013", Role.TRAINER)

    with pytest.raises(PermissionDenied):
        services.sell_at_the_door(
            by=trainer.assignment,
            full_name="Eric Habimana",
            phone="0788444555",
            service=sauna,
            method="cash",
            idempotency_key=key(),
            branch=branch,
        )


def test_another_gyms_service_cannot_be_sold_here(desk, make_gym, branch):
    other = make_gym("Kigali Fitness", "0788000002", "Jean Bosco")
    module_services.enable_module(gym=other.gym, code=PAYMENTS_FINANCE)
    theirs = member_services.create_service(by=other.assignment, name="Sauna", price_rwf=9000)

    with pytest.raises(PermissionDenied):
        services.sell_at_the_door(
            by=desk.assignment,
            full_name="Eric Habimana",
            phone="0788444555",
            service=theirs,
            method="cash",
            idempotency_key=key(),
            branch=branch,
        )

    # Nothing is half-done: the walk-in's record goes with the refused sale.
    assert not Member.objects.for_gym(desk.gym).exists()


def test_the_same_form_twice_sells_one_thing(desk, sauna, branch):
    same = key()
    for _attempt in range(2):
        services.sell_at_the_door(
            by=desk.assignment,
            full_name="Eric Habimana",
            phone="0788444555",
            service=sauna,
            method="cash",
            idempotency_key=same,
            branch=branch,
        )

    assert Invoice.objects.for_gym(desk.gym).count() == 1
    assert Payment.objects.for_gym(desk.gym).count() == 1


def test_a_door_sale_counts_in_todays_takings(desk, sauna, branch):
    services.sell_at_the_door(
        by=desk.assignment,
        full_name="Eric Habimana",
        phone="0788444555",
        service=sauna,
        method="cash",
        idempotency_key=key(),
        branch=branch,
    )

    assert billing.collected_on(desk.gym, timezone.localdate()) == 3000


# --- A massage for somebody who has walked in --------------------------------


def test_a_walk_in_massage_is_booked_and_paid_for_in_one_go(desk, claudine, deep_tissue, branch):
    sale = services.book_a_massage_at_the_door(
        by=desk.assignment,
        full_name="Eric Habimana",
        phone="0788444555",
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=timezone.localdate(),
        at=time(10, 0),
        method="cash",
        idempotency_key=key(),
    )

    assert sale.booking.status == BookingStatus.ACCEPTED
    assert sale.booking.masseuse_id == claudine.pk
    assert sale.payment.amount_rwf == 20000
    assert billing.outstanding_on(sale.invoice) == 0


def test_a_time_already_taken_books_nothing_and_takes_no_money(desk, claudine, deep_tissue, branch):
    """
    Between the desk reading a free time off the screen and pressing the
    button, somebody else may have taken it. Nothing is saved then: no money
    is taken for a massage that cannot happen.
    """
    member = member_services.register_member(
        by=desk.assignment, phone="0788111222", details=MemberDetails(full_name="Grace Kamanzi")
    )
    massage.book(
        by=desk.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=timezone.localdate(),
        at=time(10, 0),
        person=member.person,
        member=member,
    )

    with pytest.raises(InvalidDetails) as raised:
        services.book_a_massage_at_the_door(
            by=desk.assignment,
            full_name="Eric Habimana",
            phone="0788444555",
            massage_type=deep_tissue,
            masseuse=claudine,
            branch=branch,
            day=timezone.localdate(),
            at=time(10, 0),
            method="cash",
            idempotency_key=key(),
        )

    assert (raised.value.field, raised.value.reason) == ("starts_at", "taken")
    assert MassageBooking.objects.for_gym(desk.gym).count() == 1
    assert not Payment.objects.for_gym(desk.gym).exists()
    assert Member.objects.for_gym(desk.gym).filter(person__phone="+250788444555").count() == 0


def test_a_massage_is_not_in_the_list_of_things_sold_on_their_own(desk, sauna, deep_tissue):
    """
    A massage needs a masseuse and a time, so it is never sold off the plain
    list: it goes through the screen that shows the free times.
    """
    names = [service.name for service in services.door_services(desk.gym)]

    assert "Sauna" in names
    assert "Deep tissue" not in names


# --- The screens -------------------------------------------------------------


def test_the_desk_sells_a_sauna_from_the_screen(desk, sign_in, sauna, branch):
    client = sign_in(desk)

    response = client.post(
        reverse("frontdesk:door"),
        {
            "full_name": "Eric Habimana",
            "phone": "0788444555",
            "service": str(Service.objects.for_gym(desk.gym).get(name="Sauna").pk),
            "method": "cash",
            "idempotency_key": str(key()),
        },
        follow=True,
    )

    assert response.status_code == 200
    payment = Payment.objects.for_gym(desk.gym).get()
    assert payment.amount_rwf == 3000
    assert "Receipt" in response.content.decode()


def test_choosing_a_massage_shows_the_free_times_before_any_money(
    desk, sign_in, sauna, claudine, deep_tissue
):
    client = sign_in(desk)

    page = client.post(
        reverse("frontdesk:door"),
        {
            "full_name": "Eric Habimana",
            "phone": "0788444555",
            "massage_type": str(deep_tissue.pk),
            "method": "cash",
            "idempotency_key": str(key()),
        },
    ).content.decode()

    assert "Free times" in page
    assert "10:00" in page
    assert not Payment.objects.for_gym(desk.gym).exists()


def test_pressing_a_time_books_it_and_takes_the_money(desk, sign_in, sauna, claudine, deep_tissue):
    client = sign_in(desk)

    client.post(
        reverse("frontdesk:door_massage"),
        {
            "full_name": "Eric Habimana",
            "phone": "0788444555",
            "massage_type": str(deep_tissue.public_id),
            "method": "cash",
            "day": f"{timezone.localdate():%Y-%m-%d}",
            "at": "10:00",
            "idempotency_key": str(key()),
        },
        follow=True,
    )

    booking = MassageBooking.objects.for_gym(desk.gym).get()
    assert booking.starts_at == time(10, 0)
    assert Payment.objects.for_gym(desk.gym).get().amount_rwf == 20000


def test_a_day_with_nothing_free_says_so_rather_than_booking(
    desk, sign_in, sauna, deep_tissue, claudine
):
    client = sign_in(desk)
    far_off = timezone.localdate() + timedelta(days=2)

    page = client.post(
        reverse("frontdesk:door_massage"),
        {
            "full_name": "Eric Habimana",
            "phone": "0788444555",
            "massage_type": str(deep_tissue.public_id),
            "method": "cash",
            "day": f"{far_off:%Y-%m-%d}",
            "idempotency_key": str(key()),
        },
    ).content.decode()

    assert "Free times" in page or "Nothing is free" in page
    assert not Payment.objects.for_gym(desk.gym).exists()


# --- Handing the desk over ---------------------------------------------------


def test_a_closed_shift_leaves_open_work_open(desk, sign_in, claudine, deep_tissue, branch, gym):
    """
    Somebody going home at seven cannot be kept there by a massage request
    nobody has answered. Closing the shift does not answer it, cancel it or
    write it off: it is still there for the next person.
    """
    member = member_services.register_member(
        by=desk.assignment, phone="0788111222", details=MemberDetails(full_name="Grace Kamanzi")
    )
    asked = massage.book(
        by=desk.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=timezone.localdate(),
        at=time(11, 0),
        person=member.person,
        member=member,
        accepted=False,
    )

    closed = services.close_shift(by=desk.assignment, branch=branch)

    asked.refresh_from_db()
    assert asked.status == BookingStatus.REQUESTED
    assert closed.left_massages == 1
    assert closed.left_open == 1


def test_what_a_shift_hands_on_is_counted_for_the_owner(gym, desk, sauna, branch):
    package = member_services.create_package(
        by=gym.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )
    member_services.create_signup(
        desk.gym, full_name="Jean Paul", phone="0788777888", package=package, branch=branch
    )

    closed = services.close_shift(by=desk.assignment, branch=branch)

    assert closed.left_signups == 1


def test_a_quiet_shift_hands_on_nothing(desk, sauna, branch):
    services.sell_at_the_door(
        by=desk.assignment,
        full_name="Eric Habimana",
        phone="0788444555",
        service=sauna,
        method="cash",
        idempotency_key=key(),
        branch=branch,
    )

    closed = services.close_shift(by=desk.assignment, branch=branch)

    assert closed.left_open == 0
    assert closed.cash_rwf == 3000


def test_the_shift_cannot_be_closed_without_reading_what_is_open(
    desk, sign_in, claudine, deep_tissue, branch
):
    member = member_services.register_member(
        by=desk.assignment, phone="0788111222", details=MemberDetails(full_name="Grace Kamanzi")
    )
    massage.book(
        by=desk.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=timezone.localdate(),
        at=time(11, 0),
        person=member.person,
        member=member,
        accepted=False,
    )
    client = sign_in(desk)

    page = client.post(reverse("frontdesk:my_shift"), {}, follow=True).content.decode()

    assert "tick the box" in page
    assert not ShiftClose.objects.for_gym(desk.gym).exists()

    client.post(reverse("frontdesk:my_shift"), {"understood": "on"})

    assert ShiftClose.objects.for_gym(desk.gym).count() == 1


def test_the_shift_page_names_what_is_waiting(desk, sign_in, claudine, deep_tissue, branch):
    member = member_services.register_member(
        by=desk.assignment, phone="0788111222", details=MemberDetails(full_name="Grace Kamanzi")
    )
    massage.book(
        by=desk.assignment,
        massage_type=deep_tissue,
        masseuse=claudine,
        branch=branch,
        day=timezone.localdate(),
        at=time(11, 0),
        person=member.person,
        member=member,
        accepted=False,
    )

    page = sign_in(desk).get(reverse("frontdesk:my_shift")).content.decode()

    assert "Still open" in page
    assert "Grace Kamanzi" in page
    assert "not answered" in page
