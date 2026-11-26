"""
Business rules for checking members in.

The central question is `assess(member)`: may this member come in today, and if
not, why not? The screen shows the answer; `check_in` enforces it.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Subquery
from django.utils import timezone

from apps.attendance.models import CheckIn, CheckInActivity, CheckInMethod, GuestRule, GuestVisit
from apps.audit import services as audit
from apps.billing import services as billing_services
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import CHECK_IN_ROLES, MANAGER_ROLES, require_role
from apps.members import services as member_services
from apps.members.models import Member, Membership
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS
from apps.people.phones import InvalidPhoneNumber, normalize_phone

# A membership with this many days left (or fewer) gets a "renew soon" warning.
EXPIRING_SOON_DAYS = 7

MAX_SEARCH_RESULTS = 8
MIN_SEARCH_LENGTH = 2


class Reason:
    """Why a member may not come in."""

    NO_MEMBERSHIP = "no_membership"
    EXPIRED = "expired"
    NOT_STARTED = "not_started"
    FROZEN = "frozen"


@dataclass(frozen=True)
class Assessment:
    """The answer to "may this member come in today?", with what the desk should know."""

    member: Member
    allowed: bool
    reason: str = ""  # one of Reason, when not allowed
    frozen_until: date | None = None
    membership: Membership | None = None  # the valid one, or the one that explains the refusal
    days_left: int = 0
    expiring_soon: bool = False
    owes_rwf: int = 0
    already_today: CheckIn | None = None
    # What their package lets them do today, so the desk can tick it off.
    choices: tuple = ()


class NotAllowedIn(Exception):
    """The member has no membership that is valid today."""

    def __init__(self, assessment: Assessment):
        self.assessment = assessment
        super().__init__(f"{assessment.member.full_name} may not come in: {assessment.reason}")


class BranchNeeded(Exception):
    """The gym has several branches and the desk has not said which one it is."""


def attendance_enabled(gym: Gym) -> bool:
    return module_services.is_enabled(gym, ATTENDANCE_ACCESS)


def _require_attendance(gym: Gym) -> None:
    if not attendance_enabled(gym):
        raise PermissionDenied("Attendance and Access is not switched on for this gym.")


def assess(member: Member, on: date | None = None) -> Assessment:
    """Work out whether a member may come in today, and what the desk should be told."""
    today = on or timezone.localdate()
    owes = 0
    if billing_services.payments_enabled(member.gym):
        owes = max(billing_services.member_balance(member), 0)

    current = member_services.current_membership(member, on=today)
    freeze = member_services.freeze_on(current, today) if current is not None else None
    if freeze is not None:
        return Assessment(
            member=member,
            allowed=False,
            reason=Reason.FROZEN,
            membership=current,
            frozen_until=freeze.end_date,
            owes_rwf=owes,
        )
    if current is not None:
        days_left = (current.end_date - today).days + 1
        already = (
            CheckIn.objects.for_gym(member.gym).filter(member=member, checked_in_on=today).first()
        )
        return Assessment(
            member=member,
            allowed=True,
            membership=current,
            days_left=days_left,
            expiring_soon=current.is_renewable and days_left <= EXPIRING_SOON_DAYS,
            owes_rwf=owes,
            already_today=already,
            choices=tuple(member_services.allowed_services(member, today)),
        )

    # Somebody may have bought a book of visits instead of a membership, for
    # example 15 swimming tickets. Those let them in too, while visits remain.
    books = member_services.usable_ticketbooks(member, today)
    if books:
        already = (
            CheckIn.objects.for_gym(member.gym).filter(member=member, checked_in_on=today).first()
        )
        return Assessment(
            member=member,
            allowed=True,
            owes_rwf=owes,
            already_today=already,
            choices=tuple(member_services.allowed_services(member, today)),
        )

    live = member_services.memberships_for(member).filter(cancelled_at__isnull=True)
    upcoming = live.filter(start_date__gt=today).order_by("start_date").first()
    if upcoming is not None:
        return Assessment(
            member=member,
            allowed=False,
            reason=Reason.NOT_STARTED,
            membership=upcoming,
            owes_rwf=owes,
        )
    last = live.filter(end_date__lt=today).order_by("-end_date").first()
    if last is not None:
        return Assessment(
            member=member, allowed=False, reason=Reason.EXPIRED, membership=last, owes_rwf=owes
        )
    return Assessment(member=member, allowed=False, reason=Reason.NO_MEMBERSHIP, owes_rwf=owes)


def find_for_check_in(gym: Gym, query: str) -> list[Assessment]:
    """Members matching part of a name or phone number, each with their assessment."""
    query = " ".join((query or "").split())
    if len(query) < MIN_SEARCH_LENGTH:
        return []
    # A QR scanner (or the camera) types the member's card link into the box.
    if member_services.CARD_LINK_PATTERN.fullmatch(query) or "/card/" in query:
        member = member_services.find_by_card_code(gym, query)
        return [assess(member)] if member else []
    members = member_services.search_members(gym, query)[:MAX_SEARCH_RESULTS]
    return [assess(member) for member in members]


def desk_branch(by: StaffAssignment, chosen: Branch | None = None) -> Branch:
    """
    The branch a check-in happens at: the staff member's own branch if they are
    limited to one, otherwise the one they chose, otherwise the gym's only branch.

    Raises BranchNeeded when the gym has several and none was chosen.
    """
    if by.branch_id is not None:
        return by.branch
    if chosen is not None:
        if chosen.gym_id != by.gym_id or not chosen.is_active:
            raise PermissionDenied("That branch belongs to a different gym.")
        return chosen
    branches = list(Branch.objects.for_gym(by.gym).filter(is_active=True)[:2])
    if len(branches) == 1:
        return branches[0]
    raise BranchNeeded("Choose which branch this desk is at.")


@transaction.atomic
def check_in(
    *,
    by: StaffAssignment,
    member: Member,
    branch: Branch | None = None,
    method: str = CheckInMethod.MANUAL,
) -> tuple[CheckIn, bool]:
    """
    Record that a member came in today.

    Returns the check-in and whether it is new. A second check-in on the same
    day returns the first one and records nothing. Raises NotAllowedIn when the
    member has no membership valid today, and BranchNeeded (see desk_branch).

    Check-ins are not written to the audit log: there are hundreds a day, and
    each one already records who admitted the member and when.
    """
    require_role(by, CHECK_IN_ROLES)
    _require_attendance(by.gym)
    if member.gym_id != by.gym_id:
        raise PermissionDenied("That member belongs to a different gym.")

    assessment = assess(member)
    if not assessment.allowed:
        raise NotAllowedIn(assessment)

    # get_or_create relies on the "one per member per day" rule in the database,
    # so two taps at the same moment still give one check-in.
    return CheckIn.objects.get_or_create(
        gym=by.gym,
        member=member,
        checked_in_on=timezone.localdate(),
        defaults={
            "branch": desk_branch(by, branch),
            "membership": assessment.membership,
            "method": method,
            "admitted_by": by.person,
        },
    )


def self_check_in(*, gym: Gym, branch: Branch, member: Member) -> tuple[CheckIn, bool]:
    """
    A member scanned their own card at the entrance tablet of `branch`.

    The caller has already checked that the request comes from a tablet
    connected to this gym. The same rules apply as at the desk.
    """
    _require_attendance(gym)
    if member.gym_id != gym.id or branch.gym_id != gym.id:
        raise PermissionDenied("That member belongs to a different gym.")
    assessment = assess(member)
    if not assessment.allowed:
        raise NotAllowedIn(assessment)
    return CheckIn.objects.get_or_create(
        gym=gym,
        member=member,
        checked_in_on=timezone.localdate(),
        defaults={
            "branch": branch,
            "membership": assessment.membership,
            "method": CheckInMethod.TABLET,
            "admitted_by": None,
        },
    )


def visit_days(member: Member, since: date) -> list[date]:
    """The days a member came in since a day: what their streak is made of."""
    return list(
        CheckIn.objects.filter(member=member, checked_in_on__gte=since).values_list(
            "checked_in_on", flat=True
        )
    )


def busiest_hours(gym: Gym, since: date) -> list[tuple[int, int]]:
    """Check-ins per hour of the day (Kigali time) since a day: [(hour, count), ...]."""
    counts: dict[int, int] = {}
    for at in (
        CheckIn.objects.for_gym(gym)
        .filter(checked_in_on__gte=since)
        .values_list("checked_in_at", flat=True)
    ):
        hour = timezone.localtime(at).hour
        counts[hour] = counts.get(hour, 0) + 1
    return sorted(counts.items())


def streak_at_risk(gym: Gym, today: date, goal: int = 2) -> list[Member]:
    """
    Members who reached the weekly goal last week but not yet this week: a
    friendly reminder now keeps their habit going. Only from Thursday on.
    """
    if today.weekday() < 3:
        return []
    this_week = today - timedelta(days=today.weekday())
    last_week = this_week - timedelta(days=7)
    visits = CheckIn.objects.for_gym(gym).filter(checked_in_on__gte=last_week)
    last, now = {}, {}
    for member_id, day in visits.values_list("member_id", "checked_in_on"):
        bucket = now if day >= this_week else last
        bucket[member_id] = bucket.get(member_id, 0) + 1
    ids = [m for m, n in last.items() if n >= goal and now.get(m, 0) < goal]
    return list(Member.objects.for_gym(gym).filter(id__in=ids).order_by("full_name"))


def check_ins_on(gym: Gym, day: date):
    """Everyone who came in on a day, most recent first."""
    return (
        CheckIn.objects.for_gym(gym)
        .filter(checked_in_on=day)
        .select_related("member", "branch", "admitted_by")
    )


def visits_for(member: Member):
    """A member's visits, most recent first."""
    return CheckIn.objects.for_gym(member.gym).filter(member=member).select_related("branch")


def quiet_members(gym: Gym, today: date, after_days: int = 3, before_days: int = 7):
    """
    Members who still have a valid membership but whose last visit was between
    `after_days` and `before_days` ago: people slipping away, while a short
    message can still bring them back. Longest absent first.
    """
    last_allowed = today - timedelta(days=after_days)
    first_allowed = today - timedelta(days=before_days)
    recent = (
        CheckIn.objects.for_gym(gym)
        .values("member_id")
        .annotate(last_visit=Max("checked_in_on"))
        .filter(last_visit__range=(first_allowed, last_allowed))
    )
    members = member_services.members_with_valid_membership(gym, today).filter(
        id__in=Subquery(recent.values("member_id"))
    )
    return members.select_related("person").order_by("full_name")


# --- Figures for the owner --------------------------------------------------


def check_ins_between(gym: Gym, start: date, end: date):
    """Visits between two days, both included, in the order they happened."""
    return (
        CheckIn.objects.for_gym(gym)
        .filter(checked_in_on__range=(start, end))
        .select_related("branch", "admitted_by")
        .order_by("checked_in_at")
    )


def check_in_count(gym: Gym, start: date, end: date) -> int:
    """Visits between two days, both included."""
    return CheckIn.objects.for_gym(gym).filter(checked_in_on__range=(start, end)).count()


def not_visiting(gym: Gym, today: date, days: int = 14):
    """
    Members with a membership valid today who have not come in for `days` days.
    These are the members most likely not to renew.

    Members who joined less than `days` days ago are left out: they have not
    had the chance yet.
    """
    since = today - timedelta(days=days)
    recent = CheckIn.objects.filter(member=OuterRef("pk"), checked_in_on__gt=since)
    return (
        member_services.members_with_valid_membership(gym, today)
        .filter(joined_on__lte=since)
        .filter(~Exists(recent))
    )


# --- Bringing a friend ------------------------------------------------------
#
# Each gym decides its own rule: a number of free guests per member per month,
# or zero, which means the friend pays the day price at reception. The rule is
# applied here, so the desk cannot give away visits the owner did not allow.


def guest_rule(gym: Gym) -> GuestRule:
    """This gym's rule, made with the default the first time it is asked for."""
    rule, _created = GuestRule.objects.get_or_create(gym=gym)
    return rule


@transaction.atomic
def set_guest_rule(*, by: StaffAssignment, free_per_month: int) -> GuestRule:
    """Managers set how many friends a member may bring free each month."""
    require_role(by, MANAGER_ROLES)
    if free_per_month < 0 or free_per_month > 31:
        raise member_services.InvalidDetails("free_per_month", "out_of_range")
    rule = guest_rule(by.gym)
    rule.free_per_month = free_per_month
    rule.updated_by = by.person
    rule.save(update_fields=["free_per_month", "updated_by", "updated_at"])
    audit.record(
        action="guest.rule_set",
        summary=f"Members may bring {free_per_month} free guest(s) a month",
        gym=by.gym,
        actor=by.person,
        target=rule,
    )
    return rule


def free_guests_left(member: Member, today: date) -> int:
    """How many free guests this member has left this calendar month."""
    allowed = guest_rule(member.gym).free_per_month
    used = GuestVisit.objects.for_gym(member.gym).filter(
        host=member, was_free=True, came_on__year=today.year, came_on__month=today.month
    )
    return max(0, allowed - used.count())


@transaction.atomic
def bring_a_friend(
    *,
    by: StaffAssignment,
    member: Member,
    guest_name: str,
    branch: Branch,
    guest_phone: str = "",
    today: date | None = None,
) -> GuestVisit:
    """
    Write down a friend a member brought in. Whether it is free follows the
    gym's own rule; when it is not, the desk charges the day price as usual.
    Raises NotAllowedIn if the member themselves may not come in today.
    """
    require_role(by, CHECK_IN_ROLES)
    if member.gym_id != by.gym_id or branch.gym_id != by.gym_id:
        raise member_services.InvalidDetails("member", "different_gym")
    guest_name = " ".join((guest_name or "").split())
    if not guest_name:
        raise member_services.InvalidDetails("guest_name", "required")
    day = today or timezone.localdate()
    assessment = assess(member, on=day)
    if not assessment.allowed:
        raise NotAllowedIn(assessment)
    phone = (guest_phone or "").strip()
    if phone:
        try:
            phone = normalize_phone(phone)
        except InvalidPhoneNumber:
            raise member_services.InvalidDetails("guest_phone", "invalid") from None
    visit = GuestVisit.objects.create(
        gym=by.gym,
        host=member,
        branch=branch,
        guest_name=guest_name,
        guest_phone=phone,
        came_on=day,
        was_free=free_guests_left(member, day) > 0,
        recorded_by=by.person,
    )
    audit.record(
        action="guest.visited",
        summary=f"{member.full_name} brought {guest_name} in",
        gym=by.gym,
        actor=by.person,
        target=visit,
        details={"free": visit.was_free},
    )
    return visit


def guests_today(gym: Gym, today: date, branch: Branch | None = None):
    """Friends brought in today, for the desk."""
    visits = GuestVisit.objects.for_gym(gym).filter(came_on=today).select_related("host", "branch")
    return visits.filter(branch=branch) if branch else visits


# --- What a member did on a visit -------------------------------------------


def choices_for(member: Member, day: date | None = None):
    """
    What this member may tick when they come in, and what pays for each one.

    A package lists the services it includes, so somebody who bought swimming
    only is not offered the gym. Anything not on this list is paid for at the
    desk like any walk-in.
    """
    return member_services.allowed_services(member, day)


@transaction.atomic
def record_activities(
    *, by: StaffAssignment, check_in: CheckIn, services_chosen
) -> list[CheckInActivity]:
    """
    Write down what a member said they were doing. Check-in roles.

    Only services their package covers are accepted. Where a service comes out
    of a book of prepaid visits, one visit is taken from the book now, so the
    count cannot drift from what was actually used.
    """
    require_role(by, CHECK_IN_ROLES)
    if check_in.gym_id != by.gym_id:
        raise PermissionDenied("That check-in belongs to a different gym.")

    allowed = {
        item.service.pk: item for item in choices_for(check_in.member, check_in.checked_in_on)
    }
    already = set(check_in.activities.values_list("service_id", flat=True))
    written = []
    for service in services_chosen:
        if service.pk in already:
            continue
        item = allowed.get(service.pk)
        if item is None:
            raise member_services.InvalidDetails("services", "not_included")
        use = None
        if item.by_ticket:
            use = member_services.spend_ticket(
                by=by,
                ticketbook=item.ticketbook,
                service=service,
                used_on=check_in.checked_in_on,
            )
        written.append(
            CheckInActivity.objects.create(
                gym=by.gym,
                check_in=check_in,
                service=service,
                service_name=service.name,
                ticket_use=use,
            )
        )
    return written


@transaction.atomic
def cancel_activity(
    *, by: StaffAssignment, activity: CheckInActivity, reason: str
) -> CheckInActivity:
    """
    Take a service off a member's visit because it was ticked by mistake.
    Check-in roles, and only on the day, like a company visit.

    A prepaid visit that was spent goes back to the member. Nothing is deleted.
    """
    require_role(by, CHECK_IN_ROLES)
    if activity.gym_id != by.gym_id:
        raise PermissionDenied("That record belongs to a different gym.")
    reason = " ".join((reason or "").split())
    if not reason:
        raise member_services.InvalidDetails("reason", "required")
    if activity.check_in.checked_in_on != timezone.localdate():
        raise member_services.InvalidDetails("activity", "too_late")
    if activity.cancelled_at is not None:
        return activity

    if activity.ticket_use_id is not None:
        member_services.cancel_ticket_use(by=by, use=activity.ticket_use, reason=reason)
    activity.cancelled_at = timezone.now()
    activity.cancelled_by = by.person
    activity.cancel_reason = reason[:200]
    activity.save(update_fields=["cancelled_at", "cancelled_by", "cancel_reason"])
    return activity


def activities_of(check_in: CheckIn) -> list[str]:
    """The services still standing on a visit, for showing in a list."""
    return [
        activity.service_name
        for activity in check_in.activities.all()
        if activity.cancelled_at is None
    ]


@transaction.atomic
def record_self_activities(
    *, gym: Gym, check_in: CheckIn, services_chosen
) -> list[CheckInActivity]:
    """
    What a member ticked for themselves at the entrance tablet.

    There is no member of staff behind this, so the rules are checked here
    rather than against a role: the check-in must belong to this gym, and only
    services the member's own package covers are accepted.
    """
    if check_in.gym_id != gym.pk:
        raise PermissionDenied("That check-in belongs to a different gym.")

    allowed = {
        item.service.pk: item for item in choices_for(check_in.member, check_in.checked_in_on)
    }
    already = set(check_in.activities.values_list("service_id", flat=True))
    written = []
    for service in services_chosen:
        if service.pk in already:
            continue
        item = allowed.get(service.pk)
        if item is None:
            raise member_services.InvalidDetails("services", "not_included")
        use = None
        if item.by_ticket:
            use = member_services.spend_ticket_at_tablet(
                gym=gym,
                ticketbook=item.ticketbook,
                service=service,
                used_on=check_in.checked_in_on,
            )
        written.append(
            CheckInActivity.objects.create(
                gym=gym,
                check_in=check_in,
                service=service,
                service_name=service.name,
                ticket_use=use,
            )
        )
    return written
