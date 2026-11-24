"""
Classes, trainers and bookings: all the rules in one place.

The screens only collect what a person typed and show what these functions
return. Who may do what is decided here, by role, exactly as in the other
modules.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.audit import services as audit
from apps.classes.models import (
    Booking,
    ClassSession,
    ClassType,
    PersonalSession,
    Review,
    TrainerHours,
    TrainerProfile,
)
from apps.gyms.models import Branch, Gym, Role, StaffAssignment
from apps.gyms.permissions import MANAGER_ROLES, require_role
from apps.members.models import Member
from apps.members.services import InvalidDetails

# How far ahead a gym may put classes on the timetable in one go.
MAX_REPEAT_WEEKS = 12
# How long before a class starts a member may still take or give up a place.
CLOSES_MINUTES = 30


def _same_gym(by: StaffAssignment, thing) -> None:
    if thing.gym_id != by.gym_id:
        raise InvalidDetails("gym", "different_gym")


def starts(session) -> datetime:
    """The moment a class or personal session starts, in Kigali time."""
    return timezone.make_aware(datetime.combine(session.held_on, session.starts_at))


# --- Trainers ---------------------------------------------------------------


def list_trainers(gym: Gym, listed_only: bool = True):
    trainers = TrainerProfile.objects.for_gym(gym).select_related("staff__person")
    if listed_only:
        trainers = trainers.filter(is_listed=True, staff__is_active=True)
    return trainers.order_by("staff__person__full_name")


@transaction.atomic
def save_trainer(
    *,
    by: StaffAssignment,
    staff: StaffAssignment,
    headline: str = "",
    about: str = "",
    takes_personal_sessions: bool = True,
    is_listed: bool = True,
) -> TrainerProfile:
    """
    Write what members see about a trainer. Managers, or the trainer about
    themselves.
    """
    if by.pk != staff.pk:
        require_role(by, MANAGER_ROLES)
    _same_gym(by, staff)
    if staff.role != Role.TRAINER:
        raise InvalidDetails("staff", "not_a_trainer")
    profile, _created = TrainerProfile.objects.update_or_create(
        gym=by.gym,
        staff=staff,
        defaults={
            "headline": headline.strip(),
            "about": about.strip(),
            "takes_personal_sessions": takes_personal_sessions,
            "is_listed": is_listed,
        },
    )
    audit.record(
        action="class.trainer_saved",
        summary=f"{profile.full_name}'s trainer page was updated",
        gym=by.gym,
        actor=by.person,
        target=profile,
    )
    return profile


def rating(trainer: TrainerProfile) -> tuple[float | None, int]:
    """The trainer's average stars and how many reviews, hidden ones left out."""
    shown = trainer.reviews.filter(hidden_at__isnull=True)
    figures = shown.aggregate(average=Avg("stars"), count=Count("id"))
    average = figures["average"]
    return (round(average, 1) if average is not None else None, figures["count"])


# --- What the gym teaches ---------------------------------------------------


def list_class_types(gym: Gym, active_only: bool = True):
    types = ClassType.objects.for_gym(gym)
    return types.filter(is_active=True) if active_only else types


@transaction.atomic
def save_class_type(
    *,
    by: StaffAssignment,
    name: str,
    what_to_expect: str = "",
    minutes: int = 60,
    places: int = 20,
    class_type: ClassType | None = None,
) -> ClassType:
    """Add or change something the gym teaches. Managers only."""
    require_role(by, MANAGER_ROLES)
    name = " ".join((name or "").split())
    if not name:
        raise InvalidDetails("name", "required")
    if minutes < 5 or minutes > 300:
        raise InvalidDetails("minutes", "out_of_range")
    if places < 1 or places > 500:
        raise InvalidDetails("places", "out_of_range")
    if class_type is None:
        class_type = ClassType(gym=by.gym)
    else:
        _same_gym(by, class_type)
    clash = ClassType.objects.for_gym(by.gym).filter(name__iexact=name).exclude(pk=class_type.pk)
    if clash.exists():
        raise InvalidDetails("name", "already_used")
    class_type.name = name
    class_type.what_to_expect = what_to_expect.strip()
    class_type.minutes = minutes
    class_type.places = places
    class_type.save()
    audit.record(
        action="class.type_saved",
        summary=f"Class “{class_type.name}” was saved",
        gym=by.gym,
        actor=by.person,
        target=class_type,
    )
    return class_type


@transaction.atomic
def archive_class_type(*, by: StaffAssignment, class_type: ClassType) -> ClassType:
    """Stop teaching something. Past classes keep their name. Managers only."""
    require_role(by, MANAGER_ROLES)
    _same_gym(by, class_type)
    class_type.is_active = False
    class_type.save(update_fields=["is_active"])
    audit.record(
        action="class.type_archived",
        summary=f"Class “{class_type.name}” is no longer taught",
        gym=by.gym,
        actor=by.person,
        target=class_type,
    )
    return class_type


# --- The timetable ----------------------------------------------------------


@transaction.atomic
def add_to_timetable(
    *,
    by: StaffAssignment,
    class_type: ClassType,
    branch: Branch,
    first_day: date,
    at: time,
    trainer: TrainerProfile | None = None,
    repeat_weeks: int = 1,
    places: int | None = None,
    minutes: int | None = None,
) -> list[ClassSession]:
    """
    Put a class on the timetable, optionally every week for a few weeks.
    Managers only. A week that already has that class at that time is skipped,
    so running this twice does not double-book the room.
    """
    require_role(by, MANAGER_ROLES)
    _same_gym(by, class_type)
    _same_gym(by, branch)
    if trainer is not None:
        _same_gym(by, trainer)
    if repeat_weeks < 1 or repeat_weeks > MAX_REPEAT_WEEKS:
        raise InvalidDetails("repeat_weeks", "out_of_range")
    if first_day < timezone.localdate():
        raise InvalidDetails("first_day", "in_the_past")

    made: list[ClassSession] = []
    for week in range(repeat_weeks):
        day = first_day + timedelta(weeks=week)
        session, created = ClassSession.objects.get_or_create(
            gym=by.gym,
            branch=branch,
            class_type=class_type,
            held_on=day,
            starts_at=at,
            defaults={
                "trainer": trainer,
                "minutes": minutes or class_type.minutes,
                "places": places or class_type.places,
                "created_by": by.person,
            },
        )
        if created:
            made.append(session)
    audit.record(
        action="class.scheduled",
        summary=f"{class_type.name} put on the timetable {len(made)} time(s) at {branch.name}",
        gym=by.gym,
        actor=by.person,
        target=class_type,
        details={"first_day": str(first_day), "at": at.strftime("%H:%M"), "made": len(made)},
    )
    return made


def timetable(gym: Gym, start: date, days: int = 7, branch: Branch | None = None):
    """Every class from `start` for `days` days, with its trainer and bookings."""
    sessions = (
        ClassSession.objects.for_gym(gym)
        .filter(held_on__range=(start, start + timedelta(days=days - 1)))
        .select_related("class_type", "branch", "trainer__staff__person")
        .annotate(taken=Count("bookings", filter=Q(bookings__cancelled_at__isnull=True)))
    )
    if branch is not None:
        sessions = sessions.filter(branch=branch)
    return sessions


def places_left(session: ClassSession) -> int:
    taken = session.bookings.filter(cancelled_at__isnull=True).count()
    return max(0, session.places - taken)


@transaction.atomic
def cancel_session(*, by: StaffAssignment, session: ClassSession, reason: str = "") -> ClassSession:
    """
    Call off one class. The bookings stay, so the desk can tell people. Managers
    only.
    """
    require_role(by, MANAGER_ROLES)
    _same_gym(by, session)
    if session.is_cancelled:
        return session
    session.cancelled_at = timezone.now()
    session.cancel_reason = reason.strip()[:200]
    session.save(update_fields=["cancelled_at", "cancel_reason"])
    audit.record(
        action="class.cancelled",
        summary=f"{session.class_type.name} on {session.held_on} was called off",
        gym=by.gym,
        actor=by.person,
        target=session,
        details={"reason": session.cancel_reason},
    )
    return session


# --- Booking a place --------------------------------------------------------


class ClassFull(Exception):
    """Every place is taken."""


class TooLate(Exception):
    """The class has started, or is about to."""


def _is_open_for_booking(session: ClassSession) -> bool:
    if session.is_cancelled:
        return False
    return starts(session) - timedelta(minutes=CLOSES_MINUTES) > timezone.now()


@transaction.atomic
def book(*, member: Member, session: ClassSession) -> Booking:
    """
    Take a place in a class, for the member themselves. Raises ClassFull or
    TooLate. Booking again after cancelling takes the place back if there is
    still room.
    """
    if session.gym_id != member.gym_id:
        raise InvalidDetails("session", "different_gym")
    if not _is_open_for_booking(session):
        raise TooLate("Booking for this class has closed.")
    # Lock the class row so two phones cannot take the last place at once.
    locked = ClassSession.objects.select_for_update().get(pk=session.pk)
    booking = Booking.objects.filter(session=locked, member=member).first()
    if booking is not None and booking.is_open:
        return booking
    if places_left(locked) < 1:
        raise ClassFull("This class is full.")
    if booking is None:
        booking = Booking.objects.create(gym=locked.gym, session=locked, member=member)
    else:
        booking.cancelled_at = None
        booking.booked_at = timezone.now()
        booking.save(update_fields=["cancelled_at", "booked_at"])
    return booking


@transaction.atomic
def give_up_place(*, member: Member, session: ClassSession) -> Booking | None:
    """Give a place back, so somebody else can take it."""
    booking = Booking.objects.for_gym(session.gym).filter(session=session, member=member).first()
    if booking is None or not booking.is_open:
        return booking
    if not _is_open_for_booking(session):
        raise TooLate("It is too late to give up this place.")
    booking.cancelled_at = timezone.now()
    booking.save(update_fields=["cancelled_at"])
    return booking


def bookings_for(member: Member, since: date | None = None):
    """A member's places, soonest first, from today unless told otherwise."""
    first_day = since or timezone.localdate()
    return (
        Booking.objects.for_gym(member.gym)
        .filter(member=member, cancelled_at__isnull=True, session__held_on__gte=first_day)
        .select_related("session__class_type", "session__branch", "session__trainer__staff__person")
        .order_by("session__held_on", "session__starts_at")
    )


def who_is_coming(session: ClassSession):
    """The members with a place, for the desk and the trainer."""
    return (
        session.bookings.filter(cancelled_at__isnull=True)
        .select_related("member__person")
        .order_by("member__full_name")
    )


@transaction.atomic
def mark_came(*, by: StaffAssignment, booking: Booking, came: bool = True) -> Booking:
    """Tick someone off at the class. Any staff member who runs the floor."""
    require_role(by, (Role.OWNER, Role.BRANCH_MANAGER, Role.RECEPTIONIST, Role.TRAINER))
    _same_gym(by, booking)
    booking.came_at = timezone.now() if came else None
    booking.save(update_fields=["came_at"])
    return booking


# --- A session with a chosen trainer ----------------------------------------
#
# A trainer writes down the hours they are free. Members see those hours cut
# into slots and take one. Nothing is sold here: whether a one-to-one costs
# money, and how much, is the gym's own arrangement at reception.

SLOT_MINUTES = 60
# How far ahead a member may book a trainer.
BOOK_AHEAD_DAYS = 14


@dataclass(frozen=True)
class Slot:
    """One free hour with a trainer."""

    trainer: TrainerProfile
    branch: Branch
    held_on: date
    starts_at: time
    minutes: int

    @property
    def label(self) -> str:
        return f"{self.held_on:%a %d %b} · {self.starts_at:%H:%M}"


@transaction.atomic
def set_trainer_hours(
    *, by: StaffAssignment, trainer: TrainerProfile, windows: list[tuple[int, time, time, Branch]]
) -> int:
    """
    Replace a trainer's free hours with this list of (weekday, from, to,
    branch). Managers, or the trainer about themselves.
    """
    if by.pk != trainer.staff_id:
        require_role(by, MANAGER_ROLES)
    _same_gym(by, trainer)
    for weekday, from_time, to_time, branch in windows:
        if weekday < 0 or weekday > 6:
            raise InvalidDetails("weekday", "out_of_range")
        if from_time >= to_time:
            raise InvalidDetails("hours", "ends_before_it_starts")
        _same_gym(by, branch)
    TrainerHours.objects.for_gym(by.gym).filter(trainer=trainer).delete()
    TrainerHours.objects.bulk_create(
        [
            TrainerHours(
                gym=by.gym, trainer=trainer, branch=branch, weekday=day, starts_at=a, ends_at=b
            )
            for day, a, b, branch in windows
        ]
    )
    audit.record(
        action="class.trainer_hours_set",
        summary=f"{trainer.full_name}'s free hours were set ({len(windows)} window(s))",
        gym=by.gym,
        actor=by.person,
        target=trainer,
    )
    return len(windows)


def free_slots(trainer: TrainerProfile, today: date, days: int = BOOK_AHEAD_DAYS) -> list[Slot]:
    """
    The hours this trainer still has free, from tomorrow onwards. Hours already
    taken by a personal session are left out; classes they teach are too.
    """
    windows = list(TrainerHours.objects.for_gym(trainer.gym).filter(trainer=trainer))
    if not windows:
        return []
    last_day = today + timedelta(days=days)
    taken = {
        (s.held_on, s.starts_at)
        for s in PersonalSession.objects.for_gym(trainer.gym).filter(
            trainer=trainer, cancelled_at__isnull=True, held_on__range=(today, last_day)
        )
    }
    teaching = {
        (s.held_on, s.starts_at)
        for s in ClassSession.objects.for_gym(trainer.gym).filter(
            trainer=trainer, cancelled_at__isnull=True, held_on__range=(today, last_day)
        )
    }
    slots: list[Slot] = []
    for offset in range(1, days + 1):
        day = today + timedelta(days=offset)
        for window in windows:
            if window.weekday != day.weekday():
                continue
            moment = datetime.combine(day, window.starts_at)
            closing = datetime.combine(day, window.ends_at)
            while moment + timedelta(minutes=SLOT_MINUTES) <= closing:
                at = moment.time()
                if (day, at) not in taken and (day, at) not in teaching:
                    slots.append(
                        Slot(
                            trainer=trainer,
                            branch=window.branch,
                            held_on=day,
                            starts_at=at,
                            minutes=SLOT_MINUTES,
                        )
                    )
                moment += timedelta(minutes=SLOT_MINUTES)
    return sorted(slots, key=lambda s: (s.held_on, s.starts_at))


@transaction.atomic
def book_trainer(
    *, member: Member, trainer: TrainerProfile, held_on: date, at: time, note: str = ""
) -> PersonalSession:
    """
    Book one hour with a trainer. Raises TooLate if that hour is not free, so
    two members cannot take the same hour.
    """
    if trainer.gym_id != member.gym_id:
        raise InvalidDetails("trainer", "different_gym")
    if not trainer.takes_personal_sessions or not trainer.is_listed:
        raise InvalidDetails("trainer", "not_available")
    today = timezone.localdate()
    free = {(s.held_on, s.starts_at): s for s in free_slots(trainer, today)}
    slot = free.get((held_on, at))
    if slot is None:
        raise TooLate("That hour is no longer free.")
    session = PersonalSession.objects.create(
        gym=trainer.gym,
        trainer=trainer,
        member=member,
        branch=slot.branch,
        held_on=held_on,
        starts_at=at,
        minutes=slot.minutes,
        note=note.strip()[:200],
    )
    return session


@transaction.atomic
def cancel_personal_session(
    *, session: PersonalSession, by: StaffAssignment | None = None, member: Member | None = None
) -> PersonalSession:
    """Call off a one-to-one. Either the member who booked it, or gym staff."""
    if member is not None and session.member_id != member.pk:
        raise InvalidDetails("session", "not_yours")
    if by is not None:
        _same_gym(by, session)
    if session.is_cancelled:
        return session
    session.cancelled_at = timezone.now()
    session.save(update_fields=["cancelled_at"])
    return session


def personal_sessions_for(member: Member, since: date | None = None):
    """A member's one-to-ones still to come."""
    first_day = since or timezone.localdate()
    return (
        PersonalSession.objects.for_gym(member.gym)
        .filter(member=member, cancelled_at__isnull=True, held_on__gte=first_day)
        .select_related("trainer__staff__person", "branch")
        .order_by("held_on", "starts_at")
    )


def trainer_diary(trainer: TrainerProfile, start: date, days: int = 7):
    """What a trainer has on: their classes and their one-to-ones together."""
    last_day = start + timedelta(days=days - 1)
    classes = (
        ClassSession.objects.for_gym(trainer.gym)
        .filter(trainer=trainer, held_on__range=(start, last_day))
        .select_related("class_type", "branch")
        .annotate(taken=Count("bookings", filter=Q(bookings__cancelled_at__isnull=True)))
    )
    personal = (
        PersonalSession.objects.for_gym(trainer.gym)
        .filter(trainer=trainer, cancelled_at__isnull=True, held_on__range=(start, last_day))
        .select_related("member__person", "branch")
    )
    return sorted([*classes, *personal], key=lambda item: (item.held_on, item.starts_at))


# --- Reviews ----------------------------------------------------------------


def may_review(member: Member, session: ClassSession) -> bool:
    """A member may review a class they actually attended, once."""
    if session.trainer_id is None or session.is_cancelled:
        return False
    if starts(session) > timezone.now():
        return False
    came = Booking.objects.for_gym(session.gym).filter(
        session=session, member=member, cancelled_at__isnull=True
    )
    if not came.exists():
        return False
    return not Review.objects.for_gym(session.gym).filter(member=member, session=session).exists()


@transaction.atomic
def leave_review(*, member: Member, session: ClassSession, stars: int, words: str = "") -> Review:
    """
    A member's stars and words about the trainer who took a class they came to.
    Everyone sees it, so only someone who was there may write one.
    """
    if not may_review(member, session):
        raise InvalidDetails("session", "cannot_review")
    if stars < 1 or stars > 5:
        raise InvalidDetails("stars", "out_of_range")
    return Review.objects.create(
        gym=session.gym,
        trainer=session.trainer,
        member=member,
        session=session,
        stars=stars,
        words=words.strip()[:2000],
    )


def reviews_of(trainer: TrainerProfile, limit: int = 20):
    """What members said about a trainer, newest first, hidden ones left out."""
    return (
        trainer.reviews.filter(hidden_at__isnull=True)
        .select_related("member", "session__class_type")
        .order_by("-created_at")[:limit]
    )


@transaction.atomic
def hide_review(*, by: StaffAssignment, review: Review, reason: str) -> Review:
    """
    Take a review out of public view, keeping the row. Managers only, and the
    reason is written down, so this cannot be used quietly to bury criticism.
    """
    require_role(by, MANAGER_ROLES)
    _same_gym(by, review)
    reason = " ".join((reason or "").split())
    if not reason:
        raise InvalidDetails("reason", "required")
    review.hidden_at = timezone.now()
    review.hidden_reason = reason[:200]
    review.save(update_fields=["hidden_at", "hidden_reason"])
    audit.record(
        action="class.review_hidden",
        summary=f"A review of {review.trainer.full_name} was hidden",
        gym=by.gym,
        actor=by.person,
        target=review,
        details={"reason": review.hidden_reason, "stars": review.stars},
    )
    return review
