"""
Screens for the company's logistics officer: employees, contracts, visits,
warnings and bills, at every gym the company has a contract with.
"""

import csv
import io
from datetime import timedelta
from urllib.parse import quote

import segno
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.corporate import services
from apps.corporate.models import (
    BillPayment,
    Contract,
    ContractStatus,
    CorporateBill,
    CorporateVisit,
    Employee,
    Flag,
)
from apps.corporate.services import EmployeeDetails
from apps.corporate.tenancy import company_required
from apps.corporate.views import _price_grid_for_company, error_text
from apps.members.services import InvalidDetails

EMPLOYEE_ERRORS = {
    ("full_name", "required"): _("Enter the employee's name."),
    ("phone", "invalid"): _("Enter a valid phone number."),
    ("phone", "already_listed"): _("Someone with this phone number is already on the list."),
    ("employee_number", "required"): _("Enter the employee number."),
    ("employee_number", "duplicate"): _("Another employee already has this number."),
    ("pin", "invalid"): _("The PIN must be exactly 4 digits."),
    ("message", "required"): _("Say what is wrong with this visit."),
    ("status", "not_proposed"): _("This contract has already been decided."),
    ("file", "headings"): _(
        "The file needs columns named Name, Phone and Employee number (Department is optional)."
    ),
}
IMPORT_SESSION = "employee_import_rows"
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def _error(problem: InvalidDetails) -> str:
    return EMPLOYEE_ERRORS.get((problem.field, problem.reason)) or error_text(problem)


def qr_svg(text: str) -> str:
    return segno.make(text, error="m").svg_inline(
        scale=4, dark="#10201a", light="#ffffff", omitsize=True
    )


def _month(request):
    """The month chosen with ?month=2026-10 (default: this month), as (start, end)."""
    chosen = parse_date((request.GET.get("month") or "") + "-01")
    return services.month_bounds(chosen or timezone.localdate())


@company_required
def portal_home(request):
    company = request.company
    today = timezone.localdate()
    mine, league = services.league_place(company.id, today)
    return render(
        request,
        "corporate/portal_home.html",
        {
            "month": services.company_month(company, today),
            "league": league,
            "league_mine": mine,
            "departments": services.department_league(company, today)[:6],
            "warnings": services.open_flags_for_company(company)[:10],
            "pending": Contract.objects.filter(
                company=company, status=ContractStatus.PROPOSED
            ).select_related("gym"),
            "unpaid": [b for b in services.bills_for_company(company) if b.owed_rwf > 0],
            "employee_count": services.employees_for(company).count(),
            "today": timezone.localdate(),
        },
    )


# --- Employees ---------------------------------------------------------------------


@company_required
def employee_list(request):
    query = request.GET.get("q", "")
    show_removed = request.GET.get("removed") == "1"
    employees = services.employees_for(request.company, query, active_only=not show_removed)
    if show_removed:
        employees = employees.filter(is_active=False)
    context = {"employees": employees[:300], "query": query, "show_removed": show_removed}
    if request.headers.get("HX-Request"):
        return render(request, "corporate/_employee_rows.html", context)
    return render(request, "corporate/employee_list.html", context)


@company_required
def employee_add(request):
    values = {"full_name": "", "phone": "", "employee_number": "", "department": ""}
    error = ""
    if request.method == "POST":
        values = {key: request.POST.get(key, "") for key in values}
        try:
            employee = services.add_employee(by=request.officer, details=EmployeeDetails(**values))
        except InvalidDetails as problem:
            error = _error(problem)
        else:
            messages.success(request, _("%(name)s was added.") % {"name": employee.full_name})
            return redirect("corporate:employee_detail", public_id=employee.public_id)
    return render(request, "corporate/employee_form.html", {"values": values, "error": error})


def _employee(request, public_id) -> Employee:
    return get_object_or_404(
        Employee.objects.filter(company=request.company).select_related("person"),
        public_id=public_id,
    )


@company_required
def employee_detail(request, public_id):
    employee = _employee(request, public_id)
    error = ""
    if request.method == "POST":
        try:
            services.update_employee(
                by=request.officer,
                employee=employee,
                employee_number=request.POST.get("employee_number", ""),
                department=request.POST.get("department", ""),
            )
        except InvalidDetails as problem:
            error = _error(problem)
        else:
            messages.success(request, _("Changes saved."))
            return redirect("corporate:employee_detail", public_id=employee.public_id)
    url = request.build_absolute_uri(reverse("corporate:card", args=[employee.card_token]))
    text = _("Your %(company)s gym card: %(url)s") % {"company": request.company.name, "url": url}
    today = timezone.localdate()
    return render(
        request,
        "corporate/employee_detail.html",
        {
            "employee": employee,
            "error": error,
            "card_url": url,
            "whatsapp_url": f"https://wa.me/{employee.person.phone.lstrip('+')}?text={quote(text)}",
            "sms_url": f"sms:{employee.person.phone}?body={quote(text)}",
            "visits": CorporateVisit.objects.filter(employee=employee)
            .filter(checked_in_on__gte=today - timedelta(days=90))
            .select_related("gym", "branch")
            .prefetch_related("activities")[:50],
        },
    )


@company_required
@require_POST
def employee_action(request, public_id):
    employee = _employee(request, public_id)
    action = request.POST.get("action")
    try:
        if action == "remove":
            services.remove_employee(by=request.officer, employee=employee)
            messages.success(request, _("%(name)s was removed.") % {"name": employee.full_name})
        elif action == "restore":
            services.add_employee(
                by=request.officer,
                details=EmployeeDetails(
                    full_name=employee.full_name,
                    phone=employee.person.phone,
                    employee_number=employee.employee_number,
                    department=employee.department,
                ),
            )
            messages.success(
                request, _("%(name)s is back on the list.") % {"name": employee.full_name}
            )
        elif action == "pin":
            services.set_pin(by=request.officer, employee=employee, pin=request.POST.get("pin", ""))
            messages.success(request, _("PIN saved. Tell the employee their PIN in person."))
        elif action == "reset_phone":
            services.reset_card(by=request.officer, employee=employee)
            messages.success(request, _("The card can now be opened on a new phone."))
        elif action == "new_link":
            services.reset_card(by=request.officer, employee=employee, new_link=True)
            messages.success(request, _("New card link made. The old link no longer works."))
    except InvalidDetails as problem:
        messages.error(request, _error(problem))
    return redirect("corporate:employee_detail", public_id=employee.public_id)


def _read_upload(upload) -> list[list]:
    name = (upload.name or "").lower()
    data = upload.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise InvalidDetails("file", "too_big")
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [row for row in csv.reader(io.StringIO(text))]
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        except Exception as error:  # a damaged or wrong file
            raise InvalidDetails("file", "unreadable") from error
        return [list(row) for row in sheet.iter_rows(values_only=True)][:5001]
    raise InvalidDetails("file", "type")


def _rows_from_session(request):
    stored = request.session.get(IMPORT_SESSION) or []
    return [
        services.ImportRow(line=row["line"], details=EmployeeDetails(**row["details"]))
        for row in stored
    ]


@company_required
def employee_import(request):
    error = ""
    if request.method == "POST" and request.FILES.get("file"):
        try:
            rows = services.read_employee_rows(_read_upload(request.FILES["file"]))
        except InvalidDetails as problem:
            error = EMPLOYEE_ERRORS.get(
                (problem.field, problem.reason),
                _("Upload an Excel (.xlsx) or CSV file under 2 MB."),
            )
        else:
            request.session[IMPORT_SESSION] = [
                {"line": r.line, "details": r.details.__dict__} for r in rows
            ]
            return redirect("corporate:employee_import_review")
    return render(request, "corporate/employee_import.html", {"error": error})


@company_required
def employee_import_review(request):
    rows = _rows_from_session(request)
    if not rows:
        return redirect("corporate:employee_import")
    plan = services.plan_import(request.officer, rows)
    if request.method == "POST":
        result = services.apply_import(
            by=request.officer,
            plan=plan,
            remove_missing=request.POST.get("remove_missing") == "yes",
        )
        request.session.pop(IMPORT_SESSION, None)
        messages.success(
            request,
            _("List updated: %(a)s added, %(u)s changed, %(r)s removed.")
            % {"a": result.added, "u": result.updated, "r": result.removed},
        )
        if result.failed:
            messages.warning(
                request,
                _("%(n)s row(s) could not be saved; check them and add them by hand.")
                % {"n": len(result.failed)},
            )
        return redirect("corporate:employee_list")
    return render(request, "corporate/employee_import_review.html", {"plan": plan})


# --- Contracts ------------------------------------------------------------------------


@company_required
def contract_list(request):
    contracts = Contract.objects.filter(company=request.company).select_related("gym")
    return render(
        request,
        "corporate/portal_contracts.html",
        {"contracts": contracts, "company": request.company},
    )


def _contract(request, public_id) -> Contract:
    return get_object_or_404(
        Contract.objects.filter(company=request.company).select_related("gym"),
        public_id=public_id,
    )


@company_required
def contract_detail(request, public_id):
    contract = _contract(request, public_id)
    branches, rows = _price_grid_for_company(contract)
    return render(
        request,
        "corporate/portal_contract_detail.html",
        {
            "contract": contract,
            "branches": branches,
            "rows": rows,
            "today": timezone.localdate(),
        },
    )


@company_required
@require_POST
def contract_decide(request, public_id):
    contract = _contract(request, public_id)
    approve = request.POST.get("decision") == "approve"
    try:
        services.decide_contract(by=request.officer, contract=contract, approve=approve)
    except InvalidDetails as problem:
        messages.error(request, _error(problem))
    else:
        messages.success(
            request,
            _("Contract approved. Your employees can now use %(gym)s.") % {"gym": contract.gym.name}
            if approve
            else _("Contract declined. %(gym)s can change it and send it again.")
            % {"gym": contract.gym.name},
        )
    return redirect("corporate:portal_contract_detail", public_id=contract.public_id)


@company_required
@require_POST
def contract_end(request, public_id):
    contract = _contract(request, public_id)
    on = parse_date(request.POST.get("ended_on", "")) or timezone.localdate()
    try:
        services.end_contract(by=request.officer, contract=contract, on=on)
    except InvalidDetails as problem:
        messages.error(request, _error(problem))
    else:
        messages.success(request, _("The contract ends on %(day)s.") % {"day": f"{on:%d %b %Y}"})
    return redirect("corporate:portal_contract_detail", public_id=contract.public_id)


# --- Visits and warnings -----------------------------------------------------------


@company_required
def visit_list(request):
    start, end = _month(request)
    gym_id = request.GET.get("gym") or None
    contracts = Contract.objects.filter(company=request.company).select_related("gym")
    gyms = {c.gym_id: c.gym for c in contracts}
    visits = services.visits_for_company(
        request.company, start, end, gym_id=int(gym_id) if gym_id and gym_id.isdigit() else None
    )
    total = sum(a.price_rwf for v in visits for a in v.activities.all() if a.cancelled_at is None)
    months = []
    month = timezone.localdate().replace(day=1)
    for _i in range(12):
        months.append(month)
        month = (month - timedelta(days=1)).replace(day=1)
    return render(
        request,
        "corporate/portal_visits.html",
        {
            "visits": visits,
            "start": start,
            "gyms": gyms.values(),
            "gym_id": gym_id,
            "months": months,
            "total": total,
        },
    )


@company_required
@require_POST
def visit_question(request, public_id):
    visit = get_object_or_404(
        CorporateVisit.objects.filter(company=request.company).select_related(
            "gym", "employee__person"
        ),
        public_id=public_id,
    )
    try:
        services.question_visit(
            by=request.officer, visit=visit, message=request.POST.get("message", "")
        )
    except InvalidDetails as problem:
        messages.error(request, _error(problem))
    else:
        messages.success(
            request,
            _(
                "The visit is questioned. %(gym)s sees the warning; "
                "it stays off the bill until settled."
            )
            % {"gym": visit.gym.name},
        )
    return redirect(request.POST.get("next") or reverse("corporate:portal_visits"))


@company_required
def warning_list(request):
    return render(
        request,
        "corporate/portal_warnings.html",
        {
            "warnings": services.open_flags_for_company(request.company),
            "settled": Flag.objects.filter(
                company=request.company, settled_at__isnull=False
            ).select_related("gym", "visit__employee__person")[:30],
        },
    )


@company_required
@require_POST
def warning_accept(request, public_id):
    flag = get_object_or_404(Flag.objects.filter(company=request.company), public_id=public_id)
    services.settle_flag(by=request.officer, flag=flag, note=request.POST.get("note", ""))
    messages.success(request, _("Accepted. The visit will go on the next bill."))
    return redirect("corporate:portal_warnings")


# --- League ---------------------------------------------------------------------------


@company_required
def league(request):
    today = timezone.localdate()
    mine, rows = services.league_place(request.company.id, today)
    return render(
        request,
        "corporate/portal_league.html",
        {
            "rows": rows,
            "mine": mine,
            "departments": services.department_league(request.company, today),
            "today": today,
            "minimum": services.LEAGUE_VISITS,
        },
    )


# --- Bills ----------------------------------------------------------------------------


@company_required
def bill_list(request):
    return render(
        request,
        "corporate/portal_bills.html",
        {"bills": services.bills_for_company(request.company)},
    )


@company_required
def bill_detail(request, public_id):
    bill = get_object_or_404(
        CorporateBill.objects.filter(company=request.company).select_related("gym", "contract"),
        public_id=public_id,
    )
    return render(
        request,
        "corporate/bill_detail.html",
        {
            "state": services.bill_state(bill),
            "bill": bill,
            "groups": services.lines_by_service(bill),
            "payments": BillPayment.objects.filter(bill=bill).select_related("reversal"),
            "side": "company",
            "layout": "layouts/company.html",
        },
    )
