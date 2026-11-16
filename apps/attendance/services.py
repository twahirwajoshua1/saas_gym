"""
Business rules for checking members in.

The central question is `assess(member)`: may this member come in today, and if
not, why not? The screen shows the answer; `check_in` enforces it.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.attendance.models import CheckIn, CheckInMethod
from apps.billing import services as billing_services
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import CHECK_IN_ROLES, require_role
from apps.members import services as member_services
from apps.members.models import Member, Membership
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS

# A membership with this many days left (or fewer) gets a "renew soon" warning.
EXPIRING_SOON_DAYS = 7

MAX_SEARCH_RESULTS = 8
MIN_SEARCH_LENGTH = 2


class Reason:
    """Why a member may not come in."""

    NO_MEMBERSHIP = "no_membership"
    EXPIRED = "expired"
    NOT_STARTED = "not_started"


@dataclass(frozen=True)
class Assessment:
    """The answer to "may this member come in today?", with what the desk should know."""

    member: Member
    allowed: bool
    reason: str = ""  # one of Reason, when not allowed
    membership: Membership | None = None  # the valid one, or the one that explains the refusal
    days_left: int = 0
    expiring_soon: bool = False
    owes_rwf: int = 0
    already_today: CheckIn | None = None


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
            expiring_soon=days_left <= EXPIRING_SOON_DAYS,
            owes_rwf=owes,
            already_today=already,
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
