"""
Business rules for members and packages (the Gym Operations application).

Every function that changes something takes `by`: the staff assignment of the
person doing it. That one value says who they are, which gym they act for and
what their role is. The function checks the role, makes the change and records
it in the audit log, all in one database transaction.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit import services as audit
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import FRONT_DESK_ROLES, OWNER_ONLY, require_role
from apps.members.durations import end_date_for
from apps.members.models import DurationUnit, Gender, Member, Membership, Package
from apps.people import services as people_services
from apps.people.phones import InvalidPhoneNumber, normalize_phone

MAX_PRICE_RWF = 100_000_000
MAX_DURATION_COUNT = 999

# How far from today a membership may start. Reception sometimes records a sale
# a few days late, or sells a renewal in advance.
MAX_DAYS_BACKDATED = 31
MAX_DAYS_AHEAD = 366


class InvalidDetails(ValueError):
    """Something entered is missing or wrong. `field` says which input."""

    def __init__(self, field: str, reason: str):
        self.field, self.reason = field, reason
        super().__init__(f"{field}: {reason}")


class AlreadyMember(Exception):
    """This phone number already belongs to a member of this gym."""

    def __init__(self, member: Member):
        self.member = member
        super().__init__(f"{member.full_name} is already a member.")


def _clean_text(value) -> str:
    return " ".join((value or "").split())


def _same_gym(by: StaffAssignment, record) -> None:
    if record.gym_id != by.gym_id:
        raise PermissionDenied("That record belongs to a different gym.")


# --- Packages ---------------------------------------------------------------


def _clean_package(gym: Gym, *, name, price_rwf, duration_count, duration_unit, existing=None):
    name = _clean_text(name)
    if not name:
        raise InvalidDetails("name", "required")
    same_name = Package.objects.for_gym(gym).filter(name__iexact=name)
    if existing is not None:
        same_name = same_name.exclude(pk=existing.pk)
    if same_name.exists():
        raise InvalidDetails("name", "duplicate")
    if not isinstance(price_rwf, int) or not 0 < price_rwf <= MAX_PRICE_RWF:
        raise InvalidDetails("price_rwf", "out_of_range")
    if not isinstance(duration_count, int) or not 0 < duration_count <= MAX_DURATION_COUNT:
        raise InvalidDetails("duration_count", "out_of_range")
    if duration_unit not in DurationUnit.values:
        raise InvalidDetails("duration_unit", "invalid")
    return {
        "name": name,
        "price_rwf": price_rwf,
        "duration_count": duration_count,
        "duration_unit": duration_unit,
    }


@transaction.atomic
def create_package(
    *, by: StaffAssignment, name: str, price_rwf: int, duration_count: int, duration_unit: str
) -> Package:
    """Add something the gym sells. Owner only."""
    require_role(by, OWNER_ONLY)
    values = _clean_package(
        by.gym,
        name=name,
        price_rwf=price_rwf,
        duration_count=duration_count,
        duration_unit=duration_unit,
    )
    package = Package.objects.create(gym=by.gym, **values)
    audit.record(
        action="package.created",
        summary=f"Package '{package.name}' was added at RWF {package.price_rwf:,}",
        gym=by.gym,
        actor=by.person,
        target=package,
        details=values,
    )
    return package


@transaction.atomic
def update_package(
    *,
    by: StaffAssignment,
    package: Package,
    name: str,
    price_rwf: int,
    duration_count: int,
    duration_unit: str,
) -> Package:
    """
    Change a package. Owner only.

    The audit log keeps the old and new values. Memberships already sold are
    not affected: each sale will keep its own copy of the price and duration.
    """
    require_role(by, OWNER_ONLY)
    _same_gym(by, package)
    values = _clean_package(
        by.gym,
        name=name,
        price_rwf=price_rwf,
        duration_count=duration_count,
        duration_unit=duration_unit,
        existing=package,
    )
    changes = {
        field: {"from": getattr(package, field), "to": new}
        for field, new in values.items()
        if getattr(package, field) != new
    }
    if not changes:
        return package

    for field, new in values.items():
        setattr(package, field, new)
    package.save()
    audit.record(
        action="package.updated",
        summary=f"Package '{package.name}' was changed ({', '.join(changes)})",
        gym=by.gym,
        actor=by.person,
        target=package,
        details=changes,
    )
    return package


@transaction.atomic
def set_package_active(*, by: StaffAssignment, package: Package, active: bool) -> Package:
    """Archive a package so it can no longer be sold, or bring it back. Owner only."""
    require_role(by, OWNER_ONLY)
    _same_gym(by, package)
    if package.is_active == active:
        return package
    package.is_active = active
    package.save()
    audit.record(
        action="package.restored" if active else "package.archived",
        summary=f"Package '{package.name}' was {'restored' if active else 'archived'}",
        gym=by.gym,
        actor=by.person,
        target=package,
    )
    return package


# --- Members ----------------------------------------------------------------


@dataclass(frozen=True)
class MemberDetails:
    """The gym's own details about a member (everything except the phone number)."""

    full_name: str
    branch: Branch | None = None
    gender: str = ""
    date_of_birth: date | None = None
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""
    notes: str = ""


def _clean_member(gym: Gym, details: MemberDetails) -> dict:
    full_name = _clean_text(details.full_name)
    if not full_name:
        raise InvalidDetails("full_name", "required")

    branch = details.branch
    if branch is None:
        branches = list(Branch.objects.for_gym(gym).filter(is_active=True)[:2])
        if len(branches) != 1:
            raise InvalidDetails("branch", "required")
        branch = branches[0]
    elif branch.gym_id != gym.id:
        raise InvalidDetails("branch", "invalid")

    if details.gender and details.gender not in Gender.values:
        raise InvalidDetails("gender", "invalid")
    if details.date_of_birth and details.date_of_birth > timezone.localdate():
        raise InvalidDetails("date_of_birth", "in_future")

    emergency_phone = _clean_text(details.emergency_contact_phone)
    if emergency_phone:
        try:
            emergency_phone = normalize_phone(emergency_phone)
        except InvalidPhoneNumber:
            raise InvalidDetails("emergency_contact_phone", "invalid") from None

    return {
        "full_name": full_name,
        "branch": branch,
        "gender": details.gender or "",
        "date_of_birth": details.date_of_birth,
        "emergency_contact_name": _clean_text(details.emergency_contact_name),
        "emergency_contact_phone": emergency_phone,
        "notes": (details.notes or "").strip(),
    }


@transaction.atomic
def register_member(*, by: StaffAssignment, phone: str, details: MemberDetails) -> Member:
    """
    Register a member of the gym. Front-desk roles.

    Raises InvalidDetails, or AlreadyMember if the phone number already belongs
    to a member of this gym (the error carries that member).
    """
    require_role(by, FRONT_DESK_ROLES)
    values = _clean_member(by.gym, details)
    try:
        standard_phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        raise InvalidDetails("phone", "invalid") from None

    # One Person per phone number on the whole platform. If the person is
    # already known (for example as a member of another gym), their record is
    # reused and left untouched. This gym's name for them is kept on the Member.
    person = people_services.find_person_by_phone(standard_phone)
    if person is None:
        person = people_services.create_person(full_name=values["full_name"], phone=standard_phone)

    existing = Member.objects.for_gym(by.gym).filter(person=person).first()
    if existing is not None:
        raise AlreadyMember(existing)

    member = Member.objects.create(gym=by.gym, person=person, registered_by=by.person, **values)
    audit.record(
        action="member.registered",
        summary=f"{member.full_name} was registered as a member",
        gym=by.gym,
        actor=by.person,
        target=member,
        details={"branch": values["branch"].public_id},
    )
    return member


@transaction.atomic
def update_member(*, by: StaffAssignment, member: Member, details: MemberDetails) -> Member:
    """
    Change the gym's details about a member. Front-desk roles.

    The phone number cannot be changed here: it identifies the person across
    the platform, and changing it safely is a separate feature.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, member)
    values = _clean_member(by.gym, details)

    changed = [field for field, new in values.items() if getattr(member, field) != new]
    if not changed:
        return member

    for field, new in values.items():
        setattr(member, field, new)
    member.save()
    audit.record(
        action="member.updated",
        summary=f"{member.full_name}'s details were changed ({', '.join(changed)})",
        gym=by.gym,
        actor=by.person,
        target=member,
        # Which fields changed, not their contents: these are personal details.
        details={"changed": changed},
    )
    return member


def search_members(gym: Gym, query: str = ""):
    """The gym's members, optionally narrowed by part of a name or phone number."""
    members = Member.objects.for_gym(gym).select_related("person", "branch")
    query = _clean_text(query)
    if not query:
        return members

    matches = Q(full_name__icontains=query)
    digits = re.sub(r"\D", "", query)
    if len(digits) >= 3:
        # "0788..." is stored as "+250788...", so the leading 0 must not be searched for.
        matches |= Q(person__phone__contains=digits.lstrip("0") or digits)
    return members.filter(matches)


# --- Memberships ------------------------------------------------------------


def memberships_for(member: Member):
    """A member's memberships, newest first, without cancelled ones hidden."""
    return Membership.objects.for_gym(member.gym).filter(member=member)


def current_membership(member: Member, on: date | None = None) -> Membership | None:
    """The membership that is valid on the given day (today by default), if any."""
    day = on or timezone.localdate()
    return (
        memberships_for(member)
        .filter(cancelled_at__isnull=True, start_date__lte=day, end_date__gte=day)
        .first()
    )


def next_start_date(member: Member) -> date:
    """
    The natural start for a new membership: the day after the member's last
    one ends, or today if they have none that reaches today.
    """
    today = timezone.localdate()
    latest = (
        memberships_for(member)
        .filter(cancelled_at__isnull=True, end_date__gte=today)
        .order_by("-end_date")
        .first()
    )
    return latest.end_date + timedelta(days=1) if latest else today


@transaction.atomic
def create_membership(
    *, by: StaffAssignment, member: Member, package: Package, start_date: date
) -> Membership:
    """
    Give a member a membership for a package, starting on a date. Front-desk roles.

    This only creates the membership. Charging for it is done by the billing
    module, which calls this function as part of a sale.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, member)
    _same_gym(by, package)
    if not package.is_active:
        raise InvalidDetails("package", "archived")

    today = timezone.localdate()
    earliest = today - timedelta(days=MAX_DAYS_BACKDATED)
    latest = today + timedelta(days=MAX_DAYS_AHEAD)
    if not isinstance(start_date, date) or not earliest <= start_date <= latest:
        raise InvalidDetails("start_date", "out_of_range")

    end_date = end_date_for(start_date, package.duration_count, package.duration_unit)
    overlapping = memberships_for(member).filter(
        cancelled_at__isnull=True, start_date__lte=end_date, end_date__gte=start_date
    )
    if overlapping.exists():
        raise InvalidDetails("start_date", "overlaps")

    membership = Membership.objects.create(
        gym=by.gym,
        member=member,
        package=package,
        package_name=package.name,
        price_rwf=package.price_rwf,
        duration_count=package.duration_count,
        duration_unit=package.duration_unit,
        start_date=start_date,
        end_date=end_date,
        sold_by=by.person,
    )
    audit.record(
        action="membership.created",
        summary=(
            f"{member.full_name} got {package.name} from {start_date:%d %b %Y} "
            f"to {end_date:%d %b %Y}"
        ),
        gym=by.gym,
        actor=by.person,
        target=membership,
        details={"member": member.public_id, "package": package.public_id},
    )
    return membership
