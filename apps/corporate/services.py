"""
Business rules for corporate wellness.

Three kinds of people act here, and every function says which one it expects:

  * gym staff (`by: StaffAssignment`): contracts, the desk, bills, payments;
  * the company's logistics officer (`by: CompanyOfficer`): the employee
    list, approving contracts, questioning visits;
  * the employee, on their own card page (no sign-in; the card link is the key).

Money rules, as for members: amounts are whole francs, bills and payments are
permanent, and a mistake is corrected by adding a record (a credit, a
reversal), never by changing one.
"""

import calendar
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from apps.accounts import services as account_services
from apps.attendance.services import desk_branch
from apps.audit import services as audit
from apps.corporate.models import (
    BillLine,
    BillPayment,
    Company,
    CompanyLogEntry,
    CompanyOfficer,
    Contract,
    ContractPrice,
    ContractStatus,
    CorporateBill,
    CorporateSequence,
    CorporateVisit,
    Credit,
    Employee,
    Flag,
    FlagKind,
    FlagOutcome,
    PaymentReversal,
    PinAttempt,
    VisitActivity,
    VisitMethod,
)
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import CHECK_IN_ROLES, MANAGER_ROLES, OWNER_ONLY, require_role
from apps.members.models import Service
from apps.members.services import InvalidDetails
from apps.modules import services as module_services
from apps.modules.catalog import CORPORATE_WELLNESS
from apps.people import services as people_services
from apps.people.models import Person
from apps.people.phones import InvalidPhoneNumber, normalize_phone

MAX_PRICE_RWF = 5_000_000
MIN_PAYMENT_DAYS, MAX_PAYMENT_DAYS = 1, 90
TWO_PLACES_WINDOW = timedelta(hours=1)
PIN_LENGTH = 4
PIN_MAX_FAILURES = 5
PIN_LOCK = timedelta(minutes=15)

# The code on the employee's phone changes every CODE_STEP seconds, and the
# desk accepts the last few, so a screenshot stops working after ~2 minutes.
CODE_STEP = 30
CODE_STEPS_ACCEPTED = 4

CARD_LINK = re.compile(r"/e/([A-Za-z0-9_-]{32,64})/?(?:\?c=([0-9]{6}))?")


def _clean(value) -> str:
    return " ".join((value or "").split())


def corporate_enabled(gym: Gym) -> bool:
    return module_services.is_enabled(gym, CORPORATE_WELLNESS)


def _require_module(gym: Gym) -> None:
    if not corporate_enabled(gym):
        raise PermissionDenied("Corporate Wellness is not switched on for this gym.")


def _require_officer(by: CompanyOfficer) -> None:
    if by is None or not by.is_active or not by.company.is_active:
        raise PermissionDenied("You do not manage this company.")


def _same_company(by: CompanyOfficer, record) -> None:
    if record.company_id != by.company_id:
        raise PermissionDenied("That record belongs to a different company.")


def _same_gym(by: StaffAssignment, record) -> None:
    if record.gym_id != by.gym_id:
        raise PermissionDenied("That record belongs to a different gym.")


def log(company: Company, actor: Person | None, action: str, summary: str, **details) -> None:
    CompanyLogEntry.objects.create(
        company=company, actor=actor, action=action, summary=summary[:300], details=details
    )


# --- Companies (created by the platform) ------------------------------------


def _new_company_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread
    while True:
        code = "CO-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not Company.objects.filter(code=code).exists():
            return code


@dataclass(frozen=True)
class NewCompany:
    company: Company
    officer: CompanyOfficer
    temporary_password: str | None  # None when the officer already had a login


@transaction.atomic
def create_company(
    *, name: str, tin: str, officer_name: str, officer_phone: str, actor: Person | None
) -> NewCompany:
    """Platform staff register a company and its logistics officer (with a login)."""
    name = _clean(name)
    if not name:
        raise InvalidDetails("name", "required")
    try:
        phone = normalize_phone(officer_phone)
    except InvalidPhoneNumber as error:
        raise InvalidDetails("officer_phone", "invalid") from error
    company = Company.objects.create(name=name, tin=_clean(tin), code=_new_company_code())
    person = people_services.find_person_by_phone(phone)
    if person is None:
        if not _clean(officer_name):
            raise InvalidDetails("officer_name", "required")
        person = people_services.create_person(full_name=officer_name, phone=phone)
    officer = CompanyOfficer.objects.create(company=company, person=person)
    password = None
    if not hasattr(person, "user_account"):
        password = account_services.generate_temporary_password()
        account_services.create_user_account(person=person, password=password, temporary=True)
    log(
        company,
        actor,
        "company.created",
        f"{company.name} was registered with {person.full_name} as logistics officer",
    )
    return NewCompany(company=company, officer=officer, temporary_password=password)


def officer_roles_for(person: Person):
    """The companies a person manages."""
    return CompanyOfficer.objects.filter(
        person=person, is_active=True, company__is_active=True
    ).select_related("company")


def find_officer_role(*, person: Person, company_public_id) -> CompanyOfficer | None:
    return officer_roles_for(person).filter(company__public_id=company_public_id).first()


def company_for_code(code: str) -> tuple[int, str] | None:
    """The company with this contract code, as (id, name), or None."""
    code = _clean(code).upper()
    if not code:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, name FROM corporate_company_for_code(%s)", [code])
        row = cursor.fetchone()
    return (row[0], row[1]) if row else None


# --- Employees (managed by the company) -------------------------------------


def _new_card_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class EmployeeDetails:
    full_name: str
    phone: str
    employee_number: str
    department: str = ""


def _clean_employee(by: CompanyOfficer, details: EmployeeDetails, existing=None):
    number = _clean(details.employee_number)
    if not number:
        raise InvalidDetails("employee_number", "required")
    same_number = Employee.objects.filter(company=by.company, employee_number__iexact=number)
    if existing is not None:
        same_number = same_number.exclude(pk=existing.pk)
    if same_number.exists():
        raise InvalidDetails("employee_number", "duplicate")
    try:
        phone = normalize_phone(details.phone)
    except InvalidPhoneNumber as error:
        raise InvalidDetails("phone", "invalid") from error
    return number, phone, _clean(details.department)[:80]


@transaction.atomic
def add_employee(*, by: CompanyOfficer, details: EmployeeDetails) -> Employee:
    """Put someone on the company's list. A removed employee is brought back."""
    _require_officer(by)
    number, phone, department = _clean_employee(by, details)
    person = people_services.find_person_by_phone(phone)
    if person is None:
        if not _clean(details.full_name):
            raise InvalidDetails("full_name", "required")
        person = people_services.create_person(full_name=details.full_name, phone=phone)

    employee = Employee.objects.filter(company=by.company, person=person).first()
    if employee is not None:
        if employee.is_active:
            raise InvalidDetails("phone", "already_listed")
        employee.employee_number, employee.department = number, department
        employee.is_active, employee.removed_at = True, None
        employee.save()
        log(by.company, by.person, "employee.restored", f"{person.full_name} was put back")
        return employee

    employee = Employee.objects.create(
        company=by.company,
        person=person,
        employee_number=number,
        department=department,
        card_token=_new_card_token(),
    )
    log(
        by.company,
        by.person,
        "employee.added",
        f"{person.full_name} ({number}) was added",
        department=department,
    )
    return employee


@transaction.atomic
def update_employee(
    *, by: CompanyOfficer, employee: Employee, employee_number: str, department: str
) -> Employee:
    """Change an employee's number or department. Names and phones belong to the person."""
    _require_officer(by)
    _same_company(by, employee)
    details = EmployeeDetails(
        full_name=employee.full_name,
        phone=employee.person.phone,
        employee_number=employee_number,
        department=department,
    )
    number, _, department = _clean_employee(by, details, existing=employee)
    if (number, department) == (employee.employee_number, employee.department):
        return employee
    employee.employee_number, employee.department = number, department
    employee.save()
    log(by.company, by.person, "employee.updated", f"{employee.full_name}'s details were changed")
    return employee


@transaction.atomic
def remove_employee(*, by: CompanyOfficer, employee: Employee) -> Employee:
    """Take someone off the list. They cannot check in from this moment."""
    _require_officer(by)
    _same_company(by, employee)
    if not employee.is_active:
        return employee
    employee.is_active, employee.removed_at = False, timezone.now()
    employee.save()
    log(by.company, by.person, "employee.removed", f"{employee.full_name} was removed")
    return employee


@transaction.atomic
def set_pin(*, by: CompanyOfficer, employee: Employee, pin: str) -> None:
    """Give an employee a PIN (for those without a smartphone). Empty removes it."""
    _require_officer(by)
    _same_company(by, employee)
    pin = (pin or "").strip()
    if pin and not re.fullmatch(rf"\d{{{PIN_LENGTH}}}", pin):
        raise InvalidDetails("pin", "invalid")
    employee.pin_hash = make_password(pin) if pin else ""
    employee.save(update_fields=["pin_hash"])
    log(
        by.company,
        by.person,
        "employee.pin_set" if pin else "employee.pin_removed",
        f"{employee.full_name}'s PIN was {'set' if pin else 'removed'}",
    )


@transaction.atomic
def reset_card(*, by: CompanyOfficer, employee: Employee, new_link: bool = False) -> None:
    """
    Let the card be opened on a new phone (the employee changed phones).
    With `new_link`, the old link stops working altogether.
    """
    _require_officer(by)
    _same_company(by, employee)
    employee.device_hash = ""
    if new_link:
        employee.card_token = _new_card_token()
    employee.save(update_fields=["device_hash", "card_token"])
    log(by.company, by.person, "employee.card_reset", f"{employee.full_name}'s card was reset")


def employees_for(company: Company, query: str = "", active_only: bool = True):
    employees = Employee.objects.filter(company=company).select_related("person")
    if active_only:
        employees = employees.filter(is_active=True)
    query = _clean(query)
    if query:
        matches = (
            Q(person__full_name__icontains=query)
            | Q(employee_number__icontains=query)
            | Q(department__icontains=query)
        )
        digits = re.sub(r"\D", "", query)
        if len(digits) >= 3:
            matches |= Q(person__phone__contains=digits.lstrip("0") or digits)
        employees = employees.filter(matches)
    return employees


# --- Importing the company's Excel list -------------------------------------


@dataclass
class ImportRow:
    line: int
    details: EmployeeDetails
    problem: str = ""
    employee: Employee | None = None
    changes: list[str] = field(default_factory=list)


@dataclass
class ImportPlan:
    new: list[ImportRow] = field(default_factory=list)
    changed: list[ImportRow] = field(default_factory=list)
    unchanged: list[ImportRow] = field(default_factory=list)
    problems: list[ImportRow] = field(default_factory=list)
    missing: list[Employee] = field(default_factory=list)  # listed, but not in the file


HEADERS = {
    "full_name": ("name", "full name", "names", "employee name", "amazina"),
    "phone": ("phone", "tel", "telephone", "phone number", "tel number", "telefone"),
    "employee_number": ("employee number", "employee no", "staff number", "number", "id"),
    "department": ("department", "dept", "service", "ishami"),
}


def read_employee_rows(rows: list[list]) -> list[ImportRow]:
    """Turn spreadsheet rows (the first one being headings) into employee details."""
    if not rows:
        return []
    headings = [_clean(str(cell or "")).lower() for cell in rows[0]]
    columns = {}
    for key, names in HEADERS.items():
        for index, heading in enumerate(headings):
            if heading in names:
                columns[key] = index
                break
    missing = [key for key in ("full_name", "phone", "employee_number") if key not in columns]
    if missing:
        raise InvalidDetails("file", "headings")

    def cell(row, key):
        index = columns.get(key)
        if index is None or index >= len(row) or row[index] is None:
            return ""
        value = row[index]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return _clean(str(value))

    result = []
    for number, row in enumerate(rows[1:], start=2):
        if not any(_clean(str(c or "")) for c in row):
            continue
        result.append(
            ImportRow(
                line=number,
                details=EmployeeDetails(
                    full_name=cell(row, "full_name"),
                    phone=cell(row, "phone"),
                    employee_number=cell(row, "employee_number"),
                    department=cell(row, "department"),
                ),
            )
        )
    return result


def plan_import(by: CompanyOfficer, rows: list[ImportRow]) -> ImportPlan:
    """Compare the file with the current list. Nothing is saved."""
    _require_officer(by)
    plan = ImportPlan()
    current = {e.person.phone: e for e in employees_for(by.company, active_only=False)}
    seen_phones, seen_numbers = set(), set()
    for row in rows:
        try:
            phone = normalize_phone(row.details.phone)
        except InvalidPhoneNumber:
            row.problem = "phone"
            plan.problems.append(row)
            continue
        number = row.details.employee_number
        if not number:
            row.problem = "employee_number"
        elif phone in seen_phones or number.lower() in seen_numbers:
            row.problem = "repeated"
        elif not row.details.full_name and phone not in current:
            row.problem = "full_name"
        if row.problem:
            plan.problems.append(row)
            continue
        seen_phones.add(phone)
        seen_numbers.add(number.lower())
        employee = current.get(phone)
        if employee is None:
            plan.new.append(row)
            continue
        row.employee = employee
        if not employee.is_active:
            row.changes.append("restored")
        if employee.employee_number != number:
            row.changes.append("employee number")
        if employee.department != row.details.department[:80]:
            row.changes.append("department")
        (plan.changed if row.changes else plan.unchanged).append(row)

    listed = {r.employee.pk for r in plan.changed + plan.unchanged}
    plan.missing = [e for e in current.values() if e.is_active and e.pk not in listed]
    return plan


@dataclass(frozen=True)
class ImportResult:
    added: int
    updated: int
    removed: int
    failed: list[ImportRow]


@transaction.atomic
def apply_import(*, by: CompanyOfficer, plan: ImportPlan, remove_missing: bool) -> ImportResult:
    """Save an import plan. Rows that fail (for example a clash) are reported, not saved."""
    _require_officer(by)
    added = updated = removed = 0
    failed = []
    for row in plan.new:
        try:
            with transaction.atomic():
                add_employee(by=by, details=row.details)
            added += 1
        except InvalidDetails as error:
            row.problem = error.field
            failed.append(row)
    for row in plan.changed:
        try:
            with transaction.atomic():
                employee = row.employee
                if not employee.is_active:
                    employee.is_active, employee.removed_at = True, None
                    employee.save()
                update_employee(
                    by=by,
                    employee=employee,
                    employee_number=row.details.employee_number,
                    department=row.details.department,
                )
            updated += 1
        except InvalidDetails as error:
            row.problem = error.field
            failed.append(row)
    if remove_missing:
        for employee in plan.missing:
            remove_employee(by=by, employee=employee)
            removed += 1
    log(
        by.company,
        by.person,
        "employees.imported",
        f"Employee list imported: {added} added, {updated} updated, {removed} removed",
    )
    return ImportResult(added=added, updated=updated, removed=removed, failed=failed)


# --- Contracts ---------------------------------------------------------------


@dataclass(frozen=True)
class ContractTerms:
    start_date: date
    end_date: date
    payment_days: int
    # {(service_id, branch_id): price_rwf}; a missing pair is not covered.
    prices: dict


def _check_terms(by: StaffAssignment, terms: ContractTerms) -> None:
    if terms.end_date < terms.start_date:
        raise InvalidDetails("end_date", "before_start")
    if not MIN_PAYMENT_DAYS <= terms.payment_days <= MAX_PAYMENT_DAYS:
        raise InvalidDetails("payment_days", "out_of_range")
    if not terms.prices:
        raise InvalidDetails("prices", "required")
    services = set(Service.objects.for_gym(by.gym).values_list("id", flat=True))
    branches = set(Branch.objects.for_gym(by.gym).values_list("id", flat=True))
    for (service_id, branch_id), price in terms.prices.items():
        if service_id not in services or branch_id not in branches:
            raise PermissionDenied("That service or branch belongs to a different gym.")
        if not isinstance(price, int) or not 0 < price <= MAX_PRICE_RWF:
            raise InvalidDetails("prices", "out_of_range")


def _save_prices(contract: Contract, prices: dict) -> None:
    ContractPrice.objects.filter(contract=contract).delete()
    ContractPrice.objects.bulk_create(
        ContractPrice(
            gym=contract.gym,
            contract=contract,
            company=contract.company,
            service_id=service_id,
            branch_id=branch_id,
            price_rwf=price,
        )
        for (service_id, branch_id), price in prices.items()
    )


@transaction.atomic
def propose_contract(*, by: StaffAssignment, company_code: str, terms: ContractTerms) -> Contract:
    """The gym proposes a contract to a company. It starts once the company approves it."""
    require_role(by, MANAGER_ROLES)
    _require_module(by.gym)
    found = company_for_code(company_code)
    if found is None:
        raise InvalidDetails("company_code", "not_found")
    _check_terms(by, terms)
    contract = Contract.objects.create(
        gym=by.gym,
        company_id=found[0],
        start_date=terms.start_date,
        end_date=terms.end_date,
        payment_days=terms.payment_days,
        proposed_by=by.person,
    )
    _save_prices(contract, terms.prices)
    audit.record(
        action="corporate.contract_proposed",
        summary=f"Contract proposed to {found[1]} from {terms.start_date:%d %b %Y}",
        gym=by.gym,
        actor=by.person,
        target=contract,
        details={"payment_days": terms.payment_days, "prices": len(terms.prices)},
    )
    return contract


@transaction.atomic
def change_proposal(*, by: StaffAssignment, contract: Contract, terms: ContractTerms) -> Contract:
    """Change a contract the company has not decided on yet (or has declined)."""
    require_role(by, MANAGER_ROLES)
    _same_gym(by, contract)
    if contract.status not in (ContractStatus.PROPOSED, ContractStatus.DECLINED):
        raise InvalidDetails("status", "already_approved")
    _check_terms(by, terms)
    contract.start_date, contract.end_date = terms.start_date, terms.end_date
    contract.payment_days = terms.payment_days
    contract.status = ContractStatus.PROPOSED
    contract.decided_by, contract.decided_at = None, None
    contract.save()
    _save_prices(contract, terms.prices)
    audit.record(
        action="corporate.contract_changed",
        summary=f"Contract with {contract.company.name} was changed and proposed again",
        gym=by.gym,
        actor=by.person,
        target=contract,
    )
    return contract


@transaction.atomic
def decide_contract(*, by: CompanyOfficer, contract: Contract, approve: bool) -> Contract:
    """The company approves or declines a proposed contract. Approved terms are fixed."""
    _require_officer(by)
    _same_company(by, contract)
    if contract.status != ContractStatus.PROPOSED:
        raise InvalidDetails("status", "not_proposed")
    contract.status = ContractStatus.APPROVED if approve else ContractStatus.DECLINED
    contract.decided_by, contract.decided_at = by.person, timezone.now()
    contract.save()
    log(
        by.company,
        by.person,
        "contract.approved" if approve else "contract.declined",
        f"Contract with {contract.gym.name} was {'approved' if approve else 'declined'}",
    )
    return contract


@transaction.atomic
def end_contract(*, by, contract: Contract, on: date) -> Contract:
    """
    End an approved contract early, from either side. Visits up to `on` are
    still billed. `by` is the gym's owner or the company's officer.
    """
    if isinstance(by, CompanyOfficer):
        _require_officer(by)
        _same_company(by, contract)
    else:
        require_role(by, OWNER_ONLY)
        _same_gym(by, contract)
    if contract.status != ContractStatus.APPROVED or contract.ended_on is not None:
        raise InvalidDetails("status", "not_approved")
    if not contract.start_date - timedelta(days=1) <= on <= contract.end_date:
        raise InvalidDetails("ended_on", "out_of_range")
    if on < timezone.localdate() - timedelta(days=1):
        raise InvalidDetails("ended_on", "in_the_past")
    contract.ended_on = on
    contract.save()
    summary = f"Contract between {contract.company.name} and {contract.gym.name} ends {on:%d %b %Y}"
    if isinstance(by, CompanyOfficer):
        log(by.company, by.person, "contract.ended", summary)
    else:
        audit.record(
            action="corporate.contract_ended",
            summary=summary,
            gym=by.gym,
            actor=by.person,
            target=contract,
        )
    return contract


def prices_for(contract: Contract) -> dict:
    """{(service_id, branch_id): price_rwf} for a contract."""
    return {
        (p.service_id, p.branch_id): p.price_rwf
        for p in ContractPrice.objects.filter(contract=contract)
    }


def _last_day(contract: Contract) -> date:
    return contract.ended_on or contract.end_date


def running_contract(gym: Gym, company_id: int, on: date) -> Contract | None:
    contracts = Contract.objects.for_gym(gym).filter(
        company_id=company_id, status=ContractStatus.APPROVED, start_date__lte=on
    )
    return contracts.filter(
        Q(ended_on__isnull=True, end_date__gte=on) | Q(ended_on__gte=on)
    ).first()


# --- The employee's card ------------------------------------------------------


def card_code(employee: Employee, at: float | None = None) -> str:
    """The 6-digit code shown with the card's QR; it changes every 30 seconds."""
    step = int((at or time.time()) // CODE_STEP)
    digest = hmac.new(
        settings.SECRET_KEY.encode(), f"{employee.card_token}:{step}".encode(), hashlib.sha256
    ).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def code_is_fresh(employee: Employee, code: str | None) -> bool:
    if not code:
        return False
    now = time.time()
    return any(
        hmac.compare_digest(card_code(employee, now - i * CODE_STEP), code)
        for i in range(CODE_STEPS_ACCEPTED)
    )


def company_id_for_card(token: str) -> int | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", token or ""):
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT corporate_company_for_card(%s)", [token])
        return cursor.fetchone()[0]


def _device_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class CardPhone:
    NEW = "new"  # first opening: this phone becomes the card's phone
    THIS = "this"
    OTHER = "other"  # set up on another phone


def card_phone_status(employee: Employee, device_secret: str | None) -> str:
    if not employee.device_hash:
        return CardPhone.NEW
    if device_secret and hmac.compare_digest(employee.device_hash, _device_hash(device_secret)):
        return CardPhone.THIS
    return CardPhone.OTHER


def claim_card(employee: Employee) -> str:
    """Make the phone opening the card for the first time its phone. Returns its secret."""
    secret = secrets.token_urlsafe(32)
    updated = Employee.objects.filter(pk=employee.pk, device_hash="").update(
        device_hash=_device_hash(secret)
    )
    if not updated:
        raise PermissionDenied("This card is already set up on another phone.")
    return secret


def visits_to_confirm(employee: Employee):
    return (
        CorporateVisit.objects.filter(employee=employee, confirmed_at__isnull=True)
        .filter(checked_in_on__gte=timezone.localdate() - timedelta(days=2))
        .select_related("gym", "branch")
        .prefetch_related("activities")
    )


def recent_visits(employee: Employee, limit: int = 10):
    return (
        CorporateVisit.objects.filter(employee=employee)
        .select_related("gym", "branch")
        .prefetch_related("activities")[:limit]
    )


@transaction.atomic
def confirm_visit(*, employee: Employee, visit: CorporateVisit) -> None:
    """The employee confirms, on their own phone, that they made this visit."""
    if visit.employee_id != employee.pk:
        raise PermissionDenied("That visit is someone else's.")
    if visit.confirmed_at is None:
        visit.confirmed_at = timezone.now()
        visit.save(update_fields=["confirmed_at"])


# --- The desk -----------------------------------------------------------------


@dataclass
class DeskLookup:
    employee: Employee
    contract: Contract | None
    phone_code_ok: bool = False
    visit_today: CorporateVisit | None = None

    @property
    def allowed(self) -> bool:
        return self.employee.is_active and self.contract is not None


def _desk_lookup(gym: Gym, employee: Employee, code_ok: bool, today: date) -> DeskLookup:
    visit = (
        CorporateVisit.objects.for_gym(gym)
        .filter(employee=employee, checked_in_on=today)
        .prefetch_related("activities")
        .first()
    )
    return DeskLookup(
        employee=employee,
        contract=running_contract(gym, employee.company_id, today),
        phone_code_ok=code_ok,
        visit_today=visit,
    )


def find_for_desk(gym: Gym, text: str) -> list[DeskLookup]:
    """
    Employees for the desk: from a scanned card link, or by name, employee
    number or phone. Only companies with an approved contract are searched.
    """
    text = (text or "").strip()
    today = timezone.localdate()
    match = CARD_LINK.search(text)
    if match:
        employee = (
            Employee.objects.filter(card_token=match.group(1))
            .select_related("person", "company")
            .first()
        )
        if employee is None:
            return []
        code_ok = code_is_fresh(employee, match.group(2))
        return [_desk_lookup(gym, employee, code_ok, today)]
    query = _clean(text)
    if len(query) < 2:
        return []
    matches = Q(person__full_name__icontains=query) | Q(employee_number__iexact=query)
    digits = re.sub(r"\D", "", query)
    if len(digits) >= 3:
        matches |= Q(person__phone__contains=digits.lstrip("0") or digits)
    employees = Employee.objects.filter(matches).select_related("person", "company")[:8]
    return [_desk_lookup(gym, employee, False, today) for employee in employees]


class NeedsProof(Exception):
    """Neither a fresh code from the employee's phone nor a correct PIN was given."""


class PinLocked(Exception):
    """Too many wrong PINs at this gym; try again later."""


def _check_pin(gym: Gym, employee: Employee, pin: str) -> bool:
    since = timezone.now() - PIN_LOCK
    recent = PinAttempt.objects.for_gym(gym).filter(employee=employee, at__gte=since)
    last_success = recent.filter(succeeded=True).order_by("-at").values("at").first()
    failures = recent.filter(succeeded=False)
    if last_success:
        failures = failures.filter(at__gt=last_success["at"])
    if failures.count() >= PIN_MAX_FAILURES:
        raise PinLocked()
    ok = bool(employee.pin_hash) and check_password(pin, employee.pin_hash)
    PinAttempt.objects.create(gym=gym, employee=employee, succeeded=ok)
    return ok


@dataclass(frozen=True)
class DeskResult:
    visit: CorporateVisit
    added: list[VisitActivity]
    warned_two_places: bool


def check_in_employee(
    *,
    by: StaffAssignment,
    employee: Employee,
    service_ids: list[int],
    branch: Branch | None = None,
    phone_code_ok: bool = False,
    pin: str = "",
) -> DeskResult:
    """
    Reception records a company employee's visit and the services they use.
    Calling it again the same day adds services to the same visit.

    The first check-in of the day needs proof it is really the employee: the
    fresh code from the card on their phone, or their PIN. That proof is the
    employee's signature: the visit is confirmed there and then.
    """
    require_role(by, CHECK_IN_ROLES)
    _require_module(by.gym)
    pin_ok = False
    if pin:
        # Checked (and the attempt saved) on its own, so a wrong PIN still counts.
        pin_ok = _check_pin(by.gym, employee, pin)
        if not pin_ok:
            raise InvalidDetails("pin", "wrong")
    visit = (
        CorporateVisit.objects.for_gym(by.gym)
        .filter(employee=employee, checked_in_on=timezone.localdate())
        .first()
    )
    return _record_visit(
        gym=by.gym,
        branch=visit.branch if visit is not None else desk_branch(by, branch),
        employee=employee,
        service_ids=service_ids,
        person=by.person,
        phone_code_ok=phone_code_ok,
        pin_ok=pin_ok,
        method=None,
    )


def self_check_in_employee(
    *,
    gym: Gym,
    branch: Branch,
    employee: Employee,
    service_ids: list[int],
    phone_code_ok: bool = False,
    pin: str = "",
) -> DeskResult:
    """
    A company employee checks themselves in at the entrance tablet of `branch`.
    The caller has already checked the request comes from a connected tablet.
    """
    _require_module(gym)
    if branch.gym_id != gym.id:
        raise PermissionDenied("That branch belongs to a different gym.")
    pin_ok = False
    if pin:
        pin_ok = _check_pin(gym, employee, pin)
        if not pin_ok:
            raise InvalidDetails("pin", "wrong")
    visit = (
        CorporateVisit.objects.for_gym(gym)
        .filter(employee=employee, checked_in_on=timezone.localdate())
        .first()
    )
    return _record_visit(
        gym=gym,
        branch=visit.branch if visit is not None else branch,
        employee=employee,
        service_ids=service_ids,
        person=None,
        phone_code_ok=phone_code_ok,
        pin_ok=pin_ok,
        method=VisitMethod.TABLET,
    )


def check_employee(gym: Gym, employee: Employee, branch: Branch) -> list[dict]:
    """
    Before a check-in: the services this employee's contract covers at this
    branch today, with prices. Raises InvalidDetails when they cannot come in.
    """
    if not employee.is_active:
        raise InvalidDetails("employee", "removed")
    contract = running_contract(gym, employee.company_id, timezone.localdate())
    if contract is None:
        raise InvalidDetails("employee", "no_contract")
    prices = prices_for(contract)
    covered = [
        {"service": service, "price": prices[(service.id, branch.id)]}
        for service in Service.objects.for_gym(gym).filter(is_active=True)
        if (service.id, branch.id) in prices
    ]
    # The usual facilities first (the gym itself on top), then the rest A-Z.
    usual = ["gym", "sauna", "massage", "swimming", "aerobics"]
    covered.sort(
        key=lambda c: (
            usual.index(c["service"].name.lower()) if c["service"].name.lower() in usual else 99,
            c["service"].name.lower(),
        )
    )
    return covered


@transaction.atomic
def _record_visit(
    *,
    gym: Gym,
    branch: Branch,
    employee: Employee,
    service_ids: list[int],
    person: Person | None,
    phone_code_ok: bool,
    pin_ok: bool,
    method: str | None,
) -> DeskResult:
    if not employee.is_active:
        raise InvalidDetails("employee", "removed")
    today = timezone.localdate()
    contract = running_contract(gym, employee.company_id, today)
    if contract is None:
        raise InvalidDetails("employee", "no_contract")
    visit = (
        CorporateVisit.objects.for_gym(gym).filter(employee=employee, checked_in_on=today).first()
    )
    if not service_ids:
        raise InvalidDetails("services", "required")
    prices = prices_for(visit.contract if visit is not None else contract)
    services = {s.id: s for s in Service.objects.for_gym(gym).filter(id__in=service_ids)}
    for service_id in service_ids:
        if service_id not in services or (service_id, branch.id) not in prices:
            raise InvalidDetails("services", "not_covered")
    if visit is None and not (phone_code_ok or pin_ok):
        raise NeedsProof()

    warned = False
    if visit is None:
        visit = CorporateVisit.objects.create(
            gym=gym,
            company_id=employee.company_id,
            contract=contract,
            employee=employee,
            branch=branch,
            checked_in_on=today,
            method=method or (VisitMethod.PIN if pin_ok else VisitMethod.PHONE),
            admitted_by=person,
            # The scan of the phone card (or the PIN) is the signature.
            confirmed_at=timezone.now(),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT corporate_visited_elsewhere(%s, %s, %s, %s)",
                [employee.pk, gym.id, branch.pk, timezone.now() - TWO_PLACES_WINDOW],
            )
            if cursor.fetchone()[0]:
                Flag.objects.create(
                    gym=gym,
                    visit=visit,
                    company_id=employee.company_id,
                    kind=FlagKind.TWO_PLACES,
                    message="Also checked in at another gym or branch within the last hour.",
                )
                warned = True

    existing = set(
        visit.activities.filter(cancelled_at__isnull=True).values_list("service_id", flat=True)
    )
    added = []
    for service_id in service_ids:
        if service_id in existing:
            continue
        previous = visit.activities.filter(service_id=service_id).first()
        service = services[service_id]
        if previous is not None:  # it was cancelled earlier today; bring it back
            previous.cancelled_at, previous.cancelled_by, previous.cancel_reason = None, None, ""
            previous.price_rwf = prices[(service_id, visit.branch_id)]
            previous.added_at, previous.added_by = timezone.now(), person
            previous.save()
            added.append(previous)
            continue
        added.append(
            VisitActivity.objects.create(
                gym=gym,
                visit=visit,
                company_id=employee.company_id,
                service=service,
                service_name=service.name,
                price_rwf=prices[(service_id, visit.branch_id)],
                added_by=person,
            )
        )
    return DeskResult(visit=visit, added=added, warned_two_places=warned)


def visit_days(employee: Employee, since: date) -> list[date]:
    """The days an employee came in (at any gym) since a day: their streak."""
    return list(
        CorporateVisit.objects.filter(employee=employee, checked_in_on__gte=since).values_list(
            "checked_in_on", flat=True
        )
    )


# --- The company league -----------------------------------------------------------

LEAGUE_VISITS = 4


@dataclass(frozen=True)
class LeagueRow:
    rank: int
    company_id: int
    name: str
    listed: int
    active: int

    @property
    def percent(self) -> int:
        return round(self.active * 100 / self.listed) if self.listed else 0


def league(month: date) -> list[LeagueRow]:
    """
    This month's league of companies taking part: the share of each company's
    listed employees who came at least 4 times (at any gym). Only names and
    percentages leave the database, never people or money.
    """
    start, end = month_bounds(month)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT company_id, name, listed, active FROM corporate_league(%s, %s, %s)",
            [start, end, LEAGUE_VISITS],
        )
        rows = cursor.fetchall()
    rows = [r for r in rows if r[2] > 0]
    rows.sort(key=lambda r: (-(r[3] / r[2]), r[1]))
    return [
        LeagueRow(rank=i + 1, company_id=r[0], name=r[1], listed=r[2], active=r[3])
        for i, r in enumerate(rows)
    ]


def league_place(company_id: int, month: date) -> tuple[LeagueRow | None, list[LeagueRow]]:
    rows = league(month)
    mine = next((r for r in rows if r.company_id == company_id), None)
    return mine, rows


def department_league(company: Company, month: date) -> list[dict]:
    """Inside one company: the share of each department who came 4+ times."""
    start, end = month_bounds(month)
    employees = Employee.objects.filter(company=company, is_active=True)
    counts = dict(
        CorporateVisit.objects.filter(company=company, checked_in_on__range=(start, end))
        .values("employee_id")
        .annotate(n=Count("id"))
        .values_list("employee_id", "n")
    )
    groups: dict[str, list[int]] = {}
    for employee in employees:
        name = employee.department or "No department"
        groups.setdefault(name, []).append(counts.get(employee.pk, 0))
    result = [
        {
            "name": name,
            "listed": len(visits),
            "active": sum(1 for v in visits if v >= LEAGUE_VISITS),
        }
        for name, visits in groups.items()
    ]
    for row in result:
        row["percent"] = round(row["active"] * 100 / row["listed"]) if row["listed"] else 0
    return sorted(result, key=lambda r: (-r["percent"], r["name"]))


@transaction.atomic
def cancel_activity(*, by: StaffAssignment, activity: VisitActivity, reason: str) -> None:
    """Take back a service recorded by mistake, before it is billed."""
    require_role(by, CHECK_IN_ROLES)
    _same_gym(by, activity)
    reason = _clean(reason)
    if not reason:
        raise InvalidDetails("reason", "required")
    if hasattr(activity, "bill_line"):
        raise InvalidDetails("activity", "billed")
    # Only on the day. A visit that was not corrected the same day is taken as
    # right, and nobody can change it afterwards: that is what stops a quiet
    # change to a month the company has already been shown.
    if activity.visit.checked_in_on != timezone.localdate():
        raise InvalidDetails("activity", "too_late")
    if activity.cancelled_at is None:
        activity.cancelled_at, activity.cancelled_by = timezone.now(), by.person
        activity.cancel_reason = reason[:200]
        activity.save()
        audit.record(
            action="corporate.activity_cancelled",
            summary=f"{activity.service_name} for {activity.visit.employee.full_name} cancelled",
            gym=by.gym,
            actor=by.person,
            target=activity,
            details={"reason": reason},
        )


def companies_with_contracts(gym: Gym):
    """Every company this gym has ever had a contract with, by name."""
    ids = Contract.objects.for_gym(gym).values("company_id")
    return Company.objects.filter(id__in=ids).order_by("name")


def visits_for_gym(gym: Gym, start: date, end: date, company_id=None):
    visits = (
        CorporateVisit.objects.for_gym(gym)
        .filter(checked_in_on__range=(start, end))
        .select_related("employee__person", "company", "branch")
        .prefetch_related("activities", "flags")
    )
    return visits.filter(company_id=company_id) if company_id else visits


def visits_for_company(company: Company, start: date, end: date, gym_id=None):
    visits = (
        CorporateVisit.objects.filter(company=company, checked_in_on__range=(start, end))
        .select_related("employee__person", "gym", "branch")
        .prefetch_related("activities", "flags")
    )
    return visits.filter(gym_id=gym_id) if gym_id else visits


# --- Warnings -----------------------------------------------------------------


@transaction.atomic
def question_visit(*, by: CompanyOfficer, visit: CorporateVisit, message: str) -> Flag:
    """The company questions a visit. It stays off the bill until settled."""
    _require_officer(by)
    _same_company(by, visit)
    message = _clean(message)
    if not message:
        raise InvalidDetails("message", "required")
    flag = Flag.objects.create(
        gym=visit.gym,
        visit=visit,
        company=by.company,
        kind=FlagKind.DISPUTED,
        message=message[:300],
        raised_by=by.person,
    )
    log(
        by.company,
        by.person,
        "visit.questioned",
        f"Visit of {visit.employee.full_name} on {visit.checked_in_on:%d %b} at "
        f"{visit.gym.name} was questioned",
    )
    return flag


@transaction.atomic
def settle_flag(*, by, flag: Flag, note: str = "") -> Flag:
    """
    Settle a warning.
      * The company settles it by accepting the visit: it goes on the bill.
      * The gym settles it by taking the visit off: its services are cancelled.
    """
    if flag.settled_at is not None:
        return flag
    note = _clean(note)[:300]
    if isinstance(by, CompanyOfficer):
        _require_officer(by)
        _same_company(by, flag)
        flag.outcome = FlagOutcome.KEPT
        log(by.company, by.person, "warning.accepted", f"Warning on a visit to {flag.gym} accepted")
    else:
        require_role(by, MANAGER_ROLES)
        _same_gym(by, flag)
        if not note:
            raise InvalidDetails("note", "required")
        for activity in flag.visit.activities.filter(cancelled_at__isnull=True):
            if hasattr(activity, "bill_line"):
                continue
            activity.cancelled_at, activity.cancelled_by = timezone.now(), by.person
            activity.cancel_reason = f"Warning settled: {note}"[:200]
            activity.save()
        flag.outcome = FlagOutcome.REMOVED
        audit.record(
            action="corporate.warning_settled",
            summary=f"Visit of {flag.visit.employee.full_name} taken off the bill",
            gym=by.gym,
            actor=by.person,
            target=flag,
            details={"note": note},
        )
    flag.settled_note, flag.settled_by, flag.settled_at = note, by.person, timezone.now()
    flag.save()
    return flag


def open_flags_for_gym(gym: Gym):
    return (
        Flag.objects.for_gym(gym)
        .filter(settled_at__isnull=True)
        .select_related("visit__employee__person", "company", "visit__branch")
    )


def open_flags_for_company(company: Company):
    return Flag.objects.filter(company=company, settled_at__isnull=True).select_related(
        "visit__employee__person", "gym", "visit__branch"
    )


# --- Monthly bills --------------------------------------------------------------


def month_bounds(day: date) -> tuple[date, date]:
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _next_bill_number(gym: Gym) -> int:
    sequence, _ = CorporateSequence.objects.select_for_update().get_or_create(gym=gym)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return sequence.last_number


def billable_activities(contract: Contract, until: date):
    """Services used under a contract up to a day that are not yet billed or held back."""
    open_flag = Flag.objects.filter(visit=OuterRef("visit"), settled_at__isnull=True)
    return (
        VisitActivity.objects.filter(
            gym=contract.gym,
            visit__contract=contract,
            visit__checked_in_on__lte=until,
            cancelled_at__isnull=True,
            bill_line__isnull=True,
        )
        .filter(~Exists(open_flag))
        .select_related("visit__employee__person", "visit__branch")
    )


@transaction.atomic
def issue_bill(*, contract: Contract, month: date, by: StaffAssignment | None = None):
    """
    The bill for one month: every service used up to the month's last day that
    is not billed yet and has no open warning (earlier ones held back included),
    minus credits. Returns None when there is nothing to bill or it exists.
    """
    if by is not None:
        require_role(by, MANAGER_ROLES)
        _same_gym(by, contract)
    start, end = month_bounds(month)
    if end > timezone.localdate():
        raise InvalidDetails("month", "not_over")
    CorporateSequence.objects.select_for_update().get_or_create(gym=contract.gym)
    if CorporateBill.objects.filter(contract=contract, period_start=start).exists():
        return None
    activities = list(billable_activities(contract, end))
    credits = list(
        Credit.objects.filter(contract=contract, bill_line__isnull=True).select_related(
            "original_line"
        )
    )
    if not activities and not credits:
        return None

    total = sum(a.price_rwf for a in activities) - sum(c.original_line.amount_rwf for c in credits)
    issued = timezone.localdate()
    bill = CorporateBill.objects.create(
        gym=contract.gym,
        number=_next_bill_number(contract.gym),
        company=contract.company,
        contract=contract,
        period_start=start,
        period_end=end,
        issued_on=issued,
        due_on=issued + timedelta(days=contract.payment_days),
        total_rwf=total,
        issued_by=by.person if by else None,
    )
    lines = []
    for activity in activities:
        visit = activity.visit
        lines.append(
            BillLine(
                gym=contract.gym,
                bill=bill,
                company=contract.company,
                activity=activity,
                employee_name=visit.employee.full_name,
                employee_number=visit.employee.employee_number,
                department=visit.employee.department,
                phone=visit.employee.person.phone,
                branch_name=visit.branch.name,
                service_name=activity.service_name,
                visit_date=visit.checked_in_on,
                check_in_time=timezone.localtime(visit.checked_in_at).time().replace(microsecond=0),
                confirmed=visit.confirmed_at is not None,
                amount_rwf=activity.price_rwf,
            )
        )
    for credit in credits:
        original = credit.original_line
        lines.append(
            BillLine(
                gym=contract.gym,
                bill=bill,
                company=contract.company,
                credit=credit,
                employee_name=original.employee_name,
                employee_number=original.employee_number,
                department=original.department,
                phone=original.phone,
                branch_name=original.branch_name,
                service_name=f"Credit: {original.service_name}",
                visit_date=original.visit_date,
                check_in_time=original.check_in_time,
                confirmed=original.confirmed,
                amount_rwf=-original.amount_rwf,
            )
        )
    BillLine.objects.bulk_create(lines)
    audit.record(
        action="corporate.bill_issued",
        summary=f"{bill.display_number} for RWF {total:,} issued to {contract.company.name}",
        gym=contract.gym,
        actor=by.person if by else None,
        target=bill,
        details={"period": f"{start:%Y-%m}", "lines": len(lines)},
    )
    return bill


def months_to_bill(contract: Contract, today: date) -> list[date]:
    """First days of the months of a contract whose last day has come."""
    months = []
    month = contract.start_date.replace(day=1)
    last = min(_last_day(contract), today)
    while month <= last:
        if month_bounds(month)[1] <= today:
            months.append(month)
        month = (month + timedelta(days=32)).replace(day=1)
    return months


def issue_due_bills(today: date | None = None) -> list[CorporateBill]:
    """Issue every bill that is due. Run each evening (the bill comes on the last day)."""
    today = today or timezone.localdate()
    issued = []
    contracts = Contract.objects.filter(status=ContractStatus.APPROVED).select_related(
        "company", "gym"
    )
    for contract in contracts:
        for month in months_to_bill(contract, today):
            bill = issue_bill(contract=contract, month=month)
            if bill is not None:
                issued.append(bill)
    return issued


@dataclass
class BillState:
    bill: CorporateBill
    paid_rwf: int
    owed_rwf: int
    overdue: bool

    @property
    def status(self) -> str:
        if self.owed_rwf <= 0:
            return "paid"
        if self.overdue:
            return "overdue"
        return "part_paid" if self.paid_rwf else "unpaid"


def bill_state(bill: CorporateBill, today: date | None = None) -> BillState:
    paid = (
        BillPayment.objects.filter(bill=bill, reversal__isnull=True).aggregate(s=Sum("amount_rwf"))[
            "s"
        ]
        or 0
    )
    owed = bill.total_rwf - paid
    today = today or timezone.localdate()
    return BillState(
        bill=bill, paid_rwf=paid, owed_rwf=owed, overdue=owed > 0 and today > bill.due_on
    )


def bills_for_gym(gym: Gym):
    bills = CorporateBill.objects.for_gym(gym).select_related("company")
    return [bill_state(b) for b in bills]


def bills_for_company(company: Company):
    bills = CorporateBill.objects.filter(company=company).select_related("gym")
    return [bill_state(b) for b in bills]


def lines_by_service(bill: CorporateBill) -> list[tuple[str, list[BillLine], int]]:
    """The bill's lines grouped by service, like the paper lists: (service, lines, total)."""
    groups: dict[str, list[BillLine]] = {}
    for line in bill.lines.all():
        groups.setdefault(line.service_name, []).append(line)
    return [(name, lines, sum(x.amount_rwf for x in lines)) for name, lines in groups.items()]


@transaction.atomic
def record_bill_payment(
    *,
    by: StaffAssignment,
    bill: CorporateBill,
    amount_rwf: int,
    reference: str,
    received_on: date,
    idempotency_key,
) -> BillPayment:
    """Record a bank transfer received from the company for a bill."""
    require_role(by, MANAGER_ROLES)
    _same_gym(by, bill)
    CorporateSequence.objects.select_for_update().get_or_create(gym=by.gym)
    earlier = BillPayment.objects.for_gym(by.gym).filter(idempotency_key=idempotency_key).first()
    if earlier is not None:
        return earlier
    reference = _clean(reference)
    if not reference:
        raise InvalidDetails("reference", "required")
    owed = bill_state(bill).owed_rwf
    if not isinstance(amount_rwf, int) or isinstance(amount_rwf, bool) or amount_rwf <= 0:
        raise InvalidDetails("amount_rwf", "out_of_range")
    if amount_rwf > owed:
        raise InvalidDetails("amount_rwf", "more_than_owed")
    if received_on > timezone.localdate():
        raise InvalidDetails("received_on", "future")
    payment = BillPayment.objects.create(
        gym=by.gym,
        bill=bill,
        company=bill.company,
        amount_rwf=amount_rwf,
        reference=reference[:60],
        received_on=received_on,
        recorded_by=by.person,
        idempotency_key=idempotency_key,
    )
    audit.record(
        action="corporate.payment_recorded",
        summary=f"RWF {amount_rwf:,} received from {bill.company.name} for {bill.display_number}",
        gym=by.gym,
        actor=by.person,
        target=payment,
        details={"reference": reference},
    )
    return payment


@transaction.atomic
def reverse_bill_payment(*, by: StaffAssignment, payment: BillPayment, reason: str):
    """Cancel a payment recorded by mistake. It stays visible, marked as reversed."""
    require_role(by, OWNER_ONLY)
    _same_gym(by, payment)
    reason = _clean(reason)
    if not reason:
        raise InvalidDetails("reason", "required")
    if payment.is_reversed:
        raise InvalidDetails("payment", "already_reversed")
    reversal = PaymentReversal.objects.create(
        gym=by.gym,
        payment=payment,
        company=payment.company,
        reason=reason[:300],
        reversed_by=by.person,
    )
    audit.record(
        action="corporate.payment_reversed",
        summary=f"Payment of RWF {payment.amount_rwf:,} from {payment.company.name} reversed",
        gym=by.gym,
        actor=by.person,
        target=payment,
        details={"reason": reason},
    )
    return reversal


@transaction.atomic
def credit_line(*, by: StaffAssignment, line: BillLine, reason: str) -> Credit:
    """Take back a billed service: it appears as a minus line on the next bill."""
    require_role(by, MANAGER_ROLES)
    _same_gym(by, line)
    reason = _clean(reason)
    if not reason:
        raise InvalidDetails("reason", "required")
    if line.activity_id is None or hasattr(line, "credited_by"):
        raise InvalidDetails("line", "cannot_credit")
    credit = Credit.objects.create(
        gym=by.gym,
        company=line.company,
        contract=line.bill.contract,
        original_line=line,
        reason=reason[:300],
        created_by=by.person,
    )
    audit.record(
        action="corporate.credit_given",
        summary=f"{line.service_name} for {line.employee_name} on {line.visit_date:%d %b} credited",
        gym=by.gym,
        actor=by.person,
        target=credit,
        details={"amount_rwf": line.amount_rwf, "reason": reason},
    )
    return credit


def month_so_far(company: Company, today: date | None = None) -> list[dict]:
    """For the company: this month's visits and amount so far, per gym."""
    today = today or timezone.localdate()
    start, _ = month_bounds(today)
    rows = (
        VisitActivity.objects.filter(
            company=company, visit__checked_in_on__range=(start, today), cancelled_at__isnull=True
        )
        .values("gym__name")
        .annotate(total=Sum("price_rwf"))
        .order_by("gym__name")
    )
    return list(rows)


def checked_in_at_local(visit: CorporateVisit) -> datetime:
    return timezone.localtime(visit.checked_in_at)


# --- The company's month at a glance ---------------------------------------------------


@dataclass
class GymMonthRow:
    gym_id: int
    gym_name: str
    cells: dict  # service name -> {"count", "amount"}
    total_rwf: int
    owed_rwf: int
    owed_note: str


def company_month(company: Company, month: date) -> dict:
    """
    For the logistics officer: this month's services at each gym, as
    "visits x price", and what is still owed to each gym on issued bills.
    """
    start, end = month_bounds(month)
    rows = (
        VisitActivity.objects.filter(
            company=company,
            cancelled_at__isnull=True,
            visit__checked_in_on__range=(start, end),
        )
        .values("gym_id", "gym__name", "service_name")
        .annotate(count=Count("id"), amount=Sum("price_rwf"))
    )
    services: list[str] = []
    by_gym: dict[int, GymMonthRow] = {}
    for row in rows:
        if row["service_name"] not in services:
            services.append(row["service_name"])
        entry = by_gym.setdefault(
            row["gym_id"], GymMonthRow(row["gym_id"], row["gym__name"], {}, 0, 0, "")
        )
        entry.cells[row["service_name"]] = {"count": row["count"], "amount": row["amount"]}
        entry.total_rwf += row["amount"]
    # Gyms with an approved contract appear even before anyone visits.
    for contract in Contract.objects.filter(
        company=company, status=ContractStatus.APPROVED
    ).select_related("gym"):
        by_gym.setdefault(
            contract.gym_id, GymMonthRow(contract.gym_id, contract.gym.name, {}, 0, 0, "")
        )
    for state in bills_for_company(company):
        if state.owed_rwf <= 0:
            continue
        entry = by_gym.setdefault(
            state.bill.gym_id, GymMonthRow(state.bill.gym_id, state.bill.gym.name, {}, 0, 0, "")
        )
        entry.owed_rwf += state.owed_rwf
        word = "overdue since" if state.overdue else "due"
        note = f"{state.bill.period_start:%b} bill · {word} {state.bill.due_on:%d %b}"
        entry.owed_note = note if not entry.owed_note else entry.owed_note + "; " + note
    preferred = ["Gym", "Sauna", "Massage", "Swimming", "Aerobics"]
    services.sort(key=lambda n: (preferred.index(n) if n in preferred else 99, n))
    gyms = sorted(by_gym.values(), key=lambda r: (-r.total_rwf, r.gym_name))
    totals = {
        name: {
            "count": sum(g.cells.get(name, {}).get("count", 0) for g in gyms),
            "amount": sum(g.cells.get(name, {}).get("amount", 0) for g in gyms),
        }
        for name in services
    }
    visits = CorporateVisit.objects.filter(company=company, checked_in_on__range=(start, end))
    listed = Employee.objects.filter(company=company, is_active=True).count()
    per_employee = (
        visits.values("employee_id").annotate(n=Count("id")).filter(n__gte=LEAGUE_VISITS).count()
    )
    return {
        "start": start,
        "end": min(end, timezone.localdate()),
        "services": services,
        "gyms": gyms,
        "totals": totals,
        "total_rwf": sum(g.total_rwf for g in gyms),
        "owed_rwf": sum(g.owed_rwf for g in gyms),
        "visits": visits.count(),
        "activities": sum(t["count"] for t in totals.values()),
        "listed": listed,
        "active": per_employee,
    }


def gym_company_month(gym: Gym, month: date) -> list[dict]:
    """
    For the gym owner: each company this month, its visits and the amount so
    far (what the bill will be), and the state of its last bill.
    """
    start, end = month_bounds(month)
    amounts = {
        r["company_id"]: r
        for r in VisitActivity.objects.for_gym(gym)
        .filter(cancelled_at__isnull=True, visit__checked_in_on__range=(start, end))
        .values("company_id")
        .annotate(amount=Sum("price_rwf"))
    }
    visits = dict(
        CorporateVisit.objects.for_gym(gym)
        .filter(checked_in_on__range=(start, end))
        .values("company_id")
        .annotate(n=Count("id"))
        .values_list("company_id", "n")
    )
    rows = []
    for contract in (
        Contract.objects.for_gym(gym)
        .filter(status=ContractStatus.APPROVED)
        .select_related("company")
        .order_by("company__name")
    ):
        last = (
            CorporateBill.objects.for_gym(gym)
            .filter(contract=contract)
            .order_by("-period_start")
            .first()
        )
        state = bill_state(last) if last else None
        rows.append(
            {
                "company": contract.company,
                "visits": visits.get(contract.company_id, 0),
                "amount_rwf": (amounts.get(contract.company_id) or {}).get("amount") or 0,
                "last_bill": state,
            }
        )
    return sorted(rows, key=lambda r: -r["amount_rwf"])


# --- The monthly report a gym sends a company ---------------------------------

# How many whole months a report may cover. A company pays for whole months, so
# a report is always whole months too: half a month would never match a bill.
REPORT_MONTHS = (1, 2, 3, 4, 5, 6, 12)


def report_window(last_month: date, months: int) -> tuple[date, date]:
    """
    The first and last day covered by a report of `months` whole months ending
    with the month `last_month` falls in.
    """
    if months not in REPORT_MONTHS:
        raise InvalidDetails("months", "out_of_range")
    end = last_month.replace(day=calendar.monthrange(last_month.year, last_month.month)[1])
    year, month = last_month.year, last_month.month - (months - 1)
    while month < 1:
        year, month = year - 1, month + 12
    return date(year, month, 1), end


@dataclass(frozen=True)
class ReportLine:
    """One activity on one visit: a line of what the company is being billed for."""

    when: datetime
    company_name: str
    employee_number: str
    employee_name: str
    department: str
    branch_name: str
    service_name: str
    price_rwf: int
    how: str
    cancelled: bool
    cancel_note: str

    @property
    def amount_rwf(self) -> int:
        """Nothing is charged for a service that was taken off the same day."""
        return 0 if self.cancelled else self.price_rwf


def report_lines(
    gym: Gym, *, start: date, end: date, company=None, service=None
) -> list[ReportLine]:
    """
    Every service used by company staff between two days, in detail.

    This is what the gym sends the company: not a total, but who came, from
    which department, when, and what they used. A service taken off the same
    day stays on the list, crossed out, so the company can see the correction.
    """
    activities = (
        VisitActivity.objects.for_gym(gym)
        .filter(visit__checked_in_on__range=(start, end))
        .select_related("visit__employee__person", "visit__branch", "company", "service")
        .order_by("-visit__checked_in_at", "service_name")
    )
    if company is not None:
        activities = activities.filter(company=company)
    if service is not None:
        activities = activities.filter(service=service)

    return [
        ReportLine(
            when=activity.visit.checked_in_at,
            company_name=activity.company.name,
            employee_number=activity.visit.employee.employee_number,
            employee_name=activity.visit.employee.person.full_name,
            department=activity.visit.employee.department,
            branch_name=activity.visit.branch.name,
            service_name=activity.service_name,
            price_rwf=activity.price_rwf,
            how=VisitMethod(activity.visit.method).label,
            cancelled=activity.cancelled_at is not None,
            cancel_note=activity.cancel_reason,
        )
        for activity in activities
    ]


def report_totals(lines: list[ReportLine]) -> list[tuple[str, int, int]]:
    """Per service: how many times it was used and what that comes to."""
    counted: dict[str, list[int]] = {}
    for line in lines:
        if line.cancelled:
            continue
        row = counted.setdefault(line.service_name, [0, 0])
        row[0] += 1
        row[1] += line.amount_rwf
    return [(name, times, amount) for name, (times, amount) in sorted(counted.items())]


def contract_end_dates(gym: Gym, company_ids) -> dict[int, date]:
    """
    For each company, the last day its running contract with this gym covers.
    Used to say "EDCL staff, to 31 Dec 2026" next to an employee's name.
    """
    running = (
        Contract.objects.for_gym(gym)
        .filter(
            company_id__in=set(company_ids),
            status=ContractStatus.APPROVED,
            ended_on__isnull=True,
        )
        .values_list("company_id", "end_date")
        .order_by("end_date")
    )
    return {company_id: end_date for company_id, end_date in running}


def agreed_price(gym: Gym, company, service, branch: Branch, on: date | None = None):
    """
    What a company agreed to pay for one service at one branch, or None when
    its contract does not cover it. An employee pays for what is not covered.
    """
    day = on or timezone.localdate()
    contract = running_contract(gym, company.pk, day)
    if contract is None:
        return None
    price = ContractPrice.objects.filter(contract=contract, service=service, branch=branch).first()
    return price.price_rwf if price else None


def search_employees(gym: Gym, query: str = "", company=None):
    """
    Company staff this gym may see: people at a company it has a contract with.
    Matches on name, staff number or phone number.
    """
    companies = companies_with_contracts(gym)
    if company is not None:
        companies = companies.filter(pk=company.pk)
    people = Employee.objects.filter(company__in=companies, is_active=True).select_related(
        "person", "company"
    )
    text = (query or "").strip()
    if not text:
        return people
    digits = "".join(ch for ch in text if ch.isdigit())
    matches = Q(person__full_name__icontains=text) | Q(employee_number__icontains=text)
    if digits:
        matches |= Q(person__phone__contains=digits)
    return people.filter(matches)
