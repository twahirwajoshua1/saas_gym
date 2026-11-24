"""
Classes, trainers, booking and reviews.

The rules that matter: a class cannot be over-filled, booking closes before it
starts, only someone who came may review the trainer, reviews are public but
can be hidden with a reason, and a trainer's free hours cannot be
double-booked.
"""

from datetime import time, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.audit.models import AuditEntry
from apps.classes import services
from apps.classes.models import Booking, ClassSession, Review
from apps.gyms.models import Branch, Role
from apps.members import services as members
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db

TOMORROW_AT = time(18, 0)


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS):
        module_services.enable_module(gym=staff.gym, code=code)
    return staff


@pytest.fixture
def branch(alpha):
    return Branch.objects.for_gym(alpha.gym).first()


@pytest.fixture
def trainer(alpha, add_staff):
    staff = add_staff(alpha.gym, "0788000070", Role.TRAINER, "Eric Habimana")
    return services.save_trainer(
        by=alpha.assignment,
        staff=staff.assignment,
        headline="Strength and conditioning",
        about="Ten years coaching. Expect hard work and good form.",
    )


@pytest.fixture
def aerobics(alpha):
    return services.save_class_type(
        by=alpha.assignment,
        name="Aerobics",
        what_to_expect="An hour of movement to music. Bring water and a towel.",
        minutes=60,
        places=2,
    )


@pytest.fixture
def make_member(alpha):
    def make(name="Solange Keza", phone="0788111222"):
        return members.register_member(
            by=alpha.assignment, phone=phone, details=MemberDetails(full_name=name)
        )

    return make


@pytest.fixture
def tomorrow():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def session(alpha, aerobics, branch, trainer, tomorrow):
    made = services.add_to_timetable(
        by=alpha.assignment,
        class_type=aerobics,
        branch=branch,
        first_day=tomorrow,
        at=TOMORROW_AT,
        trainer=trainer,
    )
    return made[0]


# --- What the gym teaches ---------------------------------------------------


def test_only_a_manager_writes_what_the_gym_teaches(alpha, add_staff):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    with pytest.raises(PermissionDenied):
        services.save_class_type(by=desk.assignment, name="Spinning")


def test_two_classes_cannot_share_a_name(alpha, aerobics):
    with pytest.raises(InvalidDetails) as error:
        services.save_class_type(by=alpha.assignment, name="aerobics")
    assert error.value.field == "name"


def test_an_archived_class_keeps_its_past(alpha, aerobics, session):
    services.archive_class_type(by=alpha.assignment, class_type=aerobics)
    assert list(services.list_class_types(alpha.gym)) == []
    session.refresh_from_db()
    assert session.class_type.name == "Aerobics"


# --- The timetable ----------------------------------------------------------


def test_a_class_can_repeat_weekly_without_doubling_up(alpha, aerobics, branch, trainer, tomorrow):
    first = services.add_to_timetable(
        by=alpha.assignment,
        class_type=aerobics,
        branch=branch,
        first_day=tomorrow,
        at=TOMORROW_AT,
        trainer=trainer,
        repeat_weeks=4,
    )
    assert len(first) == 4
    # Running it again adds nothing: the room is already booked at that hour.
    again = services.add_to_timetable(
        by=alpha.assignment,
        class_type=aerobics,
        branch=branch,
        first_day=tomorrow,
        at=TOMORROW_AT,
        trainer=trainer,
        repeat_weeks=4,
    )
    assert again == []
    assert ClassSession.objects.for_gym(alpha.gym).count() == 4


def test_the_timetable_cannot_start_in_the_past(alpha, aerobics, branch):
    with pytest.raises(InvalidDetails) as error:
        services.add_to_timetable(
            by=alpha.assignment,
            class_type=aerobics,
            branch=branch,
            first_day=timezone.localdate() - timedelta(days=1),
            at=TOMORROW_AT,
        )
    assert error.value.field == "first_day"


# --- Booking a place --------------------------------------------------------


def test_a_class_fills_up_and_then_refuses(alpha, session, make_member):
    one = make_member("Solange Keza", "0788111222")
    two = make_member("Claude Ntwari", "0788111223")
    three = make_member("Diane Uwera", "0788111224")

    services.book(member=one, session=session)
    services.book(member=two, session=session)
    assert services.places_left(session) == 0

    with pytest.raises(services.ClassFull):
        services.book(member=three, session=session)

    # Booking twice is not two places.
    services.book(member=one, session=session)
    assert services.places_left(session) == 0


def test_giving_up_a_place_frees_it_for_somebody_else(alpha, session, make_member):
    one = make_member("Solange Keza", "0788111222")
    two = make_member("Claude Ntwari", "0788111223")
    three = make_member("Diane Uwera", "0788111224")
    services.book(member=one, session=session)
    services.book(member=two, session=session)

    services.give_up_place(member=one, session=session)
    assert services.places_left(session) == 1
    services.book(member=three, session=session)
    assert services.places_left(session) == 0
    # The cancelled booking is kept, so the gym can see who does this often.
    assert Booking.objects.for_gym(alpha.gym).filter(member=one).count() == 1


def test_booking_closes_shortly_before_the_class_starts(alpha, aerobics, branch, make_member):
    member = make_member()
    soon = ClassSession.objects.create(
        gym=alpha.gym,
        class_type=aerobics,
        branch=branch,
        held_on=timezone.localdate(),
        starts_at=(timezone.localtime() + timedelta(minutes=10)).time(),
        minutes=60,
        places=10,
    )
    with pytest.raises(services.TooLate):
        services.book(member=member, session=soon)


def test_a_called_off_class_takes_no_bookings(alpha, session, make_member):
    services.cancel_session(by=alpha.assignment, session=session, reason="Trainer is ill")
    with pytest.raises(services.TooLate):
        services.book(member=make_member(), session=session)
    session.refresh_from_db()
    assert session.cancel_reason == "Trainer is ill"


def test_a_member_cannot_book_at_another_gym(alpha, session, make_gym, make_member):
    bravo = make_gym("Bravo Fitness", "0788000002")
    theirs = members.register_member(
        by=bravo.assignment, phone="0788999111", details=MemberDetails(full_name="Outsider")
    )
    with pytest.raises(InvalidDetails):
        services.book(member=theirs, session=session)


# --- A session with a chosen trainer ----------------------------------------


def test_a_member_books_a_free_hour_and_nobody_else_can_take_it(
    alpha, trainer, branch, make_member
):
    today = timezone.localdate()
    weekday = (today + timedelta(days=2)).weekday()
    services.set_trainer_hours(
        by=alpha.assignment,
        trainer=trainer,
        windows=[(weekday, time(14, 0), time(16, 0), branch)],
    )
    slots = services.free_slots(trainer, today)
    assert [s.starts_at for s in slots][:2] == [time(14, 0), time(15, 0)]

    first = slots[0]
    services.book_trainer(
        member=make_member(), trainer=trainer, held_on=first.held_on, at=first.starts_at
    )
    left = services.free_slots(trainer, today)
    assert (first.held_on, first.starts_at) not in {(s.held_on, s.starts_at) for s in left}

    with pytest.raises(services.TooLate):
        services.book_trainer(
            member=make_member("Claude Ntwari", "0788111223"),
            trainer=trainer,
            held_on=first.held_on,
            at=first.starts_at,
        )


def test_free_hours_must_end_after_they_start(alpha, trainer, branch):
    with pytest.raises(InvalidDetails) as error:
        services.set_trainer_hours(
            by=alpha.assignment,
            trainer=trainer,
            windows=[(1, time(16, 0), time(14, 0), branch)],
        )
    assert error.value.field == "hours"


def test_a_trainers_diary_has_classes_and_one_to_ones_together(
    alpha, trainer, branch, session, make_member, tomorrow
):
    weekday = (timezone.localdate() + timedelta(days=2)).weekday()
    services.set_trainer_hours(
        by=alpha.assignment, trainer=trainer, windows=[(weekday, time(9, 0), time(10, 0), branch)]
    )
    slot = services.free_slots(trainer, timezone.localdate())[0]
    services.book_trainer(
        member=make_member(), trainer=trainer, held_on=slot.held_on, at=slot.starts_at
    )
    diary = services.trainer_diary(trainer, timezone.localdate(), days=10)
    assert len(diary) == 2


# --- Reviews ----------------------------------------------------------------


def _held_yesterday(session) -> None:
    ClassSession.objects.filter(pk=session.pk).update(
        held_on=timezone.localdate() - timedelta(days=1)
    )
    session.refresh_from_db()


def test_only_a_member_who_came_may_review_the_trainer(alpha, session, make_member):
    came = make_member("Solange Keza", "0788111222")
    stranger = make_member("Claude Ntwari", "0788111223")
    services.book(member=came, session=session)
    _held_yesterday(session)

    assert services.may_review(came, session) is True
    assert services.may_review(stranger, session) is False
    with pytest.raises(InvalidDetails):
        services.leave_review(member=stranger, session=session, stars=1, words="Never went")


def test_a_review_counts_once_and_shows_the_trainers_score(alpha, trainer, session, make_member):
    one = make_member("Solange Keza", "0788111222")
    two = make_member("Claude Ntwari", "0788111223")
    services.book(member=one, session=session)
    services.book(member=two, session=session)
    _held_yesterday(session)

    services.leave_review(member=one, session=session, stars=5, words="Eric pushed us properly.")
    services.leave_review(member=two, session=session, stars=4)
    # A second review of the same class is refused.
    with pytest.raises(InvalidDetails):
        services.leave_review(member=one, session=session, stars=1)

    average, count = services.rating(trainer)
    assert (average, count) == (4.5, 2)
    assert "Eric pushed us properly." in [r.words for r in services.reviews_of(trainer)]


def test_a_hidden_review_leaves_the_score_and_is_written_down(alpha, trainer, session, make_member):
    member = make_member()
    services.book(member=member, session=session)
    _held_yesterday(session)
    review = services.leave_review(member=member, session=session, stars=1, words="Rude words")

    services.hide_review(by=alpha.assignment, review=review, reason="Insulting language")

    assert services.rating(trainer) == (None, 0)
    assert list(services.reviews_of(trainer)) == []
    assert Review.objects.for_gym(alpha.gym).count() == 1
    entry = AuditEntry.objects.for_gym(alpha.gym).filter(action="class.review_hidden").get()
    assert "Insulting language" in str(entry.details)


def test_hiding_a_review_needs_a_reason_and_a_manager(alpha, add_staff, session, make_member):
    member = make_member()
    services.book(member=member, session=session)
    _held_yesterday(session)
    review = services.leave_review(member=member, session=session, stars=2)

    with pytest.raises(InvalidDetails):
        services.hide_review(by=alpha.assignment, review=review, reason="  ")
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    with pytest.raises(PermissionDenied):
        services.hide_review(by=desk.assignment, review=review, reason="No")
