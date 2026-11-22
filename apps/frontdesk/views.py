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

from apps.attendance import services as attendance
from apps.attendance.views import SESSION_BRANCH
from apps.billing import services as billing
from apps.corporate import services as corporate
from apps.corporate.models import Employee
from apps.frontdesk import services
from apps.frontdesk.models import EntranceTablet, ScanProblem
from apps.gyms.models import Branch
from apps.gyms.permissions import CHECK_IN_ROLES, FRONT_DESK_ROLES, MANAGER_ROLES
from apps.gyms.tenancy import gym_required
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, CORPORATE_WELLNESS, PAYMENTS_FINANCE
from apps.modules.decorators import module_required

COOKIE = "gymos_tablet"
COOKIE_AGE = 3 * 365 * 24 * 3600


def _desk_branch(request) -> Branch | None:
    chosen = request.session.get(SESSION_BRANCH)
    branch = None
    if chosen:
        branch = Branch.objects.for_gym(request.gym).filter(public_id=chosen).first()
    try:
        return attendance.desk_branch(request.assignment, branch)
    except attendance.BranchNeeded:
        return None


# --- The Desk ---------------------------------------------------------------------


@gym_required(*CHECK_IN_ROLES)
@module_required(ATTENDANCE_ACCESS)
def desk(request):
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
