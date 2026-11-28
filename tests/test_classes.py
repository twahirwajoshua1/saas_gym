"""
Classes, trainers, booking and reviews.

The rules that matter: a class cannot be over-filled, booking closes before it
starts, only someone who came may review the trainer, reviews are public but
can be hidden with a reason, and a trainer's free hours cannot be
double-booked.
"""

import uuid
from datetime import time, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.attendance import services as attendance
from apps.audit.models import AuditEntry
from apps.billing import services as billing
from apps.billing.services import PaymentInput
from apps.classes import services
from apps.classes.models import Booking, ClassSession, Review, TrainerProfile
from apps.gyms.models import Branch, Role
from apps.members import services as members
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE, TRAINING_CLASSES

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
def monthly_member(alpha, make_member):
    """A member with a membership that is valid today."""
    member = make_member()
    package = members.create_package(
        by=alpha.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )
    billing.sell_membership(
        by=alpha.assignment,
        member=member,
        package=package,
        start_date=timezone.localdate(),
        idempotency_key=uuid.uuid4(),
        payment=PaymentInput(method="cash"),
    )
    return member


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


# --- The screens ------------------------------------------------------------


@pytest.fixture
def classes_on(alpha):
    module_services.enable_module(gym=alpha.gym, code=TRAINING_CLASSES)
    return alpha


def _card(member, staff):
    token = members.create_card_link(by=staff.assignment, member=member)
    member.refresh_from_db()
    return token


def test_the_owner_builds_a_timetable_and_the_desk_sees_it(
    classes_on, aerobics, branch, trainer, add_staff, sign_in, tomorrow
):
    owner = sign_in(classes_on)
    answer = owner.post(
        reverse("classes:schedule"),
        {
            "class_type": str(aerobics.public_id),
            "branch": str(branch.public_id),
            "trainer": str(trainer.public_id),
            "first_day": tomorrow.isoformat(),
            "at": "18:00",
            "repeat_weeks": "2",
        },
    )
    assert answer.status_code == 302
    assert ClassSession.objects.for_gym(classes_on.gym).count() == 2

    desk = add_staff(classes_on.gym, "0788000005", Role.RECEPTIONIST)
    page = sign_in(desk).get(reverse("classes:timetable")).content.decode()
    assert "Aerobics" in page and "Eric Habimana" in page
    # A receptionist cannot change the timetable.
    assert sign_in(desk).get(reverse("classes:schedule")).status_code in (302, 403)


def test_a_member_books_from_their_card_and_the_desk_sees_who_is_coming(
    classes_on, session, make_member, add_staff, sign_in, client
):
    member = make_member()
    token = _card(member, classes_on)

    page = client.get(reverse("memberapp:card_classes", args=[token])).content.decode()
    assert "Aerobics" in page and "Book" in page

    booked = client.post(reverse("memberapp:card_book", args=[token, session.public_id]))
    assert booked.status_code == 302
    assert services.places_left(session) == 1

    desk = add_staff(classes_on.gym, "0788000005", Role.RECEPTIONIST)
    detail = (
        sign_in(desk)
        .get(reverse("classes:session_detail", args=[session.public_id]))
        .content.decode()
    )
    assert "Solange Keza" in detail and "1 places still free" in detail

    # Giving up the place frees it again.
    client.post(reverse("memberapp:card_book", args=[token, session.public_id]), {"give_up": "yes"})
    assert services.places_left(session) == 2


def test_a_member_books_a_trainer_from_their_card(classes_on, trainer, branch, make_member, client):
    member = make_member()
    token = _card(member, classes_on)
    weekday = (timezone.localdate() + timedelta(days=2)).weekday()
    services.set_trainer_hours(
        by=classes_on.assignment,
        trainer=trainer,
        windows=[(weekday, time(14, 0), time(16, 0), branch)],
    )

    page = client.get(reverse("memberapp:card_trainers", args=[token])).content.decode()
    assert "Eric Habimana" in page and "Strength and conditioning" in page

    slot = services.free_slots(trainer, timezone.localdate())[0]
    answer = client.post(
        reverse("memberapp:card_book_trainer", args=[token, trainer.public_id]),
        {"slot": f"{slot.held_on:%Y-%m-%d}T{slot.starts_at:%H:%M}", "note": "Back pain"},
    )
    assert answer.status_code == 302
    booked = services.personal_sessions_for(member).get()
    assert booked.trainer_id == trainer.pk and booked.note == "Back pain"


def test_a_member_reviews_the_trainer_from_their_card_and_the_gym_sees_it(
    classes_on, trainer, session, make_member, sign_in, client
):
    member = make_member()
    token = _card(member, classes_on)
    services.book(member=member, session=session)
    _held_yesterday(session)

    card = client.get(reverse("memberapp:card", args=[token])).content.decode()
    assert "How was it?" in card

    client.post(
        reverse("memberapp:card_review", args=[token, session.public_id]),
        {"stars": "5", "words": "Eric is patient and clear."},
    )
    assert services.rating(trainer) == (5.0, 1)

    page = (
        sign_in(classes_on)
        .get(reverse("classes:trainer_detail", args=[trainer.public_id]))
        .content.decode()
    )
    assert "Eric is patient and clear." in page and "Solange Keza" in page


def test_the_card_shows_no_classes_when_the_gym_has_not_bought_the_module(
    alpha, make_member, client
):
    member = make_member()
    token = _card(member, alpha)
    card = client.get(reverse("memberapp:card", args=[token])).content.decode()
    assert "See the timetable" not in card
    assert client.get(reverse("memberapp:card_classes", args=[token])).status_code == 404


def test_another_gyms_card_cannot_book_this_gyms_class(
    classes_on, session, make_gym, add_staff, client
):
    bravo = make_gym("Bravo Fitness", "0788000002")
    theirs = members.register_member(
        by=bravo.assignment, phone="0788999111", details=MemberDetails(full_name="Outsider")
    )
    token = members.create_card_link(by=bravo.assignment, member=theirs)
    answer = client.post(reverse("memberapp:card_book", args=[token, session.public_id]))
    assert answer.status_code == 404
    assert services.places_left(session) == 2


# --- Bringing a friend ------------------------------------------------------


def test_free_guests_run_out_and_then_the_friend_pays(alpha, branch, monthly_member):
    member = monthly_member
    attendance.set_guest_rule(by=alpha.assignment, free_per_month=1)

    first = attendance.bring_a_friend(
        by=alpha.assignment, member=member, guest_name="Jean Bosco", branch=branch
    )
    second = attendance.bring_a_friend(
        by=alpha.assignment,
        member=member,
        guest_name="Alice Mukamana",
        guest_phone="0788777666",
        branch=branch,
    )
    assert first.was_free is True
    assert second.was_free is False
    assert second.guest_phone == "+250788777666"
    assert attendance.free_guests_left(member, timezone.localdate()) == 0


def test_a_gym_can_give_no_free_guests_at_all(alpha, branch, monthly_member):
    attendance.set_guest_rule(by=alpha.assignment, free_per_month=0)
    visit = attendance.bring_a_friend(
        by=alpha.assignment, member=monthly_member, guest_name="Jean Bosco", branch=branch
    )
    assert visit.was_free is False


def test_a_member_who_cannot_come_in_cannot_bring_anybody(alpha, branch, make_member):
    lapsed = make_member("Lapsed Person", "0788111999")
    with pytest.raises(attendance.NotAllowedIn):
        attendance.bring_a_friend(
            by=alpha.assignment, member=lapsed, guest_name="Jean Bosco", branch=branch
        )


def test_only_a_manager_changes_the_guest_rule(alpha, add_staff):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    with pytest.raises(PermissionDenied):
        attendance.set_guest_rule(by=desk.assignment, free_per_month=5)


def test_the_desk_writes_a_friend_in_from_the_screen(
    alpha, branch, monthly_member, add_staff, sign_in
):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    client = sign_in(desk)
    answer = client.post(
        reverse("attendance:guest_new"),
        {
            "member": str(monthly_member.public_id),
            "guest_name": "Jean Bosco",
            "guest_phone": "",
            "branch": str(branch.public_id),
        },
    )
    assert answer.status_code == 302
    page = client.get(reverse("attendance:guest_new")).content.decode()
    assert "Jean Bosco" in page and monthly_member.full_name in page


# --- What runs every week -----------------------------------------------------


def test_the_timetable_says_what_runs_every_week(alpha, aerobics, branch, trainer):
    """
    "Spinning every Wednesday, 07:30 to 08:30." Nothing stores a repeating
    timetable; it is read back from the classes that are actually scheduled.
    """
    next_wednesday = _next_weekday(2)
    services.add_to_timetable(
        by=alpha.assignment,
        class_type=aerobics,
        branch=branch,
        trainer=trainer,
        first_day=next_wednesday,
        at=time(7, 30),
        minutes=60,
        places=20,
        repeat_weeks=3,
    )

    pattern = services.weekly_pattern(alpha.gym, branch, from_day=next_wednesday)

    assert len(pattern) == 1
    slot = pattern[0]
    assert slot.class_name == "Aerobics"
    assert slot.every_day_of == "Wednesday"
    assert (slot.starts_at, slot.ends_at) == (time(7, 30), time(8, 30))


def test_several_days_of_the_same_class_read_as_one_line(alpha, aerobics, branch):
    """Aerobics on Monday, Wednesday and Thursday is one habit, not three."""
    monday = _next_weekday(0)
    for offset in (0, 2, 3):
        services.add_to_timetable(
            by=alpha.assignment,
            class_type=aerobics,
            branch=branch,
            first_day=monday + timedelta(days=offset),
            at=time(18, 0),
            minutes=60,
            places=20,
            repeat_weeks=2,
        )

    pattern = services.weekly_pattern(alpha.gym, branch, from_day=monday)

    assert len(pattern) == 1
    assert pattern[0].every_day_of == "Monday, Wednesday and Thursday"


def test_a_one_off_class_is_not_called_weekly(alpha, aerobics, branch):
    monday = _next_weekday(0)
    services.add_to_timetable(
        by=alpha.assignment,
        class_type=aerobics,
        branch=branch,
        first_day=monday,
        at=time(9, 0),
        minutes=45,
        places=10,
        repeat_weeks=1,
    )

    assert services.weekly_pattern(alpha.gym, branch, from_day=monday) == []


def _next_weekday(weekday: int):
    """The next date that falls on this weekday, today included."""
    today = timezone.localdate()
    return today + timedelta(days=(weekday - today.weekday()) % 7)


# --- The desk adding a trainer ------------------------------------------------


def test_the_desk_can_add_a_trainer_and_put_them_to_work(alpha, add_staff, sign_in, classes_on):
    desk = add_staff(alpha.gym, "0788000021", Role.RECEPTIONIST, "Gisele Mugwaneza")
    client = sign_in(desk)

    response = client.post(
        reverse("classes:trainer_add"),
        {
            "full_name": "Claudine Uwase",
            "phone": "0788000031",
            "headline": "Massage therapist",
            "gives_massages": "on",
        },
        follow=True,
    )

    assert response.status_code == 200
    trainer = TrainerProfile.objects.get()
    assert trainer.staff.person.full_name == "Claudine Uwase"
    assert trainer.staff.role == Role.TRAINER
    assert trainer.gives_massages is True


def test_a_trainer_added_by_the_desk_cannot_sign_in(alpha, add_staff, sign_in, classes_on):
    """
    A way into the system is the owner's to give. A receptionist who could
    create logins could create one for themselves.
    """
    from apps.accounts.models import User

    desk = add_staff(alpha.gym, "0788000022", Role.RECEPTIONIST)

    sign_in(desk).post(
        reverse("classes:trainer_add"),
        {"full_name": "Claudine Uwase", "phone": "0788000032"},
    )

    trainer = TrainerProfile.objects.get()
    assert not User.objects.filter(person=trainer.staff.person).exists()


def test_a_trainer_cannot_be_added_twice(alpha, add_staff, sign_in, classes_on):
    desk = add_staff(alpha.gym, "0788000023", Role.RECEPTIONIST)
    client = sign_in(desk)
    form = {"full_name": "Claudine Uwase", "phone": "0788000033"}
    client.post(reverse("classes:trainer_add"), form)

    page = client.post(reverse("classes:trainer_add"), form).content.decode()

    assert "already works at this gym" in page
    assert TrainerProfile.objects.count() == 1


def test_a_bad_phone_number_is_refused_kindly(alpha, add_staff, sign_in, classes_on):
    desk = add_staff(alpha.gym, "0788000024", Role.RECEPTIONIST)

    page = (
        sign_in(desk)
        .post(reverse("classes:trainer_add"), {"full_name": "Someone", "phone": "12"})
        .content.decode()
    )

    assert "valid phone number" in page
    assert not TrainerProfile.objects.exists()
