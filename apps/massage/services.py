"""
Business rules for massage.

Three things make massage different from a class:

  * the type decides the length and the price, so picking the type decides
    everything else;
  * a slot belongs to one person, so two people can never hold the same time;
  * calling off late costs money, because the masseuse turned others away.

Who may do what: the owner sets the types and the rules; the desk books,
accepts and calls off; the masseuse writes her own notes, and only she reads
them.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.classes.models import TrainerHours, TrainerProfile
from apps.corporate.models import Employee
from apps.corporate.services import agreed_price
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import CHECK_IN_ROLES, FRONT_DESK_ROLES, OWNER_ONLY, require_role
from apps.massage.models import BookingStatus, MassageBooking, MassageRule, MassageType
from apps.members.models import Member, Service
from apps.members.services import InvalidDetails
from apps.people.models import Person

MAX_MINUTES = 240
MIN_MINUTES = 5
# How far ahead the desk may look for a free time.
DAYS_AHEAD = 30
# Free times are offered on the half hour, which is how people ask for them.
SLOT_STEP = 30


def _clean(value) -> str:
    return " ".join((value or "").split())


def _same_gym(by: StaffAssignment, record) -> None:
    if record.gym_id != by.gym_id:
        raise PermissionDenied("That record belongs to a different gym.")


# --- The types a gym offers --------------------------------------------------


def types_for(gym: Gym, *, active_only: bool = True):
    found = MassageType.objects.for_gym(gym).select_related("service")
    return found.filter(is_active=True) if active_only else found


@transaction.atomic
def create_type(
    *,
    by: StaffAssignment,
    name: str,
    minutes: int,
    price_rwf: int,
    what_to_expect: str = "",
) -> MassageType:
    """
    Add a kind of massage. Owner only.

    A massage type is also a service the gym sells, so one is created with the
    same name and price. That keeps the door price and the prices companies
    agree in their contracts in one place.
    """
    require_role(by, OWNER_ONLY)
    from apps.members import services as member_services

    name = _clean(name)
    if not name:
        raise InvalidDetails("name", "required")
    if MassageType.objects.for_gym(by.gym).filter(name__iexact=name).exists():
        raise InvalidDetails("name", "duplicate")
    if not isinstance(minutes, int) or not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise InvalidDetails("minutes", "out_of_range")

    service = member_services.create_service(by=by, name=name, price_rwf=price_rwf)
    massage_type = MassageType.objects.create(
        gym=by.gym,
        name=name,
        minutes=minutes,
        service=service,
        what_to_expect=_clean(what_to_expect),
    )
    audit.record(
        action="massage.type_created",
        summary=f"Massage '{name}' added: {minutes} min, RWF {price_rwf:,}",
        gym=by.gym,
        actor=by.person,
        target=massage_type,
    )
    return massage_type


@transaction.atomic
def update_type(
    *,
    by: StaffAssignment,
    massage_type: MassageType,
    name: str,
    minutes: int,
    price_rwf: int,
    what_to_expect: str = "",
) -> MassageType:
    """Change a kind of massage. Owner only. Bookings already made keep their own copy."""
    require_role(by, OWNER_ONLY)
    _same_gym(by, massage_type)
    from apps.members import services as member_services

    name = _clean(name)
    if not name:
        raise InvalidDetails("name", "required")
    clash = (
        MassageType.objects.for_gym(by.gym).filter(name__iexact=name).exclude(pk=massage_type.pk)
    )
    if clash.exists():
        raise InvalidDetails("name", "duplicate")
    if not isinstance(minutes, int) or not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise InvalidDetails("minutes", "out_of_range")

    member_services.update_service(
        by=by, service=massage_type.service, name=name, price_rwf=price_rwf
    )
    massage_type.name = name
    massage_type.minutes = minutes
    massage_type.what_to_expect = _clean(what_to_expect)
    massage_type.save()
    audit.record(
        action="massage.type_updated",
        summary=f"Massage '{name}' changed: {minutes} min, RWF {price_rwf:,}",
        gym=by.gym,
        actor=by.person,
        target=massage_type,
    )
    return massage_type


@transaction.atomic
def set_type_active(*, by: StaffAssignment, massage_type: MassageType, active: bool) -> MassageType:
    """Stop offering a massage, or offer it again. Owner only."""
    require_role(by, OWNER_ONLY)
    _same_gym(by, massage_type)
    from apps.members import services as member_services

    if massage_type.is_active == active:
        return massage_type
    massage_type.is_active = active
    massage_type.save(update_fields=["is_active"])
    member_services.set_service_active(by=by, service=massage_type.service, active=active)
    audit.record(
        action="massage.type_restored" if active else "massage.type_stopped",
        summary=(
            f"Massage '{massage_type.name}' is "
            + ("offered again" if active else "no longer offered")
        ),
        gym=by.gym,
        actor=by.person,
        target=massage_type,
    )
    return massage_type


# --- How this gym runs its bookings ------------------------------------------


def rules_for(gym: Gym) -> MassageRule:
    rule = MassageRule.objects.for_gym(gym).first()
    return rule or MassageRule(gym=gym)


@transaction.atomic
def set_rules(
    *,
    by: StaffAssignment,
    cut_off_minutes: int,
    gap_minutes: int,
    reminder_minutes: int,
) -> MassageRule:
    """Set the cut-off, the gap between massages and when to remind. Owner only."""
    require_role(by, OWNER_ONLY)
    for field, value, top in (
        ("cut_off_minutes", cut_off_minutes, 24 * 60),
        ("gap_minutes", gap_minutes, 120),
        ("reminder_minutes", reminder_minutes, 120),
    ):
        if not isinstance(value, int) or not 0 <= value <= top:
            raise InvalidDetails(field, "out_of_range")

    rule, _made = MassageRule.objects.get_or_create(gym=by.gym)
    rule.cut_off_minutes = cut_off_minutes
    rule.gap_minutes = gap_minutes
    rule.reminder_minutes = reminder_minutes
    rule.updated_by = by.person
    rule.save()
    audit.record(
        action="massage.rules_set",
        summary=(
            f"Massage rules: called off up to {cut_off_minutes} min before, "
            f"{gap_minutes} min between massages"
        ),
        gym=by.gym,
        actor=by.person,
        target=rule,
    )
    return rule


# --- Who gives massages ------------------------------------------------------


def masseuses_for(gym: Gym, branch: Branch | None = None):
    found = (
        TrainerProfile.objects.for_gym(gym)
        .filter(gives_massages=True, staff__is_active=True)
        .select_related("staff__person")
    )
    if branch is not None:
        found = found.filter(hours__branch=branch).distinct()
    return found.order_by("staff__person__full_name")


def _working_windows(masseuse: TrainerProfile, branch: Branch, day: date):
    """When this masseuse is at this branch on this day, as (start, end) times."""
    hours = TrainerHours.objects.for_gym(masseuse.gym).filter(
        trainer=masseuse, branch=branch, weekday=day.weekday()
    )
    return [(row.starts_at, row.ends_at) for row in hours.order_by("starts_at")]


def _taken(gym: Gym, branch: Branch, day: date, gap: int):
    """Busy stretches per masseuse on a day, each widened by the gap between massages."""
    busy: dict[int, list[tuple[datetime, datetime]]] = {}
    booked = MassageBooking.objects.for_gym(gym).filter(
        branch=branch,
        held_on=day,
        status__in=(BookingStatus.ACCEPTED, BookingStatus.DONE, BookingStatus.NO_SHOW),
    )
    for booking in booked:
        start = datetime.combine(day, booking.starts_at)
        finish = start + timedelta(minutes=booking.minutes + gap)
        busy.setdefault(booking.masseuse_id, []).append((start, finish))
    return busy


@dataclass(frozen=True)
class FreeSlot:
    """A time somebody could be booked into, and who would give the massage."""

    at: time
    masseuse: TrainerProfile

    @property
    def label(self) -> str:
        return f"{self.at:%H:%M}"


def free_slots(
    gym: Gym,
    *,
    branch: Branch,
    massage_type: MassageType,
    day: date,
    masseuse: TrainerProfile | None = None,
) -> list[FreeSlot]:
    """
    The times somebody can still be booked in on a day.

    A time is offered while at least one masseuse is free for the whole length
    of the massage, inside her working hours, with the gym's gap kept after the
    massage before her. Times already gone today are not offered.
    """
    rule = rules_for(gym)
    busy = _taken(gym, branch, day, rule.gap_minutes)
    now = timezone.localtime()
    candidates = [masseuse] if masseuse is not None else list(masseuses_for(gym, branch))

    found: dict[time, TrainerProfile] = {}
    for person in candidates:
        if person is None or person.gym_id != gym.pk:
            continue
        for window_start, window_end in _working_windows(person, branch, day):
            at = datetime.combine(day, window_start)
            closes = datetime.combine(day, window_end)
            while at + timedelta(minutes=massage_type.minutes) <= closes:
                finish = at + timedelta(minutes=massage_type.minutes)
                clear = all(
                    finish <= taken_from or at >= taken_to
                    for taken_from, taken_to in busy.get(person.pk, ())
                )
                not_past = day > now.date() or at.time() > now.time()
                if clear and not_past and at.time() not in found:
                    found[at.time()] = person
                at += timedelta(minutes=SLOT_STEP)

    return [FreeSlot(at=at, masseuse=found[at]) for at in sorted(found)]


def nearest_free(
    gym: Gym, *, branch: Branch, massage_type: MassageType, day: date, wanted: time, count: int = 3
) -> list[FreeSlot]:
    """The free times closest to one that has gone, so the desk can offer another."""
    slots = free_slots(gym, branch=branch, massage_type=massage_type, day=day)
    wanted_minutes = wanted.hour * 60 + wanted.minute
    by_distance = sorted(
        slots, key=lambda slot: abs(slot.at.hour * 60 + slot.at.minute - wanted_minutes)
    )
    return by_distance[:count]


# --- Booking -----------------------------------------------------------------


def _price_for(gym: Gym, massage_type: MassageType, branch: Branch, employee: Employee | None):
    """
    What this massage will cost, and who pays.

    A company employee is charged the price their company agreed for this
    massage. If the contract does not cover it, the company does not pay and
    the employee pays the door price themselves.
    """
    if employee is not None:
        agreed = agreed_price(gym, employee.company, massage_type.service, branch)
        if agreed is not None:
            return agreed, employee.company.name
        return massage_type.price_rwf, ""
    return massage_type.price_rwf, ""


def _check_slot(
    gym: Gym,
    *,
    branch: Branch,
    massage_type: MassageType,
    masseuse: TrainerProfile,
    day: date,
    at: time,
) -> None:
    offered = free_slots(gym, branch=branch, massage_type=massage_type, day=day, masseuse=masseuse)
    if not any(slot.at == at for slot in offered):
        raise InvalidDetails("starts_at", "taken")


@transaction.atomic
def book(
    *,
    by: StaffAssignment,
    massage_type: MassageType,
    masseuse: TrainerProfile,
    branch: Branch,
    day: date,
    at: time,
    person: Person,
    member: Member | None = None,
    employee: Employee | None = None,
    accepted: bool = True,
) -> MassageBooking:
    """
    Put somebody in a slot. Front-desk roles.

    `accepted=False` records a request the gym has not answered yet: it does
    not hold the time, and somebody else may still take it.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, massage_type)
    _same_gym(by, branch)
    _same_gym(by, masseuse)
    if not massage_type.is_active:
        raise InvalidDetails("massage_type", "not_offered")
    if not masseuse.gives_massages:
        raise InvalidDetails("masseuse", "does_not_give_massages")

    today = timezone.localdate()
    if not isinstance(day, date) or not today <= day <= today + timedelta(days=DAYS_AHEAD):
        raise InvalidDetails("held_on", "out_of_range")
    if accepted:
        _check_slot(
            by.gym, branch=branch, massage_type=massage_type, masseuse=masseuse, day=day, at=at
        )

    price, paid_by = _price_for(by.gym, massage_type, branch, employee)
    booking = MassageBooking.objects.create(
        gym=by.gym,
        massage_type=massage_type,
        type_name=massage_type.name,
        minutes=massage_type.minutes,
        price_rwf=price,
        masseuse=masseuse,
        branch=branch,
        person=person,
        member=member,
        employee=employee,
        paid_by=paid_by,
        held_on=day,
        starts_at=at,
        status=BookingStatus.ACCEPTED if accepted else BookingStatus.REQUESTED,
        asked_by=by.person,
        decided_at=timezone.now() if accepted else None,
        decided_by=by.person if accepted else None,
    )
    audit.record(
        action="massage.booked" if accepted else "massage.requested",
        summary=(
            f"{person.full_name}: {massage_type.name} with "
            f"{masseuse.staff.person.full_name} on {day:%d %b} at {at:%H:%M}"
        ),
        gym=by.gym,
        actor=by.person,
        target=booking,
    )
    return booking


@transaction.atomic
def accept(*, by: StaffAssignment, booking: MassageBooking) -> MassageBooking:
    """Say yes to a request. The slot is held from this moment. Front-desk roles."""
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, booking)
    if booking.status != BookingStatus.REQUESTED:
        raise InvalidDetails("booking", "already_decided")
    _check_slot(
        by.gym,
        branch=booking.branch,
        massage_type=booking.massage_type,
        masseuse=booking.masseuse,
        day=booking.held_on,
        at=booking.starts_at,
    )
    booking.status = BookingStatus.ACCEPTED
    booking.decided_at, booking.decided_by = timezone.now(), by.person
    booking.save(update_fields=["status", "decided_at", "decided_by"])
    audit.record(
        action="massage.accepted",
        summary=f"{booking.who}: {booking.type_name} on {booking.held_on:%d %b} accepted",
        gym=by.gym,
        actor=by.person,
        target=booking,
    )
    return booking


@transaction.atomic
def decline(*, by: StaffAssignment, booking: MassageBooking, reason: str) -> MassageBooking:
    """Say no to a request, with a reason the person can be told. Front-desk roles."""
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, booking)
    reason = _clean(reason)
    if not reason:
        raise InvalidDetails("reason", "required")
    if booking.status != BookingStatus.REQUESTED:
        raise InvalidDetails("booking", "already_decided")
    booking.status = BookingStatus.DECLINED
    booking.decided_at, booking.decided_by = timezone.now(), by.person
    booking.cancel_reason = reason[:200]
    booking.save(update_fields=["status", "decided_at", "decided_by", "cancel_reason"])
    audit.record(
        action="massage.declined",
        summary=f"{booking.who}: {booking.type_name} on {booking.held_on:%d %b} declined",
        gym=by.gym,
        actor=by.person,
        target=booking,
        details={"reason": reason},
    )
    return booking


def too_late_to_cancel(booking: MassageBooking, *, now=None) -> bool:
    """Whether the slot is now held for good, so calling it off still costs."""
    rule = rules_for(booking.gym)
    now = now or timezone.localtime()
    starts = timezone.make_aware(datetime.combine(booking.held_on, booking.starts_at))
    return now > starts - timedelta(minutes=rule.cut_off_minutes)


@transaction.atomic
def cancel(*, by: StaffAssignment, booking: MassageBooking, reason: str) -> MassageBooking:
    """
    Call a massage off. Front-desk roles.

    Up to the cut-off this costs nothing. After it the slot stays charged,
    because the masseuse held the time and turned other people away.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, booking)
    reason = _clean(reason)
    if not reason:
        raise InvalidDetails("reason", "required")
    if booking.status not in (BookingStatus.REQUESTED, BookingStatus.ACCEPTED):
        raise InvalidDetails("booking", "already_decided")

    late = booking.status == BookingStatus.ACCEPTED and too_late_to_cancel(booking)
    booking.status = BookingStatus.NO_SHOW if late else BookingStatus.CANCELLED
    booking.cancelled_at, booking.cancelled_by = timezone.now(), by.person
    booking.cancel_reason = reason[:200]
    booking.charged_anyway = late
    booking.save(
        update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "charged_anyway"]
    )
    audit.record(
        action="massage.cancelled",
        summary=(
            f"{booking.who}: {booking.type_name} on {booking.held_on:%d %b} called off"
            + (" after the cut-off, so it is still charged" if late else "")
        ),
        gym=by.gym,
        actor=by.person,
        target=booking,
        details={"reason": reason, "charged": late},
    )
    return booking


@transaction.atomic
def mark_done(*, by: StaffAssignment, booking: MassageBooking) -> MassageBooking:
    """Say the massage happened. The desk or the masseuse."""
    require_role(by, CHECK_IN_ROLES)
    _same_gym(by, booking)
    if booking.status != BookingStatus.ACCEPTED:
        raise InvalidDetails("booking", "not_booked")
    booking.status = BookingStatus.DONE
    booking.save(update_fields=["status"])
    return booking


@transaction.atomic
def write_note(*, by: StaffAssignment, booking: MassageBooking, note: str) -> MassageBooking:
    """
    The masseuse writes her own note about the person, for next time.

    Only the masseuse who gave the massage may write or read it. It is not
    shown to the desk, the owner or the company: it is about somebody's body.
    """
    _same_gym(by, booking)
    if booking.masseuse.staff_id != by.pk:
        raise PermissionDenied("Only the masseuse who gave the massage may write the note.")
    booking.note = _clean(note)[:300]
    booking.note_at = timezone.now() if booking.note else None
    booking.save(update_fields=["note", "note_at"])
    return booking


# --- Lists -------------------------------------------------------------------


def waiting_for_the_gym(gym: Gym, branch: Branch | None = None):
    """Requests nobody has answered yet, soonest first."""
    found = (
        MassageBooking.objects.for_gym(gym)
        .filter(status=BookingStatus.REQUESTED, held_on__gte=timezone.localdate())
        .select_related("person", "masseuse__staff__person", "branch")
    )
    if branch is not None:
        found = found.filter(branch=branch)
    return found.order_by("held_on", "starts_at")


def diary(gym: Gym, *, branch: Branch, day: date):
    """Every booking at a branch on a day, for the desk's grid."""
    return (
        MassageBooking.objects.for_gym(gym)
        .filter(branch=branch, held_on=day)
        .exclude(status__in=(BookingStatus.DECLINED, BookingStatus.CANCELLED))
        .select_related("person", "masseuse__staff__person", "massage_type")
        .order_by("starts_at")
    )


def day_for(masseuse: TrainerProfile, day: date):
    """One masseuse's bookings on a day, in time order. Her own page."""
    return (
        MassageBooking.objects.for_gym(masseuse.gym)
        .filter(masseuse=masseuse, held_on=day)
        .exclude(status__in=(BookingStatus.DECLINED, BookingStatus.CANCELLED))
        .select_related("person", "branch")
        .order_by("starts_at")
    )


def starting_soon(gym: Gym, *, within_minutes: int | None = None):
    """
    Massages about to start, so the desk can remind the masseuse and the client.

    The system sends nothing itself: the desk presses a button that opens
    WhatsApp or a text message with the words already written.
    """
    rule = rules_for(gym)
    minutes = rule.reminder_minutes if within_minutes is None else within_minutes
    now = timezone.localtime()
    today = now.date()
    until = (now + timedelta(minutes=minutes)).time()
    return (
        MassageBooking.objects.for_gym(gym)
        .filter(
            held_on=today,
            status=BookingStatus.ACCEPTED,
            starts_at__gte=now.time(),
            starts_at__lte=until,
        )
        .select_related("person", "masseuse__staff__person")
        .order_by("starts_at")
    )


def count_for(gym: Gym, masseuse: TrainerProfile, *, since: date) -> int:
    return (
        MassageBooking.objects.for_gym(gym)
        .filter(masseuse=masseuse, held_on__gte=since, status=BookingStatus.DONE)
        .count()
    )


def service_of(massage_type: MassageType) -> Service:
    return massage_type.service
