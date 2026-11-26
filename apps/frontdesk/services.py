"""
Business rules for the desk and the entrance tablet.

The tablet lets people in by themselves: members with their card, company
staff with their card (whose code changes every 30 seconds) or their phone
number and PIN. Whatever the tablet cannot let through becomes a ScanProblem
that the receptionist sees under "Needs you".
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import quote

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.utils import timezone

from apps.attendance import services as attendance
from apps.attendance.models import CheckIn, CheckInMethod
from apps.audit import services as audit
from apps.core import streaks, tenant_db
from apps.corporate import services as corporate
from apps.corporate.models import CorporateVisit, Employee, VisitMethod
from apps.frontdesk.models import EntranceTablet, ProblemKind, ScanProblem
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import CHECK_IN_ROLES, MANAGER_ROLES, require_role
from apps.massage import services as massage
from apps.members import services as member_services
from apps.members.models import Member, Membership
from apps.members.services import InvalidDetails
from apps.people.phones import InvalidPhoneNumber, normalize_phone

PAIRING_MINUTES = 15
PROBLEM_HOURS = 3  # problems older than this drop off the desk
STREAK_LOOKBACK = timedelta(weeks=60)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# --- Connecting tablets (owner / branch manager) --------------------------------


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


@transaction.atomic
def add_tablet(*, by: StaffAssignment, branch: Branch, name: str) -> EntranceTablet:
    require_role(by, MANAGER_ROLES)
    attendance._require_attendance(by.gym)
    if branch.gym_id != by.gym_id:
        raise PermissionDenied("That branch belongs to a different gym.")
    name = " ".join((name or "").split())[:60] or "Entrance tablet"
    tablet = EntranceTablet.objects.create(
        gym=by.gym, branch=branch, name=name, created_by=by.person
    )
    _give_code(tablet)
    audit.record(
        action="tablet.added",
        summary=f"Entrance tablet '{tablet.name}' added at {branch.name}",
        gym=by.gym,
        actor=by.person,
        target=tablet,
    )
    return tablet


def _give_code(tablet: EntranceTablet) -> None:
    tablet.pairing_code = _new_code()
    tablet.pairing_expires_at = timezone.now() + timedelta(minutes=PAIRING_MINUTES)
    tablet.save(update_fields=["pairing_code", "pairing_expires_at"])


@transaction.atomic
def new_code(*, by: StaffAssignment, tablet: EntranceTablet) -> EntranceTablet:
    """A fresh connection code, for example to connect a replacement tablet."""
    require_role(by, MANAGER_ROLES)
    if tablet.gym_id != by.gym_id:
        raise PermissionDenied("That tablet belongs to a different gym.")
    _give_code(tablet)
    return tablet


@transaction.atomic
def disconnect(*, by: StaffAssignment, tablet: EntranceTablet) -> None:
    """Disconnect a tablet at once (lost, stolen or replaced). It needs a new code to come back."""
    require_role(by, MANAGER_ROLES)
    if tablet.gym_id != by.gym_id:
        raise PermissionDenied("That tablet belongs to a different gym.")
    tablet.secret_hash = ""
    tablet.pairing_code = ""
    tablet.save(update_fields=["secret_hash", "pairing_code"])
    audit.record(
        action="tablet.disconnected",
        summary=f"Entrance tablet '{tablet.name}' was disconnected",
        gym=by.gym,
        actor=by.person,
        target=tablet,
    )


def _lookup(function: str, value: str) -> tuple[int, int] | None:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT id, gym_id FROM {function}(%s)", [value])  # noqa: S608 - fixed names
        row = cursor.fetchone()
    return (row[0], row[1]) if row else None


@transaction.atomic
def connect(code: str) -> tuple[EntranceTablet, str] | None:
    """The tablet types its code. Returns the tablet and the secret it must keep."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    found = _lookup("frontdesk_tablet_for_code", code)
    if found is None:
        return None
    tenant_db.set_current_gym(found[1])
    tablet = EntranceTablet.objects.select_related("gym", "branch").get(pk=found[0])
    secret = secrets.token_urlsafe(32)
    tablet.secret_hash = _hash(secret)
    tablet.paired_at = timezone.now()
    tablet.pairing_code = ""
    tablet.save(update_fields=["secret_hash", "paired_at", "pairing_code"])
    return tablet, secret


def tablet_for(secret: str | None) -> EntranceTablet | None:
    """The tablet carrying this secret, with the database switched to its gym."""
    if not secret:
        return None
    found = _lookup("frontdesk_tablet_for_secret", _hash(secret))
    if found is None:
        return None
    tenant_db.set_current_gym(found[1])
    tablet = EntranceTablet.objects.select_related("gym", "branch").get(pk=found[0])
    now = timezone.now()
    if tablet.last_seen_at is None or now - tablet.last_seen_at > timedelta(minutes=1):
        EntranceTablet.objects.filter(pk=tablet.pk).update(last_seen_at=now)
    return tablet


def tablets_for(gym: Gym):
    return EntranceTablet.objects.for_gym(gym).filter(is_active=True).select_related("branch")


# --- What the tablet shows ----------------------------------------------------------


@dataclass
class Welcome:
    first_name: str
    what: str  # e.g. "Monthly · 22 days left" or "WASAC · Gym + Sauna"
    streak: streaks.Streak
    company_line: str = ""  # e.g. "WASAC is 2nd this month"
    already: bool = False


@dataclass
class Outcome:
    """The tablet's answer to a scan."""

    kind: str  # "welcome", "choose", "choose_member", "stop", "old_code"
    welcome: Welcome | None = None
    message: str = ""
    employee: Employee | None = None
    member: Member | None = None
    services: list = field(default_factory=list)
    code: str = ""


def _first_name(full_name: str) -> str:
    return (full_name or "").split()[0] if full_name else ""


def _problem(tablet: EntranceTablet, kind: str, *, member=None, employee=None, detail=""):
    ScanProblem.objects.create(
        gym=tablet.gym,
        branch=tablet.branch,
        kind=kind,
        member=member,
        employee=employee,
        detail=detail[:200],
    )


STOP_WORDS = {
    attendance.Reason.EXPIRED: (
        ProblemKind.MEMBERSHIP_ENDED,
        "Your membership has ended. Please see reception to renew.",
    ),
    attendance.Reason.NO_MEMBERSHIP: (
        ProblemKind.NO_MEMBERSHIP,
        "You have no membership yet. Please see reception.",
    ),
    attendance.Reason.NOT_STARTED: (
        ProblemKind.NOT_STARTED,
        "Your membership has not started yet. Please see reception.",
    ),
    attendance.Reason.FROZEN: (
        ProblemKind.FROZEN,
        "Your membership is frozen. Please see reception.",
    ),
}


def member_welcome(member: Member, assessment, already=False) -> Welcome:
    today = timezone.localdate()
    days = attendance.visit_days(member, today - STREAK_LOOKBACK)
    membership = assessment.membership
    what = ""
    if membership is not None:
        what = f"{membership.package_name}"
        if assessment.days_left is not None:
            what += f" · {assessment.days_left} days left"
    return Welcome(
        first_name=_first_name(member.full_name),
        what=what,
        streak=streaks.compute(days, today),
        already=already,
    )


def employee_welcome(employee: Employee, service_names: list[str]) -> Welcome:
    today = timezone.localdate()
    days = corporate.visit_days(employee, today - STREAK_LOOKBACK)
    mine, rows = corporate.league_place(employee.company_id, today)
    line = ""
    if mine is not None and len(rows) > 1:
        line = f"{employee.company.name} is {_ordinal(mine.rank)} this month"
    return Welcome(
        first_name=_first_name(employee.full_name),
        what=f"{employee.company.name} · {' + '.join(service_names)}",
        streak=streaks.compute(days, today),
        company_line=line,
    )


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def scan(tablet: EntranceTablet, text: str) -> Outcome:
    """Someone held a card up to the tablet's camera."""
    text = (text or "").strip()
    if corporate.CARD_LINK.search(text):
        return _scan_employee(tablet, text)
    member = member_services.find_by_card_code(tablet.gym, text)
    if member is None:
        _problem(tablet, ProblemKind.UNKNOWN_CARD, detail="Scanned at the tablet")
        return Outcome(kind="stop", message="We don't know this card here. Please see reception.")
    assessment = attendance.assess(member)
    if not assessment.allowed:
        kind, words = STOP_WORDS.get(
            assessment.reason, (ProblemKind.NO_MEMBERSHIP, "Please see reception.")
        )
        _problem(tablet, kind, member=member)
        return Outcome(kind="stop", message=f"Sorry {_first_name(member.full_name)}. {words}")
    record, is_new = attendance.self_check_in(gym=tablet.gym, branch=tablet.branch, member=member)
    welcome = member_welcome(member, assessment, already=not is_new)
    if not is_new:
        return Outcome(kind="welcome", welcome=welcome)

    # What is their package good for today? One thing needs no asking; several
    # do, because the gym has to know what was actually used.
    choices = list(assessment.choices)
    if len(choices) == 1:
        attendance.record_self_activities(
            gym=tablet.gym, check_in=record, services_chosen=[choices[0].service]
        )
        return Outcome(kind="welcome", welcome=welcome)
    if len(choices) > 1:
        return Outcome(kind="choose_member", member=member, services=choices, welcome=welcome)
    return Outcome(kind="welcome", welcome=welcome)


def member_enter(tablet: EntranceTablet, member: Member, service_public_ids) -> Outcome:
    """A member ticked what they are doing on the tablet."""
    assessment = attendance.assess(member)
    if not assessment.allowed:
        return Outcome(kind="stop", message="Please see reception.")
    record = (
        CheckIn.objects.for_gym(tablet.gym)
        .filter(member=member, checked_in_on=timezone.localdate())
        .first()
    )
    if record is None:
        record, _made = attendance.self_check_in(
            gym=tablet.gym, branch=tablet.branch, member=member
        )
    wanted = {str(value) for value in service_public_ids}
    choices = list(assessment.choices)
    chosen = [item.service for item in choices if str(item.service.public_id) in wanted]
    if not chosen:
        return Outcome(
            kind="choose_member",
            member=member,
            services=choices,
            welcome=member_welcome(member, assessment, already=True),
            message="Choose at least one.",
        )
    attendance.record_self_activities(gym=tablet.gym, check_in=record, services_chosen=chosen)
    return Outcome(kind="welcome", welcome=member_welcome(member, assessment, already=False))


def _employee_allowed(tablet: EntranceTablet, employee: Employee) -> Outcome | None:
    try:
        corporate.check_employee(tablet.gym, employee, tablet.branch)
    except InvalidDetails as error:
        kind = ProblemKind.NOT_ON_LIST if error.reason == "removed" else ProblemKind.NO_CONTRACT
        _problem(tablet, kind, employee=employee)
        return Outcome(
            kind="stop",
            message=f"Sorry {_first_name(employee.full_name)}. Please see reception.",
        )
    return None


def _scan_employee(tablet: EntranceTablet, text: str) -> Outcome:
    match = corporate.CARD_LINK.search(text)
    employee = (
        Employee.objects.filter(card_token=match.group(1))
        .select_related("person", "company")
        .first()
    )
    if employee is None:
        _problem(tablet, ProblemKind.UNKNOWN_CARD, detail="Company card not recognised here")
        return Outcome(kind="stop", message="We don't know this card here. Please see reception.")
    stop = _employee_allowed(tablet, employee)
    if stop is not None:
        return stop
    code = match.group(2) or ""
    if not corporate.code_is_fresh(employee, code):
        return Outcome(
            kind="old_code",
            message="Please open your card on your phone and scan again, or use your PIN.",
        )
    return _choose_or_enter(tablet, employee, code=code)


def _choose_or_enter(tablet: EntranceTablet, employee: Employee, *, code="", pin="") -> Outcome:
    covered = corporate.check_employee(tablet.gym, employee, tablet.branch)
    already = (
        CorporateVisit.objects.for_gym(tablet.gym)
        .filter(employee=employee, checked_in_on=timezone.localdate())
        .exists()
    )
    if len(covered) == 1 or already:
        names = [covered[0]["service"].name] if covered and not already else []
        if already:
            return Outcome(
                kind="welcome",
                welcome=employee_welcome(employee, ["already in today"]),
            )
        return employee_enter(
            tablet, employee, [covered[0]["service"].public_id], code=code, pin=pin, names=names
        )
    return Outcome(kind="choose", employee=employee, services=covered, code=code)


def employee_enter(tablet, employee, service_public_ids, *, code="", pin="", names=None) -> Outcome:
    """The employee picked their services (or there was only one)."""
    covered = {
        c["service"].public_id: c["service"]
        for c in corporate.check_employee(tablet.gym, employee, tablet.branch)
    }
    chosen = [covered[p] for p in service_public_ids if p in covered]
    if not chosen:
        return Outcome(
            kind="choose",
            employee=employee,
            services=list({"service": s, "price": None} for s in covered.values()),
            code=code,
            message="Choose at least one.",
        )
    try:
        corporate.self_check_in_employee(
            gym=tablet.gym,
            branch=tablet.branch,
            employee=employee,
            service_ids=[s.id for s in chosen],
            phone_code_ok=corporate.code_is_fresh(employee, code),
            pin=pin,
        )
    except corporate.PinLocked:
        _problem(tablet, ProblemKind.PIN_LOCKED, employee=employee)
        return Outcome(kind="stop", message="Too many wrong PINs. Please see reception.")
    except corporate.NeedsProof:
        return Outcome(
            kind="old_code", message="Please open your card on your phone and scan again."
        )
    except InvalidDetails as error:
        if error.field == "pin":
            return Outcome(
                kind="stop", message="That PIN is not right. Try again or see reception."
            )
        return Outcome(kind="stop", message="Please see reception.")
    return Outcome(
        kind="welcome", welcome=employee_welcome(employee, names or [s.name for s in chosen])
    )


def employee_by_phone(tablet: EntranceTablet, phone: str) -> Employee | None:
    """For the PIN keypad: the company employee with this phone (visible to this gym)."""
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        return None
    return (
        Employee.objects.filter(person__phone=phone, is_active=True)
        .select_related("person", "company")
        .first()
    )


def pin_entry(tablet: EntranceTablet, phone: str, pin: str) -> Outcome:
    employee = employee_by_phone(tablet, phone)
    if employee is None:
        return Outcome(
            kind="stop", message="We couldn't find that phone number. Please see reception."
        )
    stop = _employee_allowed(tablet, employee)
    if stop is not None:
        return stop
    if not employee.has_pin:
        return Outcome(kind="stop", message="You have no PIN yet. Please see reception.")
    covered = corporate.check_employee(tablet.gym, employee, tablet.branch)
    if len(covered) == 1:
        return employee_enter(tablet, employee, [covered[0]["service"].public_id], pin=pin)
    # Check the PIN now, so a wrong one is refused before choosing services.
    try:
        if not corporate._check_pin(tablet.gym, employee, pin):
            return Outcome(
                kind="stop", message="That PIN is not right. Try again or see reception."
            )
    except corporate.PinLocked:
        _problem(tablet, ProblemKind.PIN_LOCKED, employee=employee)
        return Outcome(kind="stop", message="Too many wrong PINs. Please see reception.")
    return Outcome(kind="choose", employee=employee, services=covered, code="", message="")


# --- The desk -------------------------------------------------------------------------


def open_problems(gym: Gym, branch: Branch | None = None):
    since = timezone.now() - timedelta(hours=PROBLEM_HOURS)
    problems = (
        ScanProblem.objects.for_gym(gym)
        .filter(resolved_at__isnull=True, created_at__gte=since)
        .select_related("member", "employee__person", "employee__company", "branch")
    )
    return problems.filter(branch=branch) if branch else problems


@transaction.atomic
def resolve(*, by: StaffAssignment, problem: ScanProblem) -> None:
    require_role(by, CHECK_IN_ROLES)
    if problem.gym_id != by.gym_id:
        raise PermissionDenied("That belongs to a different gym.")
    if problem.resolved_at is None:
        problem.resolved_at, problem.resolved_by = timezone.now(), by.person
        problem.save(update_fields=["resolved_at", "resolved_by"])


@dataclass
class Arrival:
    at: object
    name: str
    detail: str
    via: str  # "Tablet", "Desk", "PIN"
    # Set for a company visit, so the desk can open it and fix a wrong service
    # on the same day. Member check-ins are not corrected this way.
    visit_id: object = None


VIA = {
    CheckInMethod.TABLET: "Tablet",
    CheckInMethod.QR: "Desk",
    CheckInMethod.MANUAL: "Desk",
    VisitMethod.TABLET: "Tablet",
    VisitMethod.PIN: "PIN",
    VisitMethod.PHONE: "Desk",
    VisitMethod.STAFF: "Desk",
}


def arrivals(gym: Gym, day: date, branch: Branch | None = None, limit: int = 12) -> list[Arrival]:
    """Members and company staff who came in today, newest first."""
    members = (
        CheckIn.objects.for_gym(gym)
        .filter(checked_in_on=day)
        .select_related("member", "membership")
        .prefetch_related("activities")
    )
    company = (
        CorporateVisit.objects.for_gym(gym)
        .filter(checked_in_on=day)
        .select_related("employee__person", "company")
        .prefetch_related("activities")
    )
    if branch is not None:
        members, company = members.filter(branch=branch), company.filter(branch=branch)
    rows = [
        Arrival(
            at=c.checked_in_at,
            name=c.member.full_name,
            detail=" · ".join(
                part
                for part in (
                    c.membership.package_name if c.membership_id else "",
                    " + ".join(
                        a.service_name for a in c.activities.all() if a.cancelled_at is None
                    ),
                )
                if part
            ),
            via=VIA.get(c.method, "Desk"),
        )
        for c in members.order_by("-checked_in_at")[:limit]
    ]
    rows += [
        Arrival(
            at=v.checked_in_at,
            name=v.employee.full_name,
            detail=f"{v.company.name} · "
            + " + ".join(a.service_name for a in v.activities.all() if a.cancelled_at is None),
            via=VIA.get(v.method, "Desk"),
            visit_id=v.public_id,
        )
        for v in company.order_by("-checked_in_at")[:limit]
    ]
    rows.sort(key=lambda r: r.at, reverse=True)
    return rows[:limit]


def counts_today(gym: Gym, day: date, branch: Branch | None = None) -> dict:
    members = CheckIn.objects.for_gym(gym).filter(checked_in_on=day)
    company = CorporateVisit.objects.for_gym(gym).filter(checked_in_on=day)
    if branch is not None:
        members, company = members.filter(branch=branch), company.filter(branch=branch)
    total = members.count() + company.count()
    by_tablet = (
        members.filter(method=CheckInMethod.TABLET).count()
        + company.filter(method=VisitMethod.TABLET).count()
    )
    return {"total": total, "tablet": by_tablet}


# --- Messages the desk sends ------------------------------------------------
#
# The gym sends from its own phone, through WhatsApp or the phone's SMS app.
# We only write the message and open the right app; nothing is sent by us and
# no message is stored. That keeps the gym's own number as the sender, which is
# what members recognise, and costs the gym nothing extra.

REMINDER_DAYS = 7  # a membership ending within a week
QUIET_AFTER_DAYS = 3  # not seen for three days
QUIET_BEFORE_DAYS = 7  # but seen within the last week


@dataclass(frozen=True)
class Reminder:
    """One member to reach out to, with the message already written."""

    member: Member
    phone: str
    reason: str  # "ending" or "quiet"
    detail: str  # what the desk sees under the name
    message: str

    @property
    def whatsapp_url(self) -> str:
        return f"https://wa.me/{self.phone.lstrip('+')}?text={quote(self.message)}"

    @property
    def sms_url(self) -> str:
        return f"sms:{self.phone}?body={quote(self.message)}"


def reminders(gym: Gym, today: date, limit: int = 6) -> list[Reminder]:
    """
    Who the desk should message now: memberships about to end first, then
    members who have gone quiet. The text is a starting point; staff can change
    it in WhatsApp before sending.
    """
    out: list[Reminder] = []
    for membership in member_services.ending_soon(gym, today, REMINDER_DAYS)[:limit]:
        member = membership.member
        days = (membership.end_date - today).days
        when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
        out.append(
            Reminder(
                member=member,
                phone=member.person.phone,
                reason="ending",
                detail=f"{membership.package_name} ends {when}",
                message=(
                    f"Muraho {_first_name(member.full_name)}, your {membership.package_name} at "
                    f"{gym.name} ends on {membership.end_date:%d/%m}. "
                    f"Renew at reception and keep your streak going."
                ),
            )
        )
    room = limit - len(out)
    if room > 0:
        quiet = attendance.quiet_members(gym, today, QUIET_AFTER_DAYS, QUIET_BEFORE_DAYS)
        for member in quiet[:room]:
            out.append(
                Reminder(
                    member=member,
                    phone=member.person.phone,
                    reason="quiet",
                    detail="not seen for a few days",
                    message=(
                        f"Muraho {_first_name(member.full_name)}, we have missed you at "
                        f"{gym.name}. Come back this week and keep your streak going."
                    ),
                )
            )
    return out


# --- The desk's list of everyone ---------------------------------------------

# Who the list shows. "individuals" are the gym's own members; "staff" are
# people whose company pays. A company's public_id narrows it to that company.
EVERYONE = "everyone"
INDIVIDUALS = "individuals"
COMPANY_STAFF = "staff"


@dataclass(frozen=True)
class DeskPerson:
    """
    One line of the desk's list of people.

    It is either a member of this gym, or a member of staff at a company that
    has a contract with it. The two are shown side by side, because at the
    desk they are both just somebody standing in front of you.
    """

    name: str
    phone: str
    member: Member | None = None
    membership: Membership | None = None
    is_active: bool = False
    days_left: int = 0
    employee: Employee | None = None
    company_name: str = ""
    covered_until: date | None = None

    @property
    def from_a_company(self) -> bool:
        return self.employee is not None


def _staff_of_companies(gym: Gym, query: str, group: str):
    """Company employees this gym may see, narrowed to one company if asked."""
    company = None
    if group not in (EVERYONE, COMPANY_STAFF):
        company = corporate.companies_with_contracts(gym).filter(public_id=group).first()
        if company is None:
            return Employee.objects.none()
    return corporate.search_employees(gym, query, company=company)


def people_page(
    gym: Gym,
    *,
    query: str = "",
    state: str = "",
    group: str = EVERYONE,
    page_number=None,
    per_page: int = 25,
):
    """
    One page of everyone the desk deals with: the gym's members and the staff
    of its companies, together, in name order.

    Narrowing by membership (active, ending soon, ended recently) leaves out
    company staff, because their company's contract covers them, not a
    membership of their own.
    """
    today = timezone.localdate()
    members = member_services.search_members(gym, query)
    if state in member_services.MEMBER_STATES:
        members = member_services.narrow_members(gym, members, state, today)

    show_members = group in (EVERYONE, INDIVIDUALS)
    show_staff = group != INDIVIDUALS and not state

    # Only the name and the key are read for everybody; the rest is read for
    # the twenty-five people actually shown.
    index: list[tuple[str, str, int]] = []
    if show_members:
        index += [(name, "member", pk) for pk, name in members.values_list("id", "full_name")]
    if show_staff:
        index += [
            (name, "staff", pk)
            for pk, name in _staff_of_companies(gym, query, group).values_list(
                "id", "person__full_name"
            )
        ]
    index.sort(key=lambda row: (row[0].lower(), row[1]))

    page = Paginator(index, per_page).get_page(page_number)
    return page, _fill_in(gym, list(page.object_list), today)


def _fill_in(gym: Gym, index: list[tuple[str, str, int]], today: date) -> list[DeskPerson]:
    """Read the full records for one page of the list."""
    member_ids = [pk for _, kind, pk in index if kind == "member"]
    staff_ids = [pk for _, kind, pk in index if kind == "staff"]

    members = {
        member.pk: member
        for member in Member.objects.for_gym(gym)
        .filter(pk__in=member_ids)
        .select_related("person", "branch")
    }
    statuses = {
        status.member.pk: status for status in member_services.status_for(members.values(), today)
    }
    employees = {
        employee.pk: employee
        for employee in Employee.objects.filter(pk__in=staff_ids).select_related(
            "person", "company"
        )
    }
    covered = corporate.contract_end_dates(gym, [e.company_id for e in employees.values()])

    rows = []
    for _name, kind, pk in index:
        if kind == "member" and pk in members:
            status = statuses[pk]
            rows.append(
                DeskPerson(
                    name=status.member.full_name,
                    phone=status.member.person.phone,
                    member=status.member,
                    membership=status.membership,
                    is_active=status.is_active,
                    days_left=status.days_left,
                )
            )
        elif kind == "staff" and pk in employees:
            employee = employees[pk]
            rows.append(
                DeskPerson(
                    name=employee.person.full_name,
                    phone=employee.person.phone,
                    employee=employee,
                    company_name=employee.company.name,
                    covered_until=covered.get(employee.company_id),
                )
            )
    return rows


# --- Reminders for what starts in a few minutes ------------------------------


@dataclass(frozen=True)
class Soon:
    """
    Something about to start, with the message already written.

    The system sends nothing by itself: the receptionist presses a button that
    opens WhatsApp or a text message on the gym's own phone, with the words
    ready. That is the honest version until a gym connects a message provider.
    """

    at: str
    what: str
    staff_name: str
    staff_phone: str
    client_name: str
    client_phone: str
    minutes_away: int

    def _link(self, phone: str, text: str, *, sms: bool) -> str:
        digits = "".join(ch for ch in phone if ch.isdigit())
        if sms:
            return f"sms:+{digits}?&body={quote(text)}"
        return f"https://wa.me/{digits}?text={quote(text)}"

    def _for_staff(self) -> str:
        return (
            f"Reminder: {self.what} with {self.client_name} at {self.at}, "
            f"in {self.minutes_away} minutes."
        )

    def _for_client(self) -> str:
        return (
            f"Reminder: your {self.what} with {self.staff_name} is at {self.at}, "
            f"in {self.minutes_away} minutes."
        )

    @property
    def whatsapp_staff(self) -> str:
        return self._link(self.staff_phone, self._for_staff(), sms=False)

    @property
    def sms_staff(self) -> str:
        return self._link(self.staff_phone, self._for_staff(), sms=True)

    @property
    def whatsapp_client(self) -> str:
        return self._link(self.client_phone, self._for_client(), sms=False)

    @property
    def sms_client(self) -> str:
        return self._link(self.client_phone, self._for_client(), sms=True)


def starting_soon(gym: Gym, *, within_minutes: int | None = None) -> list[Soon]:
    """Massages and one-to-one sessions about to start, soonest first."""
    now = timezone.localtime()
    rows = []
    for booking in massage.starting_soon(gym, within_minutes=within_minutes):
        away = (
            datetime.combine(booking.held_on, booking.starts_at) - now.replace(tzinfo=None)
        ).total_seconds() // 60
        rows.append(
            Soon(
                at=f"{booking.starts_at:%H:%M}",
                what=booking.type_name,
                staff_name=booking.masseuse.staff.person.full_name,
                staff_phone=booking.masseuse.staff.person.phone,
                client_name=booking.person.full_name,
                client_phone=booking.person.phone,
                minutes_away=max(int(away), 0),
            )
        )
    return rows
