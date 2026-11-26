"""
Screens: the receptionist's Desk, connecting entrance tablets, and the
tablet itself (which has no sign-in: it is identified by a secret cookie).
"""

import uuid

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.billing import services as billing
from apps.billing.forms import PayNow, SignUpPaymentForm
from apps.corporate import services as corporate
from apps.corporate.models import Employee
from apps.frontdesk import services
from apps.frontdesk.models import EntranceTablet, ScanProblem
from apps.gyms import session
from apps.gyms.models import Branch
from apps.gyms.permissions import CHECK_IN_ROLES, FRONT_DESK_ROLES, MANAGER_ROLES
from apps.gyms.tenancy import gym_required
from apps.massage import services as massage
from apps.members import services as member_services
from apps.members.models import Member, SignUp, SignUpStatus
from apps.modules import services as module_services
from apps.modules.catalog import (
    ATTENDANCE_ACCESS,
    CORPORATE_WELLNESS,
    PAYMENTS_FINANCE,
    TRAINING_CLASSES,
)
from apps.modules.decorators import module_required

COOKIE = "gymos_tablet"
COOKIE_AGE = 3 * 365 * 24 * 3600


def _desk_branch(request) -> Branch | None:
    return session.desk_branch(request)


# --- The Desk ---------------------------------------------------------------------


@gym_required(*CHECK_IN_ROLES)
def desk(request):
    if not module_services.is_enabled(request.gym, ATTENDANCE_ACCESS):
        # Without check-in switched on, the desk's daily work is the member list.
        return redirect("frontdesk:people")
    today = timezone.localdate()
    branch = _desk_branch(request)
    shift = None
    if request.assignment.role in FRONT_DESK_ROLES and module_services.is_enabled(
        request.gym, PAYMENTS_FINANCE
    ):
        shift = billing.shift_summary(request.gym, request.user.person)
    context = {
        "today": today,
        "branch": branch,
        "branches": Branch.objects.for_gym(request.gym).filter(is_active=True),
        "counts": services.counts_today(request.gym, today, branch),
        "problems": services.open_problems(request.gym, branch),
        "arrivals": services.arrivals(request.gym, today, branch),
        "shift": shift,
        "tablets": services.tablets_for(request.gym).filter(
            **({"branch": branch} if branch else {})
        ),
        "corporate_on": module_services.is_enabled(request.gym, CORPORATE_WELLNESS),
        "reminders": services.reminders(request.gym, today),
        "signups": member_services.waiting_signups(request.gym)
        if module_services.is_enabled(request.gym, PAYMENTS_FINANCE)
        else [],
        "starting_soon": services.starting_soon(request.gym),
        "massage_requests": massage.waiting_for_the_gym(request.gym, branch)
        if module_services.is_enabled(request.gym, TRAINING_CLASSES)
        else [],
    }
    if request.headers.get("HX-Request"):
        return render(request, "frontdesk/_desk_live.html", context)
    return render(request, "frontdesk/desk.html", context)


@gym_required(*CHECK_IN_ROLES)
@require_POST
def problem_done(request, public_id):
    problem = get_object_or_404(ScanProblem.objects.for_gym(request.gym), public_id=public_id)
    services.resolve(by=request.assignment, problem=problem)
    return redirect("frontdesk:desk")


# --- Connecting tablets ------------------------------------------------------------


@gym_required(*MANAGER_ROLES)
@module_required(ATTENDANCE_ACCESS)
def tablets(request):
    branches = Branch.objects.for_gym(request.gym).filter(is_active=True)
    if request.method == "POST":
        branch = get_object_or_404(branches, public_id=request.POST.get("branch") or None)
        tablet = services.add_tablet(
            by=request.assignment, branch=branch, name=request.POST.get("name", "")
        )
        messages.success(
            request,
            _("Tablet added. On the tablet, open %(url)s and type the code %(code)s.")
            % {"url": request.build_absolute_uri("/tablet/"), "code": tablet.pairing_code},
        )
        return redirect("frontdesk:tablets")
    return render(
        request,
        "frontdesk/tablets.html",
        {
            "tablets": services.tablets_for(request.gym),
            "branches": branches,
            "tablet_url": request.build_absolute_uri("/tablet/"),
            "now": timezone.now(),
        },
    )


@gym_required(*MANAGER_ROLES)
@require_POST
def tablet_action(request, public_id):
    tablet = get_object_or_404(EntranceTablet.objects.for_gym(request.gym), public_id=public_id)
    if request.POST.get("action") == "disconnect":
        services.disconnect(by=request.assignment, tablet=tablet)
        messages.success(request, _("%(name)s was disconnected.") % {"name": tablet.name})
    else:
        services.new_code(by=request.assignment, tablet=tablet)
        messages.success(
            request,
            _("New code for %(name)s: %(code)s")
            % {"name": tablet.name, "code": tablet.pairing_code},
        )
    return redirect("frontdesk:tablets")


# --- The tablet itself (no sign-in) --------------------------------------------------


def _tablet(request):
    return services.tablet_for(request.COOKIES.get(COOKIE))


def _kiosk_context(tablet):
    today = timezone.localdate()
    league = []
    if module_services.is_enabled(tablet.gym, CORPORATE_WELLNESS):
        league = corporate.league(today)[:5]
    return {"tablet": tablet, "league": league}


def kiosk(request):
    tablet = _tablet(request)
    if tablet is None:
        return render(request, "frontdesk/kiosk_connect.html", {"error": ""})
    response = render(request, "frontdesk/kiosk.html", _kiosk_context(tablet))
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def kiosk_connect(request):
    result = services.connect(request.POST.get("code", ""))
    if result is None:
        return render(
            request,
            "frontdesk/kiosk_connect.html",
            {"error": _("That code is not right or has expired. Ask the gym owner for a new one.")},
        )
    tablet, secret = result
    response = redirect("frontdesk:kiosk")
    response.set_cookie(
        COOKIE,
        secret,
        max_age=COOKIE_AGE,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure(),
    )
    return response


def _outcome(request, tablet, outcome):
    return render(request, "frontdesk/_kiosk_outcome.html", {"o": outcome, "tablet": tablet})


def _gone():
    response = HttpResponse("", status=403)
    response["HX-Redirect"] = "/tablet/"
    return response


@require_POST
def kiosk_scan(request):
    tablet = _tablet(request)
    if tablet is None:
        return _gone()
    return _outcome(request, tablet, services.scan(tablet, request.POST.get("text", "")))


@require_POST
def kiosk_member_enter(request, public_id):
    """A member ticked on the tablet what they are doing today."""
    tablet = _tablet(request)
    if tablet is None:
        return _gone()
    member = get_object_or_404(
        Member.objects.for_gym(tablet.gym).select_related("person"), public_id=public_id
    )
    outcome = services.member_enter(tablet, member, request.POST.getlist("service"))
    return _outcome(request, tablet, outcome)


@require_POST
def kiosk_enter(request, public_id):
    tablet = _tablet(request)
    if tablet is None:
        return _gone()
    employee = get_object_or_404(
        Employee.objects.select_related("person", "company"), public_id=public_id
    )
    chosen = []
    for value in request.POST.getlist("service"):
        try:
            chosen.append(uuid.UUID(value))
        except ValueError:
            continue
    outcome = services.employee_enter(
        tablet,
        employee,
        chosen,
        code=request.POST.get("code", ""),
        pin=request.POST.get("pin", ""),
    )
    return _outcome(request, tablet, outcome)


def kiosk_pin(request):
    tablet = _tablet(request)
    if tablet is None:
        return _gone()
    if request.method == "POST":
        outcome = services.pin_entry(
            tablet, request.POST.get("phone", ""), request.POST.get("pin", "")
        )
        if outcome.kind == "choose":
            outcome.code = ""
            return render(
                request,
                "frontdesk/_kiosk_outcome.html",
                {"o": outcome, "tablet": tablet, "pin": request.POST.get("pin", "")},
            )
        return _outcome(request, tablet, outcome)
    return render(request, "frontdesk/_kiosk_pin.html", {"tablet": tablet})


def kiosk_idle(request):
    return HttpResponse("")


# --- Everyone the desk deals with -------------------------------------------


@gym_required(*FRONT_DESK_ROLES)
def people(request):
    """
    The Members page: the gym's own members and the staff of its companies,
    in one list, because at the desk they are both just somebody at the door.
    """
    query = request.GET.get("q", "")
    state = request.GET.get("show", "")
    group = request.GET.get("company", services.EVERYONE)
    companies = (
        corporate.companies_with_contracts(request.gym)
        if module_services.is_enabled(request.gym, CORPORATE_WELLNESS)
        else []
    )
    page, rows = services.people_page(
        request.gym,
        query=query,
        state=state,
        group=group if companies else services.INDIVIDUALS,
        page_number=request.GET.get("page"),
    )
    context = {
        "page": page,
        "rows": rows,
        "query": query,
        "state": state if state in member_services.MEMBER_STATES else "",
        "group": group,
        "companies": companies,
        "today": timezone.localdate(),
    }
    # When the search box asks (via HTMX), send back only the results part.
    if request.headers.get("HX-Request"):
        return render(request, "frontdesk/_people_results.html", context)
    return render(request, "frontdesk/people_list.html", context)


# --- Sign-ups waiting for payment -------------------------------------------


def _signup(request, public_id) -> SignUp:
    return get_object_or_404(
        SignUp.objects.for_gym(request.gym).select_related("package"), public_id=public_id
    )


@gym_required(*FRONT_DESK_ROLES)
@module_required(PAYMENTS_FINANCE)
def signup_accept(request, public_id):
    """
    Somebody signed up on their phone and has come to the desk to pay.

    One step does all of it: the member is registered, the package is sold and
    the money is recorded, so nothing is half done if the receptionist is
    called away.
    """
    signup = _signup(request, public_id)
    form = SignUpPaymentForm(request.POST or None)
    if signup.status != SignUpStatus.WAITING:
        messages.error(request, _("This sign-up has already been handled."))
        return redirect("frontdesk:desk")

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            member = member_services.register_from_signup(by=request.assignment, signup=signup)
            sale = billing.sell_package(
                by=request.assignment,
                member=member,
                package=signup.package,
                start_date=timezone.localdate(),
                idempotency_key=data["idempotency_key"],
                payment=billing.PaymentInput(
                    method=data["method"],
                    amount_rwf=None if data["pay_now"] == PayNow.FULL else data["amount_rwf"],
                ),
            )
        except member_services.AlreadyMember:
            messages.error(request, _("This phone is already a member here."))
            return redirect("frontdesk:desk")
        except member_services.InvalidDetails as problem:
            form.add_error(None, _("Check this value: %(field)s") % {"field": problem.field})
        else:
            messages.success(
                request,
                _("%(name)s joined and paid. %(package)s starts today.")
                % {"name": member.full_name, "package": sale.package_name},
            )
            if sale.payment is not None:
                return redirect("billing:receipt", public_id=sale.payment.public_id)
            return redirect("members:member_detail", public_id=member.public_id)

    return render(request, "frontdesk/signup_accept.html", {"signup": signup, "form": form})


@gym_required(*FRONT_DESK_ROLES)
@require_POST
def signup_drop(request, public_id):
    member_services.drop_signup(by=request.assignment, signup=_signup(request, public_id))
    messages.success(request, _("Taken off the list."))
    return redirect("frontdesk:desk")
