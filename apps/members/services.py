"""
Business rules for members and packages (the Gym Operations application).

Every function that changes something takes `by`: the staff assignment of the
person doing it. That one value says who they are, which gym they act for and
what their role is. The function checks the role, makes the change and records
it in the audit log, all in one database transaction.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from apps.audit import services as audit
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import FRONT_DESK_ROLES, MANAGER_ROLES, OWNER_ONLY, require_role
from apps.members.durations import end_date_for
from apps.members.models import (
    RENEWABLE_UNITS,
    DurationUnit,
    Gender,
    Member,
    Membership,
    MembershipFreeze,
    MembershipService,
    Package,
    PackageKind,
    PackageService,
    Service,
    SignUp,
    SignUpStatus,
    Ticketbook,
    TicketbookService,
    TicketUse,
)
from apps.people import services as people_services
from apps.people.phones import InvalidPhoneNumber, normalize_phone

MAX_PRICE_RWF = 100_000_000
MAX_DURATION_COUNT = 999
MAX_VISIT_COUNT = 999

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


def _clean_services(gym: Gym, services) -> list[Service]:
    """
    The services a package includes: all this gym's, all still on sale.

    `services=None` means every service the gym sells today. That is what a
    package meant before packages listed their services, so callers that do not
    care (a test, an import) keep the old behaviour.
    """
    if services is None:
        return list(Service.objects.for_gym(gym).filter(is_active=True))
    chosen = list(services)
    if not chosen:
        raise InvalidDetails("services", "required")
    ids = {service.pk for service in chosen}
    found = list(Service.objects.for_gym(gym).filter(pk__in=ids))
    if len(found) != len(ids):
        raise InvalidDetails("services", "unknown")
    if any(not service.is_active for service in found):
        raise InvalidDetails("services", "archived")
    return found


def _clean_package(
    gym: Gym,
    *,
    name,
    price_rwf,
    kind,
    services,
    duration_count,
    duration_unit,
    visit_count,
    existing=None,
):
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
    if kind not in PackageKind.values:
        raise InvalidDetails("kind", "invalid")

    chosen = _clean_services(gym, services)

    counts_visits = kind == PackageKind.COUNT
    if counts_visits:
        if not isinstance(visit_count, int) or not 0 < visit_count <= MAX_VISIT_COUNT:
            raise InvalidDetails("visit_count", "out_of_range")
        # Visits may run out after a while, or never. Both parts are left empty
        # together: "3" with no unit says nothing.
        if duration_count in (None, "") and not duration_unit:
            duration_count, duration_unit = None, ""
        elif not (
            isinstance(duration_count, int)
            and 0 < duration_count <= MAX_DURATION_COUNT
            and duration_unit in DurationUnit.values
        ):
            raise InvalidDetails("duration_count", "out_of_range")
    else:
        visit_count = None
        if not isinstance(duration_count, int) or not 0 < duration_count <= MAX_DURATION_COUNT:
            raise InvalidDetails("duration_count", "out_of_range")
        if duration_unit not in DurationUnit.values:
            raise InvalidDetails("duration_unit", "invalid")

    return {
        "name": name,
        "price_rwf": price_rwf,
        "kind": kind,
        "duration_count": duration_count,
        "duration_unit": duration_unit,
        "visit_count": visit_count,
    }, chosen


def _set_package_services(package: Package, services: list[Service]) -> None:
    wanted = {service.pk for service in services}
    package.service_links.exclude(service_id__in=wanted).delete()
    already = set(package.service_links.values_list("service_id", flat=True))
    PackageService.objects.bulk_create(
        [
            PackageService(gym_id=package.gym_id, package=package, service_id=service_id)
            for service_id in wanted - already
        ]
    )


@transaction.atomic
def create_package(
    *,
    by: StaffAssignment,
    name: str,
    price_rwf: int,
    services=None,
    kind: str = PackageKind.TIME,
    duration_count: int | None = None,
    duration_unit: str = "",
    visit_count: int | None = None,
) -> Package:
    """Add something the gym sells. Owner only."""
    require_role(by, OWNER_ONLY)
    values, chosen = _clean_package(
        by.gym,
        name=name,
        price_rwf=price_rwf,
        kind=kind,
        services=services,
        duration_count=duration_count,
        duration_unit=duration_unit,
        visit_count=visit_count,
    )
    package = Package.objects.create(gym=by.gym, **values)
    _set_package_services(package, chosen)
    audit.record(
        action="package.created",
        summary=f"Package '{package.name}' was added at RWF {package.price_rwf:,}",
        gym=by.gym,
        actor=by.person,
        target=package,
        details={**values, "services": [service.name for service in chosen]},
    )
    return package


@transaction.atomic
def update_package(
    *,
    by: StaffAssignment,
    package: Package,
    name: str,
    price_rwf: int,
    services=None,
    kind: str | None = None,
    duration_count: int | None = None,
    duration_unit: str = "",
    visit_count: int | None = None,
) -> Package:
    """
    Change a package. Owner only.

    The audit log keeps the old and new values. Memberships and ticketbooks
    already sold are not affected: each sale keeps its own copy of the price,
    the rule and the services it included.
    """
    require_role(by, OWNER_ONLY)
    _same_gym(by, package)
    # Leaving the services out means "keep the ones it already has".
    if services is None:
        services = list(package.included_services.all()) or None
    values, chosen = _clean_package(
        by.gym,
        name=name,
        price_rwf=price_rwf,
        kind=package.kind if kind is None else kind,
        services=services,
        duration_count=duration_count,
        duration_unit=duration_unit,
        visit_count=visit_count,
        existing=package,
    )
    changes = {
        field: {"from": getattr(package, field), "to": new}
        for field, new in values.items()
        if getattr(package, field) != new
    }
    was = sorted(service.name for service in package.included_services.all())
    now = sorted(service.name for service in chosen)
    if was != now:
        changes["services"] = {"from": was, "to": now}
    if not changes:
        return package

    for field, new in values.items():
        setattr(package, field, new)
    package.save()
    _set_package_services(package, chosen)
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


# --- Services (single sessions) ---------------------------------------------


def _clean_service(gym: Gym, *, name, price_rwf, existing=None):
    name = _clean_text(name)
    if not name:
        raise InvalidDetails("name", "required")
    same_name = Service.objects.for_gym(gym).filter(name__iexact=name)
    if existing is not None:
        same_name = same_name.exclude(pk=existing.pk)
    if same_name.exists():
        raise InvalidDetails("name", "duplicate")
    if not isinstance(price_rwf, int) or not 0 < price_rwf <= MAX_PRICE_RWF:
        raise InvalidDetails("price_rwf", "out_of_range")
    return {"name": name, "price_rwf": price_rwf}


def services_for(gym: Gym, *, active_only: bool = True):
    services = Service.objects.for_gym(gym)
    return services.filter(is_active=True) if active_only else services


@transaction.atomic
def create_service(*, by: StaffAssignment, name: str, price_rwf: int) -> Service:
    """Add a single session the gym sells, such as Sauna. Owner only."""
    require_role(by, OWNER_ONLY)
    values = _clean_service(by.gym, name=name, price_rwf=price_rwf)
    service = Service.objects.create(gym=by.gym, **values)
    audit.record(
        action="service.created",
        summary=f"Service '{service.name}' was added at RWF {service.price_rwf:,}",
        gym=by.gym,
        actor=by.person,
        target=service,
        details=values,
    )
    return service


@transaction.atomic
def update_service(*, by: StaffAssignment, service: Service, name: str, price_rwf: int) -> Service:
    """Rename or reprice a service. Sessions already sold keep their own price."""
    require_role(by, OWNER_ONLY)
    _same_gym(by, service)
    values = _clean_service(by.gym, name=name, price_rwf=price_rwf, existing=service)
    changes = {
        field: {"from": getattr(service, field), "to": new}
        for field, new in values.items()
        if getattr(service, field) != new
    }
    if not changes:
        return service
    for field, new in values.items():
        setattr(service, field, new)
    service.save()
    audit.record(
        action="service.updated",
        summary=f"Service '{service.name}' was changed ({', '.join(changes)})",
        gym=by.gym,
        actor=by.person,
        target=service,
        details=changes,
    )
    return service


@transaction.atomic
def set_service_active(*, by: StaffAssignment, service: Service, active: bool) -> Service:
    """Archive a service so it can no longer be sold, or bring it back. Owner only."""
    require_role(by, OWNER_ONLY)
    _same_gym(by, service)
    if service.is_active == active:
        return service
    service.is_active = active
    service.save()
    audit.record(
        action="service.restored" if active else "service.archived",
        summary=f"Service '{service.name}' was {'restored' if active else 'archived'}",
        gym=by.gym,
        actor=by.person,
        target=service,
    )
    return service


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


def check_registration(gym: Gym, *, phone: str, details: MemberDetails) -> str:
    """
    Check a member's details without saving anything (used by the spreadsheet
    import to show problems before anything is saved).

    Returns the phone number in standard form. Raises InvalidDetails, or
    AlreadyMember if the phone already belongs to a member of this gym.
    """
    _clean_member(gym, details)
    try:
        standard_phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        raise InvalidDetails("phone", "invalid") from None
    existing = Member.objects.for_gym(gym).filter(person__phone=standard_phone).first()
    if existing is not None:
        raise AlreadyMember(existing)
    return standard_phone


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

    member = Member.objects.create(
        gym=by.gym,
        person=person,
        registered_by=by.person,
        card_token=_new_card_token(),
        **values,
    )
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
    if package.is_count:
        raise InvalidDetails("package", "counts_visits")

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
    MembershipService.objects.bulk_create(
        [
            MembershipService(gym=by.gym, membership=membership, service=service)
            for service in package.included_services.all()
        ]
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


# --- Ticketbooks (packages counted in visits) -------------------------------


@transaction.atomic
def create_ticketbook(
    *, by: StaffAssignment, member: Member, package: Package, bought_on: date | None = None
) -> Ticketbook:
    """
    Give a member a book of prepaid visits, for example 15 swimming tickets.
    Front-desk roles.

    This only creates the book. Charging for it is done by the billing module,
    which calls this function as part of a sale.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, member)
    _same_gym(by, package)
    if not package.is_active:
        raise InvalidDetails("package", "archived")
    if not package.is_count:
        raise InvalidDetails("package", "counts_time")

    today = timezone.localdate()
    bought_on = today if bought_on is None else bought_on
    earliest = today - timedelta(days=MAX_DAYS_BACKDATED)
    if not isinstance(bought_on, date) or not earliest <= bought_on <= today:
        raise InvalidDetails("bought_on", "out_of_range")

    expires_on = None
    if package.expires:
        expires_on = end_date_for(bought_on, package.duration_count, package.duration_unit)

    book = Ticketbook.objects.create(
        gym=by.gym,
        member=member,
        package=package,
        package_name=package.name,
        price_rwf=package.price_rwf,
        visits_allowed=package.visit_count,
        bought_on=bought_on,
        expires_on=expires_on,
        sold_by=by.person,
    )
    TicketbookService.objects.bulk_create(
        [
            TicketbookService(gym=by.gym, ticketbook=book, service=service)
            for service in package.included_services.all()
        ]
    )
    audit.record(
        action="ticketbook.created",
        summary=(
            f"{member.full_name} got {package.name}: "
            f"{book.visits_allowed} visits, {book.expiry_label}"
        ),
        gym=by.gym,
        actor=by.person,
        target=book,
        details={"member": member.public_id, "package": package.public_id},
    )
    return book


def ticketbooks_for(member: Member):
    return Ticketbook.objects.for_gym(member.gym).filter(member=member)


def usable_ticketbooks(member: Member, day: date | None = None):
    """The member's books that still have a visit left and have not run out."""
    day = timezone.localdate() if day is None else day
    books = (
        ticketbooks_for(member)
        .filter(cancelled_at__isnull=True, bought_on__lte=day)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=day))
    )
    return [book for book in books.prefetch_related("included_services") if book.visits_left > 0]


@dataclass(frozen=True)
class Allowed:
    """What a member may use on a day, and what pays for it."""

    service: Service
    membership: Membership | None = None
    ticketbook: Ticketbook | None = None

    @property
    def by_ticket(self) -> bool:
        return self.ticketbook is not None


def allowed_services(member: Member, day: date | None = None) -> list[Allowed]:
    """
    Which services this member may use on a day without paying again.

    A membership covers its services as often as the member likes. A ticketbook
    covers its services while it still has visits. A service in neither list is
    paid for at the desk.
    """
    day = timezone.localdate() if day is None else day
    found: dict[int, Allowed] = {}

    membership = current_membership(member, day)
    if membership is not None:
        for service in membership.included_services.all():
            found[service.pk] = Allowed(service=service, membership=membership)

    for book in usable_ticketbooks(member, day):
        for service in book.included_services.all():
            if service.pk not in found:
                found[service.pk] = Allowed(service=service, ticketbook=book)

    return sorted(found.values(), key=lambda allowed: allowed.service.name)


@transaction.atomic
def spend_ticket(
    *, by: StaffAssignment, ticketbook: Ticketbook, service: Service, used_on: date | None = None
) -> TicketUse:
    """Take one visit out of a book. Front-desk roles."""
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, ticketbook)
    _same_gym(by, service)

    day = timezone.localdate() if used_on is None else used_on
    # Lock the book so two tills cannot spend the last visit at the same moment.
    locked = Ticketbook.objects.select_for_update().get(pk=ticketbook.pk)
    if not locked.is_usable_on(day):
        raise InvalidDetails("ticketbook", "used_up")
    if not locked.service_links.filter(service=service).exists():
        raise InvalidDetails("service", "not_included")

    use = TicketUse.objects.create(
        gym=by.gym,
        ticketbook=locked,
        service=service,
        service_name=service.name,
        used_on=day,
        recorded_by=by.person,
    )
    audit.record(
        action="ticket.used",
        summary=(
            f"{locked.member.full_name} used one {service.name} visit "
            f"from {locked.package_name} ({locked.visits_left} left)"
        ),
        gym=by.gym,
        actor=by.person,
        target=use,
    )
    return use


@transaction.atomic
def cancel_ticket_use(*, by: StaffAssignment, use: TicketUse, reason: str) -> TicketUse:
    """
    Put a visit back because it was taken by mistake. Front-desk roles.

    The use is not deleted: it stays on record, marked cancelled, with who
    cancelled it and why.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, use)
    reason = _clean_text(reason)
    if not reason:
        raise InvalidDetails("reason", "required")
    if use.cancelled_at is not None:
        return use

    use.cancelled_at = timezone.now()
    use.cancelled_by = by.person
    use.cancel_reason = reason
    use.save(update_fields=["cancelled_at", "cancelled_by", "cancel_reason"])
    audit.record(
        action="ticket.use_cancelled",
        summary=f"A {use.service_name} visit was put back: {reason}",
        gym=by.gym,
        actor=by.person,
        target=use,
        details={"reason": reason},
    )
    return use


# --- Figures for the owner --------------------------------------------------


def members_with_valid_membership(gym: Gym, day: date):
    """Members who have a membership valid on `day`."""
    valid = Membership.objects.for_gym(gym).filter(
        member=OuterRef("pk"), cancelled_at__isnull=True, start_date__lte=day, end_date__gte=day
    )
    return Member.objects.for_gym(gym).filter(Exists(valid))


def memberships_valid_on(gym: Gym, day: date):
    """Every membership valid on a day, across the gym's members."""
    return Membership.objects.for_gym(gym).filter(
        cancelled_at__isnull=True, start_date__lte=day, end_date__gte=day
    )


# The states the member list can be narrowed to, for the desk.
MEMBER_STATES = ("active", "soon", "lapsed")


def narrow_members(gym: Gym, members, state: str, today: date):
    """
    Narrow a list of members to one state: who may train now, whose membership
    runs out within a week, and who stopped in the last month. An unknown
    state changes nothing.
    """
    if state == "active":
        return members.filter(pk__in=members_with_valid_membership(gym, today).values("pk"))
    if state == "soon":
        ids = ending_soon(gym, today, 7).values("member_id")
        return members.filter(pk__in=ids)
    if state == "lapsed":
        ids = lapsed(gym, today, 30).values("member_id")
        return members.filter(pk__in=ids)
    return members


@dataclass(frozen=True)
class MemberStatus:
    """A member with the membership that explains where they stand today."""

    member: Member
    membership: Membership | None  # valid today, else the most recent one that ended
    is_active: bool
    days_left: int = 0

    @property
    def ends_on(self) -> date | None:
        return self.membership.end_date if self.membership else None


def status_for(members, today: date) -> list[MemberStatus]:
    """
    Where each of these members stands, in two queries however long the list
    is, so the desk's list stays fast on a gym with thousands of members.
    """
    members = list(members)
    if not members:
        return []
    gym = members[0].gym
    ids = [m.pk for m in members]
    live = Membership.objects.for_gym(gym).filter(
        member_id__in=ids, cancelled_at__isnull=True, start_date__lte=today, end_date__gte=today
    )
    current = {m.member_id: m for m in live.order_by("end_date")}
    missing = [i for i in ids if i not in current]
    last: dict[int, Membership] = {}
    if missing:
        past = Membership.objects.for_gym(gym).filter(
            member_id__in=missing, cancelled_at__isnull=True
        )
        for membership in past.order_by("member_id", "end_date"):
            last[membership.member_id] = membership  # the latest wins
    rows = []
    for member in members:
        live_one = current.get(member.pk)
        rows.append(
            MemberStatus(
                member=member,
                membership=live_one or last.get(member.pk),
                is_active=live_one is not None,
                days_left=(live_one.end_date - today).days if live_one else 0,
            )
        )
    return rows


def active_by_package(gym: Gym, day: date) -> list[tuple[str, int]]:
    """
    How many memberships valid today sit on each package ("Monthly: 84",
    "Annual: 12"). Biggest group first. The owner uses this to see how much of
    the gym is on long subscriptions.
    """
    rows = (
        memberships_valid_on(gym, day)
        .values("package_name")
        .annotate(people=Count("member_id", distinct=True))
        .order_by("-people", "package_name")
    )
    return [(row["package_name"], row["people"]) for row in rows]


def new_member_count(gym: Gym, start: date, end: date) -> int:
    """Members who joined between two days, both included."""
    return Member.objects.for_gym(gym).filter(joined_on__range=(start, end)).count()


def _not_renewed(memberships):
    """
    Keep only memberships worth following up (a month or longer, not day or
    week passes) with nothing bought to follow them.
    """
    later = Membership.objects.filter(
        member=OuterRef("member"), cancelled_at__isnull=True, start_date__gt=OuterRef("end_date")
    )
    return memberships.filter(duration_unit__in=RENEWABLE_UNITS).filter(~Exists(later))


def ending_soon(gym: Gym, today: date, within_days: int):
    """
    Memberships valid today that end within `within_days` days (today counts as
    the first day) and have not been renewed. Soonest first.
    """
    last_day = today + timedelta(days=within_days - 1)
    memberships = Membership.objects.for_gym(gym).filter(
        cancelled_at__isnull=True, start_date__lte=today, end_date__range=(today, last_day)
    )
    return _not_renewed(memberships).select_related("member__person").order_by("end_date", "id")


def lapsed(gym: Gym, today: date, within_days: int = 30):
    """
    Memberships that ended in the last `within_days` days and were not renewed:
    members the gym has probably lost. Most recent first.
    """
    first_day = today - timedelta(days=within_days)
    memberships = Membership.objects.for_gym(gym).filter(
        cancelled_at__isnull=True, end_date__range=(first_day, today - timedelta(days=1))
    )
    return _not_renewed(memberships).select_related("member__person").order_by("-end_date", "id")


# How far ahead an imported membership may end. Longer than any real package,
# short enough to catch a typing mistake such as 2062 for 2026.
MAX_IMPORTED_YEARS_AHEAD = 5


def find_package_by_name(gym: Gym, name: str) -> Package | None:
    """The gym's package with this name (capitals and spaces do not matter)."""
    return Package.objects.for_gym(gym).filter(name__iexact=_clean_text(name)).first()


def check_existing_membership(gym: Gym, *, package_name: str, valid_until) -> Package:
    """
    Check a membership the member already had before the gym used this system.

    Returns the package. Raises InvalidDetails.
    """
    package = find_package_by_name(gym, package_name)
    if package is None:
        raise InvalidDetails("package", "not_found")
    today = timezone.localdate()
    if not isinstance(valid_until, date):
        raise InvalidDetails("valid_until", "invalid")
    if valid_until < today:
        raise InvalidDetails("valid_until", "in_past")
    if valid_until > today.replace(year=today.year + MAX_IMPORTED_YEARS_AHEAD):
        raise InvalidDetails("valid_until", "too_far")
    return package


@transaction.atomic
def record_existing_membership(
    *, by: StaffAssignment, member: Member, package: Package, valid_until: date
) -> Membership:
    """
    Record a membership the member already had, brought in from the gym's
    previous records. Owner and branch manager.

    It is valid from today until `valid_until`. It has no invoice: whatever was
    paid for it was paid before the gym used this system. The real start date
    is not known, so today is used.
    """
    require_role(by, MANAGER_ROLES)
    _same_gym(by, member)
    _same_gym(by, package)
    check_existing_membership(by.gym, package_name=package.name, valid_until=valid_until)
    today = timezone.localdate()
    overlapping = memberships_for(member).filter(
        cancelled_at__isnull=True, start_date__lte=valid_until, end_date__gte=today
    )
    if overlapping.exists():
        raise InvalidDetails("valid_until", "overlaps")

    membership = Membership.objects.create(
        gym=by.gym,
        member=member,
        package=package,
        package_name=package.name,
        price_rwf=package.price_rwf,
        duration_count=package.duration_count,
        duration_unit=package.duration_unit,
        start_date=today,
        end_date=valid_until,
        sold_by=by.person,
        imported=True,
    )
    audit.record(
        action="membership.imported",
        summary=(
            f"{member.full_name}'s existing {package.name} membership, valid until "
            f"{valid_until:%d %b %Y}, was brought in"
        ),
        gym=by.gym,
        actor=by.person,
        target=membership,
        details={"member": member.public_id, "package": package.public_id},
    )
    return membership


# --- Freezing and extending ---------------------------------------------------

MAX_EXTEND_DAYS = 365
MAX_FREEZE_DAYS = 90


def freeze_on(membership: Membership, day: date) -> MembershipFreeze | None:
    """The freeze covering a day, if the membership is frozen then."""
    return freezes_for(membership).filter(start_date__lte=day, end_date__gte=day).first()


def freezes_for(membership: Membership):
    """A membership's freezes that count (not called off before they began)."""
    return MembershipFreeze.objects.for_gym(membership.gym).filter(
        membership=membership, cancelled=False
    )


def _adjustable(by: StaffAssignment, membership: Membership) -> None:
    require_role(by, MANAGER_ROLES)
    _same_gym(by, membership)
    if membership.cancelled_at is not None or membership.end_date < timezone.localdate():
        raise InvalidDetails("membership", "not_current")


def _move_end(membership: Membership, new_end: date) -> None:
    """Move a membership's last day, refusing if it would run into a later membership."""
    later = memberships_for(membership.member).filter(
        cancelled_at__isnull=True, start_date__gt=membership.end_date
    )
    clash = later.filter(start_date__lte=new_end).order_by("start_date").first()
    if clash is not None:
        raise InvalidDetails("days", "runs_into_next")
    membership.end_date = new_end
    membership.save(update_fields=["end_date"])


def _days(value, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise InvalidDetails("days", "out_of_range")
    return value


def _reason(value: str) -> str:
    reason = _clean_text(value)
    if not reason:
        raise InvalidDetails("reason", "required")
    return reason[:300]


@transaction.atomic
def extend_membership(
    *, by: StaffAssignment, membership: Membership, days: int, reason: str
) -> Membership:
    """Add days to the end of a current or coming membership. Owner and branch manager."""
    _adjustable(by, membership)
    days, reason = _days(days, MAX_EXTEND_DAYS), _reason(reason)
    old_end = membership.end_date
    _move_end(membership, old_end + timedelta(days=days))
    audit.record(
        action="membership.extended",
        summary=(
            f"{membership.member.full_name}'s {membership.package_name} was extended by "
            f"{days} days, to {membership.end_date:%d %b %Y}: {reason[:120]}"
        ),
        gym=by.gym,
        actor=by.person,
        target=membership,
        details={"days": days, "end_before": old_end, "reason": reason},
    )
    return membership


@transaction.atomic
def freeze_membership(
    *, by: StaffAssignment, membership: Membership, start: date, days: int, reason: str
) -> MembershipFreeze:
    """
    Pause a membership for some days from a start date. The member may not come
    in during the freeze, and the membership ends that many days later.
    Owner and branch manager.
    """
    _adjustable(by, membership)
    days, reason = _days(days, MAX_FREEZE_DAYS), _reason(reason)
    today = timezone.localdate()
    if not isinstance(start, date) or start < max(today, membership.start_date):
        raise InvalidDetails("start", "out_of_range")
    if start > membership.end_date:
        raise InvalidDetails("start", "after_end")
    end = start + timedelta(days=days - 1)
    if freezes_for(membership).filter(start_date__lte=end, end_date__gte=start).exists():
        raise InvalidDetails("start", "overlaps")

    _move_end(membership, membership.end_date + timedelta(days=days))
    freeze = MembershipFreeze.objects.create(
        gym=by.gym,
        membership=membership,
        start_date=start,
        end_date=end,
        reason=reason,
        created_by=by.person,
    )
    audit.record(
        action="membership.frozen",
        summary=(
            f"{membership.member.full_name}'s {membership.package_name} was frozen from "
            f"{start:%d %b} to {end:%d %b %Y}; it now ends {membership.end_date:%d %b %Y}"
        ),
        gym=by.gym,
        actor=by.person,
        target=membership,
        details={"start": start, "days": days, "reason": reason},
    )
    return freeze


@transaction.atomic
def end_freeze(*, by: StaffAssignment, freeze: MembershipFreeze) -> MembershipFreeze:
    """
    End a freeze before its planned end, for example when the member comes back
    early. The days not used are taken off the membership's end again.
    Owner and branch manager.
    """
    require_role(by, MANAGER_ROLES)
    _same_gym(by, freeze)
    today = timezone.localdate()
    if freeze.ended_early_at is not None or freeze.end_date < today:
        raise InvalidDetails("freeze", "already_over")

    membership = freeze.membership
    if freeze.start_date >= today:
        # Not begun, or begins today: call it off entirely, so the member can
        # come in today.
        unused = freeze.days
        freeze.cancelled = True
    else:
        # Begun on an earlier day: it ends yesterday.
        unused = (freeze.end_date - today).days + 1
        freeze.end_date = today - timedelta(days=1)
    membership.end_date -= timedelta(days=unused)
    membership.save(update_fields=["end_date"])
    freeze.ended_early_at = timezone.now()
    freeze.save(update_fields=["end_date", "ended_early_at", "cancelled"])
    audit.record(
        action="membership.freeze_ended",
        summary=(
            f"The freeze on {membership.member.full_name}'s {membership.package_name} was ended "
            f"early; {unused} unused days were taken off, so it now ends "
            f"{membership.end_date:%d %b %Y}"
        ),
        gym=by.gym,
        actor=by.person,
        target=membership,
        details={"unused_days": unused},
    )
    return freeze


# --- The member's card link ---------------------------------------------------

CARD_LINK_PATTERN = re.compile(r"(?:/card/)?([A-Za-z0-9_-]{32,64})/?")


def _new_card_token() -> str:
    return secrets.token_urlsafe(32)


@transaction.atomic
def create_card_link(*, by: StaffAssignment, member: Member, renew: bool = False) -> str:
    """
    Give the member a card link, or (with renew=True) a new one, after which the
    old link stops working. Front-desk roles. Returns the token.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, member)
    if member.card_token and not renew:
        return member.card_token
    member.card_token = _new_card_token()
    member.save(update_fields=["card_token"])
    audit.record(
        action="member.card_link_renewed" if renew else "member.card_link_created",
        summary=f"{'A new' if renew else 'A'} card link was made for {member.full_name}",
        gym=by.gym,
        actor=by.person,
        target=member,
    )
    return member.card_token


def find_by_card_code(gym: Gym, text: str) -> Member | None:
    """The gym's member whose card link or code this is (as read by a QR scanner)."""
    match = CARD_LINK_PATTERN.search((text or "").strip())
    if not match:
        return None
    return Member.objects.for_gym(gym).filter(card_token=match.group(1)).first()


def gym_id_for_card(token: str) -> int | None:
    """
    Which gym a card link belongs to, or None.

    A card is opened by someone who is not signed in, so no gym has been chosen
    and tenant isolation (lock 2) hides every member. This asks a small database
    function that may look up exactly one thing: the gym of the member holding
    this exact token. The caller then works within that gym only.
    """
    if not CARD_LINK_PATTERN.fullmatch(token or ""):
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT app_gym_for_card(%s)", [token])
        return cursor.fetchone()[0]


# --- Signing up on your own phone -------------------------------------------

SIGNUP_CODE_LENGTH = 4
# How many people may sign up for one gym in an hour from the public page.
# A form anybody can reach needs a ceiling, or one bored person fills the desk.
MAX_SIGNUPS_PER_HOUR = 40


class AlreadySignedUp(Exception):
    """This phone already has a sign-up waiting at this gym."""

    def __init__(self, signup):
        self.signup = signup
        super().__init__(f"{signup.full_name} already signed up; the code is {signup.code}.")


def packages_on_sale(gym: Gym):
    return Package.objects.for_gym(gym).filter(is_active=True).prefetch_related("included_services")


def waiting_signups(gym: Gym):
    return SignUp.objects.for_gym(gym).filter(status=SignUpStatus.WAITING).select_related("package")


@transaction.atomic
def create_signup(
    gym: Gym,
    *,
    full_name: str,
    phone: str,
    package: Package,
    gender: str = "",
    date_of_birth=None,
    emergency_contact_name: str = "",
    emergency_contact_phone: str = "",
    branch=None,
    wants_messages: bool = False,
) -> SignUp:
    """
    Somebody fills in the gym's own sign-up page. Nobody is signed in.

    Nothing is a membership yet: this only puts them on the desk's list with a
    short code. They pay at reception, and the receptionist turns it into a
    membership. Raises AlreadyMember if the phone is already one of this gym's
    members, and AlreadySignedUp if they filled the form in twice.
    """
    details = MemberDetails(
        full_name=full_name,
        gender=gender,
        date_of_birth=date_of_birth,
        emergency_contact_name=emergency_contact_name,
        emergency_contact_phone=emergency_contact_phone,
        branch=branch,
    )
    # The same checks as registering at the desk, minus the parts a person
    # filling in a form on their own phone cannot answer (branch, notes).
    values = _clean_member(gym, details)
    values = {
        field: value
        for field, value in values.items()
        if field
        in (
            "full_name",
            "gender",
            "date_of_birth",
            "emergency_contact_name",
            "emergency_contact_phone",
            "branch",
        )
    }
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber as error:
        raise InvalidDetails("phone", "invalid") from error

    if package.gym_id != gym.pk or not package.is_active:
        raise InvalidDetails("package", "archived")

    person = people_services.find_person_by_phone(phone)
    if person is not None:
        existing = Member.objects.for_gym(gym).filter(person=person).first()
        if existing is not None:
            raise AlreadyMember(existing)

    waiting = waiting_signups(gym).filter(phone=phone).first()
    if waiting is not None:
        raise AlreadySignedUp(waiting)

    hour_ago = timezone.now() - timedelta(hours=1)
    if SignUp.objects.for_gym(gym).filter(created_at__gte=hour_ago).count() >= (
        MAX_SIGNUPS_PER_HOUR
    ):
        raise InvalidDetails("gym", "too_many_signups")

    signup = SignUp.objects.create(
        gym=gym,
        code=f"{secrets.randbelow(10**SIGNUP_CODE_LENGTH):0{SIGNUP_CODE_LENGTH}d}",
        phone=phone,
        package=package,
        package_name=package.name,
        price_rwf=package.price_rwf,
        wants_messages=wants_messages,
        **values,
    )
    audit.record(
        action="signup.created",
        summary=f"{signup.full_name} signed up on their phone for {package.name}",
        gym=gym,
        actor=None,
        target=signup,
    )
    return signup


@transaction.atomic
def register_from_signup(*, by: StaffAssignment, signup: SignUp) -> Member:
    """
    Turn a sign-up into a member once they are at the desk. Front-desk roles.

    Selling them the package and taking the money is the billing module's work;
    this only creates the member and marks the sign-up handled.
    """
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, signup)
    if signup.status != SignUpStatus.WAITING:
        raise InvalidDetails("signup", "already_handled")

    member = register_member(
        by=by,
        phone=signup.phone,
        details=MemberDetails(
            full_name=signup.full_name,
            gender=signup.gender,
            date_of_birth=signup.date_of_birth,
            emergency_contact_name=signup.emergency_contact_name,
            emergency_contact_phone=signup.emergency_contact_phone,
            branch=signup.branch,
        ),
    )
    signup.status = SignUpStatus.JOINED
    signup.member = member
    signup.handled_at, signup.handled_by = timezone.now(), by.person
    signup.save(update_fields=["status", "member", "handled_at", "handled_by"])
    return member


@transaction.atomic
def drop_signup(*, by: StaffAssignment, signup: SignUp) -> SignUp:
    """Take a sign-up off the desk's list: they did not come. Front-desk roles."""
    require_role(by, FRONT_DESK_ROLES)
    _same_gym(by, signup)
    if signup.status != SignUpStatus.WAITING:
        return signup
    signup.status = SignUpStatus.DROPPED
    signup.handled_at, signup.handled_by = timezone.now(), by.person
    signup.save(update_fields=["status", "handled_at", "handled_by"])
    audit.record(
        action="signup.dropped",
        summary=f"{signup.full_name}'s sign-up was taken off the list",
        gym=by.gym,
        actor=by.person,
        target=signup,
    )
    return signup


@transaction.atomic
def spend_ticket_at_tablet(
    *, gym: Gym, ticketbook: Ticketbook, service: Service, used_on: date | None = None
) -> TicketUse:
    """
    Take one visit out of a book when the member did it themselves at the
    entrance tablet. No member of staff is involved, so there is no role to
    check: the caller has already proved the tablet belongs to this gym.
    """
    if ticketbook.gym_id != gym.pk or service.gym_id != gym.pk:
        raise PermissionDenied("That record belongs to a different gym.")
    day = timezone.localdate() if used_on is None else used_on
    locked = Ticketbook.objects.select_for_update().get(pk=ticketbook.pk)
    if not locked.is_usable_on(day):
        raise InvalidDetails("ticketbook", "used_up")
    if not locked.service_links.filter(service=service).exists():
        raise InvalidDetails("service", "not_included")

    use = TicketUse.objects.create(
        gym=gym,
        ticketbook=locked,
        service=service,
        service_name=service.name,
        used_on=day,
        recorded_by=None,
    )
    audit.record(
        action="ticket.used",
        summary=(
            f"{locked.member.full_name} used one {service.name} visit at the tablet "
            f"from {locked.package_name} ({locked.visits_left} left)"
        ),
        gym=gym,
        actor=None,
        target=use,
    )
    return use


# --- The price grid -----------------------------------------------------------

# The lengths a gym sells a single service for. The order is the order the
# columns appear in, shortest first, which is how an owner thinks about price.
GRID_LENGTHS = (
    ("day", 1, DurationUnit.DAY, "Day pass"),
    ("month", 1, DurationUnit.MONTH, "Monthly"),
    ("quarter", 3, DurationUnit.MONTH, "Quarterly"),
    ("year", 1, DurationUnit.YEAR, "Annual"),
)
GRID_BY_KEY = {key: (count, unit, label) for key, count, unit, label in GRID_LENGTHS}


@dataclass(frozen=True)
class GridCell:
    """One box in the grid: a service for a length, priced or not."""

    key: str
    label: str
    package: Package | None

    @property
    def price_rwf(self) -> int | None:
        return self.package.price_rwf if self.package else None

    @property
    def field_name(self) -> str:
        return f"price-{self.service_id}-{self.key}"


@dataclass(frozen=True)
class GridRow:
    """One service, across every length, plus whatever tickets it sells."""

    service: Service
    cells: list[GridCell]
    bundles: list[Package]

    @property
    def name(self) -> str:
        return self.service.name


def _single_service_packages(gym: Gym) -> dict[tuple[int, str], Package]:
    """
    The packages that the grid owns: one service, for a length.

    A package the grid made is recognised by its shape rather than by a flag,
    because that shape is exactly what a grid box means. A package covering
    two services, or a ticketbook, belongs to the builder below the grid and
    is never touched from here.
    """
    found: dict[tuple[int, str], Package] = {}
    packages = (
        Package.objects.for_gym(gym)
        .filter(kind=PackageKind.TIME, is_active=True)
        .prefetch_related("included_services")
        .order_by("id")
    )
    for package in packages:
        services = list(package.included_services.all())
        if len(services) != 1:
            continue
        for key, count, unit, _label in GRID_LENGTHS:
            if package.duration_count == count and package.duration_unit == unit:
                found.setdefault((services[0].pk, key), package)
                break
    return found


def price_grid(gym: Gym) -> list[GridRow]:
    """Every service the gym sells, with the prices set against it."""
    owned = _single_service_packages(gym)
    bundles = _ticket_bundles(gym)
    rows = []
    for service in Service.objects.for_gym(gym).filter(is_active=True).order_by("name"):
        cells = [
            GridCell(key=key, label=label, package=owned.get((service.pk, key)))
            for key, _count, _unit, label in GRID_LENGTHS
        ]
        for cell in cells:
            object.__setattr__(cell, "service_id", service.pk)
        rows.append(GridRow(service=service, cells=cells, bundles=bundles.get(service.pk, [])))
    return rows


def _ticket_bundles(gym: Gym) -> dict[int, list[Package]]:
    """
    Ticketbooks for a single service, by service.

    Tickets are not a grid box. A gym sells 5, 10 or 20 at different prices,
    and the bigger bundle is usually cheaper per visit, so each size is its
    own thing with its own price.
    """
    found: dict[int, list[Package]] = {}
    packages = (
        Package.objects.for_gym(gym)
        .filter(kind=PackageKind.COUNT, is_active=True)
        .prefetch_related("included_services")
        .order_by("visit_count")
    )
    for package in packages:
        services = list(package.included_services.all())
        if len(services) == 1:
            found.setdefault(services[0].pk, []).append(package)
    return found


def _grid_name(service: Service, key: str) -> str:
    _count, _unit, label = GRID_BY_KEY[key]
    return f"{service.name}, {label.lower()}"


@transaction.atomic
def save_price_grid(*, by: StaffAssignment, prices: dict[tuple[int, str], int | None]) -> int:
    """
    Write the grid back. Owner only.

    A box with a price in it becomes a package the gym sells. A box emptied
    stops that package being sold — it is archived, never deleted, because
    everyone who already bought it keeps what they bought.

    Returns how many packages changed, so the owner is told something true.
    """
    require_role(by, OWNER_ONLY)
    owned = _single_service_packages(by.gym)
    services = {
        service.pk: service for service in Service.objects.for_gym(by.gym).filter(is_active=True)
    }
    changed = 0
    for (service_id, key), price in prices.items():
        service = services.get(service_id)
        if service is None or key not in GRID_BY_KEY:
            continue
        count, unit, _label = GRID_BY_KEY[key]
        package = owned.get((service_id, key))
        if price is None:
            if package is not None:
                set_package_active(by=by, package=package, active=False)
                changed += 1
            continue
        if package is None:
            create_package(
                by=by,
                name=_unused_name(by.gym, _grid_name(service, key)),
                price_rwf=price,
                services=[service],
                kind=PackageKind.TIME,
                duration_count=count,
                duration_unit=unit,
            )
            changed += 1
        elif package.price_rwf != price:
            update_package(
                by=by,
                package=package,
                name=package.name,
                price_rwf=price,
                services=[service],
                kind=PackageKind.TIME,
                duration_count=count,
                duration_unit=unit,
            )
            changed += 1
    return changed


def _unused_name(gym: Gym, wanted: str) -> str:
    """Package names are unique per gym, and the owner did not choose this one."""
    name = wanted
    number = 2
    while Package.objects.for_gym(gym).filter(name__iexact=name).exists():
        name = f"{wanted} ({number})"
        number += 1
    return name


def add_ticket_bundle(
    *, by: StaffAssignment, service: Service, visits: int, price_rwf: int
) -> Package:
    """
    Sell a fixed number of visits to one service at one price. Owner only.

    Five tickets and twenty tickets are two different things to sell, because
    the twenty is usually cheaper per visit. So each is its own package.
    """
    require_role(by, OWNER_ONLY)
    return create_package(
        by=by,
        name=_unused_name(by.gym, f"{visits} {service.name.lower()} tickets"),
        price_rwf=price_rwf,
        services=[service],
        kind=PackageKind.COUNT,
        visit_count=visits,
    )
