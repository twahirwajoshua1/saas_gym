"""
Gym screens for corporate wellness: contracts, the company desk, visits,
warnings and monthly bills. Company screens are in portal.py; the
employee's card is in card.py.
"""

import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.attendance import services as attendance
from apps.core.amounts import parse_rwf
from apps.corporate import services
from apps.corporate.models import (
    BillLine,
    BillPayment,
    Contract,
    ContractStatus,
    CorporateBill,
    Employee,
    Flag,
    VisitActivity,
)
from apps.corporate.tenancy import select_company
from apps.gyms import services as gym_services
from apps.gyms.models import Branch
from apps.gyms.permissions import CHECK_IN_ROLES, MANAGER_ROLES, OWNER_ONLY
from apps.gyms.tenancy import gym_required, select_gym
from apps.members.models import Service
from apps.members.services import InvalidDetails
from apps.modules.catalog import CORPORATE_WELLNESS
from apps.modules.decorators import module_required

DESK_BRANCH = "desk_branch"  # the same choice as the member check-in desk

ERRORS = {
    ("company_code", "not_found"): _("No company has this code. Check it with the company."),
    ("end_date", "before_start"): _("The end date is before the start date."),
    ("payment_days", "out_of_range"): _("Payment days must be between 1 and 90."),
    ("prices", "required"): _("Enter a price for at least one service."),
    ("prices", "out_of_range"): _("Prices must be whole francs above zero."),
    ("prices", "invalid"): _("Prices must be whole francs above zero."),
    ("status", "already_approved"): _("The company has approved this contract; it cannot change."),
    ("status", "not_approved"): _("Only a running contract can be ended."),
    ("ended_on", "out_of_range"): _("Choose a day within the contract."),
    ("ended_on", "in_the_past"): _("Choose today or a later day."),
    ("employee", "removed"): _("This employee is no longer on the company's list."),
    ("employee", "no_contract"): _("The company has no running contract with this gym."),
    ("services", "required"): _("Choose at least one service."),
    ("services", "not_covered"): _("The contract does not cover that service at this branch."),
    ("pin", "wrong"): _("Wrong PIN."),
    ("reason", "required"): _("Give a reason."),
    ("note", "required"): _("Write a short note for the company."),
    ("activity", "billed"): _("This is already on a bill. Give a credit from the bill instead."),
    ("month", "not_over"): _("A month's bill is issued on its last day."),
    ("reference", "required"): _("Enter the bank transfer reference."),
    ("amount_rwf", "out_of_range"): _("Enter an amount above zero."),
    ("amount_rwf", "more_than_owed"): _("This is more than what is owed on the bill."),
    ("received_on", "future"): _("The date cannot be in the future."),
    ("payment", "already_reversed"): _("This payment was already reversed."),
    ("line", "cannot_credit"): _("This line was already credited."),
}


def error_text(error: InvalidDetails) -> str:
    return ERRORS.get((error.field, error.reason), _("Check what you entered."))


def corporate_gym(*roles):
    def decorator(view):
        return gym_required(*roles)(module_required(CORPORATE_WELLNESS)(view))

    return decorator


# --- Choosing where to work after signing in --------------------------------------


def start(request):
    """After sign-in: go to your gym or company, or choose when you have several."""
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    person = request.user.person
    assignments = list(gym_services.active_assignments_for(person))
    officer_roles = list(services.officer_roles_for(person))
    if not officer_roles:
        return redirect("gyms:enter")
    if request.method == "POST":
        choice = request.POST.get("company")
        for officer in officer_roles:
            if str(officer.company.public_id) == choice:
                select_company(request, officer)
                return redirect("corporate:portal_home")
        for assignment in assignments:
            if str(assignment.gym.public_id) == request.POST.get("gym"):
                select_gym(request, assignment)
                return redirect("gyms:home")
    if len(officer_roles) == 1 and not assignments:
        select_company(request, officer_roles[0])
        return redirect("corporate:portal_home")
    return render(
        request,
        "corporate/start.html",
        {"assignments": assignments, "officer_roles": officer_roles},
    )


# --- Overview and contracts ------------------------------------------------------


@corporate_gym(*CHECK_IN_ROLES)
def gym_home(request):
    today = timezone.localdate()
    contracts = Contract.objects.for_gym(request.gym).select_related("company")
    is_manager = request.assignment.role in MANAGER_ROLES
    return render(
        request,
        "corporate/gym_home.html",
        {
            "contracts": contracts,
            "today": today,
            "visits_today": services.visits_for_gym(request.gym, today, today),
            "warnings": services.open_flags_for_gym(request.gym) if is_manager else [],
            "bills": [b for b in services.bills_for_gym(request.gym) if b.owed_rwf > 0]
            if is_manager
            else [],
            "is_manager": is_manager,
        },
    )


def _price_grid(gym, contract=None, posted=None):
    """Rows of (service, [(branch, field name, value)]) for the contract price table."""
    current = services.prices_for(contract) if contract else {}
    branches = list(Branch.objects.for_gym(gym).filter(is_active=True))
    rows = []
    for service in Service.objects.for_gym(gym).filter(is_active=True):
        cells = []
        for branch in branches:
            name = f"price_{service.public_id}_{branch.public_id}"
            if posted is not None:
                value = posted.get(name, "")
            else:
                price = current.get((service.id, branch.id))
                value = f"{price}" if price else ""
            cells.append((branch, name, value))
        rows.append((service, cells))
    return branches, rows


def _fill_with_reduction(rows, percent: int):
    """Put member price minus `percent` in every box (rounded to 100 francs)."""
    filled = []
    for service, cells in rows:
        price = max(100, int(round(service.price_rwf * (100 - percent) / 100, -2)))
        filled.append((service, [(branch, name, str(price)) for branch, name, _ in cells]))
    return filled


def _reduction(posted) -> int | None:
    try:
        percent = int((posted.get("reduction") or "").strip().rstrip("%"))
    except ValueError:
        return None
    return percent if 0 <= percent < 100 else None


def _read_terms(request, rows):
    prices = {}
    for service, cells in rows:
        for branch, _name, value in cells:
            if value.strip():
                amount = parse_rwf(value)
                if amount is None:
                    raise InvalidDetails("prices", "invalid")
                prices[(service.id, branch.id)] = amount
    start = parse_date(request.POST.get("start_date", ""))
    end = parse_date(request.POST.get("end_date", ""))
    if start is None:
        raise InvalidDetails("start_date", "required")
    if end is None:
        raise InvalidDetails("end_date", "required")
    try:
        days = int(request.POST.get("payment_days", ""))
    except ValueError as error:
        raise InvalidDetails("payment_days", "out_of_range") from error
    return services.ContractTerms(start_date=start, end_date=end, payment_days=days, prices=prices)


def _price_grid_for_company(contract):
    """The contract's prices as read-only rows, for the company's screens."""
    current = services.prices_for(contract)
    branches = list(Branch.objects.filter(gym=contract.gym, is_active=True))
    rows = []
    for service in Service.objects.filter(gym=contract.gym, is_active=True):
        cells = [(branch, "", current.get((service.id, branch.id))) for branch in branches]
        if any(price for _, _, price in cells):
            rows.append((service, cells))
    return branches, rows


@corporate_gym(*MANAGER_ROLES)
def contract_new(request):
    posted = request.POST if request.method == "POST" else None
    branches, rows = _price_grid(request.gym, posted=posted)
    values = {
        "company_code": "",
        "start_date": f"{timezone.localdate():%Y-%m-%d}",
        "end_date": "",
        "payment_days": "30",
    }
    found, error = None, ""
    if posted is not None:
        values = {key: posted.get(key, "") for key in values}
        found = services.company_for_code(values["company_code"])
        if posted.get("action") == "fill":
            percent = _reduction(posted)
            if percent is None:
                error = _("Enter the reduction as a whole number from 0 to 99.")
            else:
                rows = _fill_with_reduction(rows, percent)
        if found is None:
            error = error_text(InvalidDetails("company_code", "not_found"))
        elif posted.get("action") == "propose":
            try:
                terms = _read_terms(request, rows)
                contract = services.propose_contract(
                    by=request.assignment, company_code=values["company_code"], terms=terms
                )
            except InvalidDetails as problem:
                error = error_text(problem)
            else:
                messages.success(
                    request,
                    _("Contract sent to %(company)s for approval.") % {"company": found[1]},
                )
                return redirect("corporate:contract_detail", public_id=contract.public_id)
    return render(
        request,
        "corporate/contract_form.html",
        {
            "values": values,
            "found": found,
            "error": error,
            "branches": branches,
            "rows": rows,
        },
    )


def _contract(request, public_id) -> Contract:
    return get_object_or_404(
        Contract.objects.for_gym(request.gym).select_related("company"), public_id=public_id
    )


@corporate_gym(*MANAGER_ROLES)
def contract_detail(request, public_id):
    contract = _contract(request, public_id)
    branches, rows = _price_grid_for_company(contract)
    return render(
        request,
        "corporate/contract_detail.html",
        {
            "contract": contract,
            "branches": branches,
            "rows": rows,
            "can_change": contract.status in (ContractStatus.PROPOSED, ContractStatus.DECLINED),
            "can_end": contract.status == ContractStatus.APPROVED
            and contract.ended_on is None
            and request.assignment.role in OWNER_ONLY,
            "today": timezone.localdate(),
        },
    )


@corporate_gym(*MANAGER_ROLES)
def contract_edit(request, public_id):
    contract = _contract(request, public_id)
    posted = request.POST if request.method == "POST" else None
    branches, rows = _price_grid(request.gym, contract=contract, posted=posted)
    values = {
        "company_code": contract.company.code,
        "start_date": f"{contract.start_date:%Y-%m-%d}",
        "end_date": f"{contract.end_date:%Y-%m-%d}",
        "payment_days": str(contract.payment_days),
    }
    error = ""
    if posted is not None and posted.get("action") == "fill":
        values |= {k: posted.get(k, "") for k in ("start_date", "end_date", "payment_days")}
        percent = _reduction(posted)
        if percent is None:
            error = _("Enter the reduction as a whole number from 0 to 99.")
        else:
            rows = _fill_with_reduction(rows, percent)
    elif posted is not None:
        values |= {k: posted.get(k, "") for k in ("start_date", "end_date", "payment_days")}
        try:
            services.change_proposal(
                by=request.assignment, contract=contract, terms=_read_terms(request, rows)
            )
        except InvalidDetails as problem:
            error = error_text(problem)
        else:
            messages.success(request, _("The changed contract was sent to the company again."))
            return redirect("corporate:contract_detail", public_id=contract.public_id)
    return render(
        request,
        "corporate/contract_form.html",
        {
            "values": values,
            "found": (contract.company_id, contract.company.name),
            "contract": contract,
            "error": error,
            "branches": branches,
            "rows": rows,
        },
    )


@corporate_gym(*OWNER_ONLY)
@require_POST
def contract_end(request, public_id):
    contract = _contract(request, public_id)
    on = parse_date(request.POST.get("ended_on", "")) or timezone.localdate()
    try:
        services.end_contract(by=request.assignment, contract=contract, on=on)
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    else:
        messages.success(request, _("The contract ends on %(day)s.") % {"day": f"{on:%d %b %Y}"})
    return redirect("corporate:contract_detail", public_id=contract.public_id)


# --- The company desk ---------------------------------------------------------------


def _desk_branch(request):
    chosen = None
    chosen_id = request.session.get(DESK_BRANCH)
    if chosen_id:
        chosen = Branch.objects.for_gym(request.gym).filter(public_id=chosen_id).first()
    try:
        return attendance.desk_branch(request.assignment, chosen)
    except attendance.BranchNeeded:
        return None


def _covered_services(lookup, branch):
    """The services the employee's contract covers at this branch, with prices."""
    if lookup.contract is None or branch is None:
        return []
    prices = services.prices_for(lookup.contract)
    taken = set()
    if lookup.visit_today is not None:
        branch = lookup.visit_today.branch
        taken = {
            a.service_id for a in lookup.visit_today.activities.all() if a.cancelled_at is None
        }
    result = []
    for service in Service.objects.for_gym(lookup.contract.gym).filter(is_active=True):
        price = prices.get((service.id, branch.id))
        if price:
            result.append({"service": service, "price": price, "taken": service.id in taken})
    return result


@corporate_gym(*CHECK_IN_ROLES)
def desk(request):
    query = request.GET.get("q", "")
    branch = _desk_branch(request)
    lookups = services.find_for_desk(request.gym, query)
    for lookup in lookups:
        lookup.covered = _covered_services(lookup, branch)
    match = services.CARD_LINK.search(query)
    context = {
        "query": query,
        "lookups": lookups,
        "searched": len(query.strip()) >= 2,
        "desk_branch": branch,
        "code": match.group(2) if match and match.group(2) else "",
        "today": timezone.localdate(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "corporate/_desk_results.html", context)
    return render(request, "corporate/desk.html", context)


@corporate_gym(*CHECK_IN_ROLES)
@require_POST
def desk_check_in(request, public_id):
    employee = get_object_or_404(
        Employee.objects.select_related("person", "company"), public_id=public_id
    )
    code = request.POST.get("code", "")
    service_ids = [
        s.id
        for s in Service.objects.for_gym(request.gym).filter(
            public_id__in=[v for v in request.POST.getlist("service") if _is_uuid(v)]
        )
    ]
    try:
        result = services.check_in_employee(
            by=request.assignment,
            employee=employee,
            service_ids=service_ids,
            branch=_desk_branch(request),
            phone_code_ok=services.code_is_fresh(employee, code),
            pin=request.POST.get("pin", "").strip(),
        )
    except services.NeedsProof:
        messages.error(
            request,
            _(
                "Ask %(name)s to open their card on their phone and scan it again, "
                "or to type their PIN."
            )
            % {"name": employee.full_name},
        )
    except services.PinLocked:
        messages.error(request, _("Too many wrong PINs. Try again in 15 minutes."))
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    except attendance.BranchNeeded:
        messages.error(request, _("Choose this desk's branch on the Check-in page first."))
    else:
        names = ", ".join(a.service_name for a in result.added) or _("nothing new")
        messages.success(
            request,
            _("%(name)s (%(company)s) checked in: %(services)s.")
            % {"name": employee.full_name, "company": employee.company.name, "services": names},
        )
        if result.warned_two_places:
            messages.warning(
                request,
                _(
                    "Warning: %(name)s was also checked in at another place within the last "
                    "hour. The company has been alerted."
                )
                % {"name": employee.full_name},
            )
    return redirect(reverse("corporate:desk"))


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


# --- Visits and warnings -----------------------------------------------------------


@corporate_gym(*CHECK_IN_ROLES)
def visit_list(request):
    day = parse_date(request.GET.get("day", "")) or timezone.localdate()
    companies = services.companies_with_contracts(request.gym)
    chosen = next(
        (c for c in companies if str(c.public_id) == request.GET.get("company", "")), None
    )
    visits = list(
        services.visits_for_gym(request.gym, day, day, company_id=chosen.id if chosen else None)
    )
    return render(
        request,
        "corporate/gym_visits.html",
        {
            "day": day,
            "visits": visits,
            "companies": companies,
            "company": chosen,
            "day_total": sum(v.billed_rwf for v in visits),
        },
    )


@corporate_gym(*CHECK_IN_ROLES)
@require_POST
def activity_cancel(request, public_id):
    activity = get_object_or_404(
        VisitActivity.objects.for_gym(request.gym).select_related("visit__employee__person"),
        public_id=public_id,
    )
    try:
        services.cancel_activity(
            by=request.assignment, activity=activity, reason=request.POST.get("reason", "")
        )
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    else:
        messages.success(
            request, _("%(service)s was cancelled.") % {"service": activity.service_name}
        )
    return redirect(
        f"{reverse('corporate:visit_list')}?day={activity.visit.checked_in_on:%Y-%m-%d}"
    )


@corporate_gym(*MANAGER_ROLES)
def warning_list(request):
    return render(
        request,
        "corporate/gym_warnings.html",
        {"warnings": services.open_flags_for_gym(request.gym)},
    )


@corporate_gym(*MANAGER_ROLES)
@require_POST
def warning_settle(request, public_id):
    flag = get_object_or_404(
        Flag.objects.for_gym(request.gym).select_related("visit__employee__person"),
        public_id=public_id,
    )
    try:
        services.settle_flag(by=request.assignment, flag=flag, note=request.POST.get("note", ""))
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    else:
        messages.success(request, _("The visit was taken off the bill."))
    return redirect("corporate:warning_list")


# --- Bills ----------------------------------------------------------------------------


@corporate_gym(*MANAGER_ROLES)
def bill_list(request):
    today = timezone.localdate()
    # Months that have ended (or end today) and could still be billed by hand.
    ready = []
    for contract in (
        Contract.objects.for_gym(request.gym)
        .filter(status=ContractStatus.APPROVED)
        .select_related("company")
    ):
        billed = set(
            CorporateBill.objects.filter(contract=contract).values_list("period_start", flat=True)
        )
        for month in services.months_to_bill(contract, today):
            if (
                month not in billed
                and services.billable_activities(contract, services.month_bounds(month)[1]).exists()
            ):
                ready.append((contract, month))
    return render(
        request,
        "corporate/gym_bills.html",
        {"bills": services.bills_for_gym(request.gym), "ready": ready},
    )


@corporate_gym(*MANAGER_ROLES)
@require_POST
def bill_issue(request, public_id):
    contract = _contract(request, public_id)
    month = parse_date(request.POST.get("month", ""))
    if month is None:
        raise PermissionDenied("No month given.")
    try:
        bill = services.issue_bill(contract=contract, month=month, by=request.assignment)
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
        return redirect("corporate:bill_list")
    if bill is None:
        messages.info(request, _("There was nothing to bill."))
        return redirect("corporate:bill_list")
    messages.success(request, _("%(number)s was issued.") % {"number": bill.display_number})
    return redirect("corporate:bill_detail", public_id=bill.public_id)


def _bill(request, public_id) -> CorporateBill:
    return get_object_or_404(
        CorporateBill.objects.for_gym(request.gym).select_related("company", "contract"),
        public_id=public_id,
    )


@corporate_gym(*MANAGER_ROLES)
def bill_detail(request, public_id):
    bill = _bill(request, public_id)
    return render(
        request,
        "corporate/bill_detail.html",
        {
            "state": services.bill_state(bill),
            "bill": bill,
            "groups": services.lines_by_service(bill),
            "payments": BillPayment.objects.for_gym(request.gym)
            .filter(bill=bill)
            .select_related("reversal"),
            "side": "gym",
            "layout": "layouts/gym.html",
            "key": uuid.uuid4(),
            "today": timezone.localdate(),
            "can_reverse": request.assignment.role in OWNER_ONLY,
        },
    )


@corporate_gym(*MANAGER_ROLES)
@require_POST
def bill_payment(request, public_id):
    bill = _bill(request, public_id)
    try:
        key = uuid.UUID(request.POST.get("key", ""))
    except ValueError as error:
        raise PermissionDenied("Missing form code.") from error
    amount = parse_rwf(request.POST.get("amount_rwf", ""))
    received = parse_date(request.POST.get("received_on", "")) or timezone.localdate()
    try:
        services.record_bill_payment(
            by=request.assignment,
            bill=bill,
            amount_rwf=amount if amount is not None else 0,
            reference=request.POST.get("reference", ""),
            received_on=received,
            idempotency_key=key,
        )
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    else:
        messages.success(request, _("Payment recorded."))
    return redirect("corporate:bill_detail", public_id=bill.public_id)


@corporate_gym(*OWNER_ONLY)
@require_POST
def bill_payment_reverse(request, public_id):
    payment = get_object_or_404(
        BillPayment.objects.for_gym(request.gym).select_related("bill", "company"),
        public_id=public_id,
    )
    try:
        services.reverse_bill_payment(
            by=request.assignment, payment=payment, reason=request.POST.get("reason", "")
        )
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    else:
        messages.success(request, _("The payment was reversed."))
    return redirect("corporate:bill_detail", public_id=payment.bill.public_id)


@corporate_gym(*MANAGER_ROLES)
@require_POST
def bill_line_credit(request, pk):
    line = get_object_or_404(
        BillLine.objects.for_gym(request.gym).select_related("bill__contract"), pk=pk
    )
    try:
        services.credit_line(
            by=request.assignment, line=line, reason=request.POST.get("reason", "")
        )
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    else:
        messages.success(request, _("Credit given. It will appear on the next bill."))
    return redirect("corporate:bill_detail", public_id=line.bill.public_id)
