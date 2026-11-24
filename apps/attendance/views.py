"""Screens for checking members in. They call services.py and show the result."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.attendance import services
from apps.gyms import session
from apps.gyms.models import Branch
from apps.gyms.permissions import CHECK_IN_ROLES, FRONT_DESK_ROLES
from apps.gyms.tenancy import gym_required
from apps.members.models import Member
from apps.modules.catalog import ATTENDANCE_ACCESS
from apps.modules.decorators import module_required

# Where the desk's chosen branch is remembered while the person stays signed in.


def _branches(request):
    return Branch.objects.for_gym(request.gym).filter(is_active=True)


def _desk_branch(request) -> Branch | None:
    """The branch this desk is at, or None if it still has to be chosen."""
    return session.desk_branch(request)


@gym_required(*CHECK_IN_ROLES)
@module_required(ATTENDANCE_ACCESS)
def home(request):
    query = request.GET.get("q", "")
    context = {
        "query": query,
        "results": services.find_for_check_in(request.gym, query),
        "searched": len(query.strip()) >= services.MIN_SEARCH_LENGTH,
        # The new desk shows these results inside itself and wants them back there.
        "next": request.GET.get("next", ""),
    }
    # The search box asks for the results on their own (HTMX).
    if request.headers.get("HX-Request"):
        return render(request, "attendance/_results.html", context)

    today = timezone.localdate()
    check_ins = services.check_ins_on(request.gym, today)
    branches = list(_branches(request))
    context |= {
        "today": today,
        "check_ins": check_ins[:50],
        "check_in_count": check_ins.count(),
        "desk_branch": _desk_branch(request),
        "branches": branches,
        "can_change_branch": len(branches) > 1 and request.assignment.branch_id is None,
    }
    return render(request, "attendance/home.html", context)


def _back(request) -> str:
    """Where to go after an action: the page that asked (the desk), if it is ours."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse("attendance:home")


@gym_required(*CHECK_IN_ROLES)
@module_required(ATTENDANCE_ACCESS)
@require_POST
def set_branch(request):
    """Remember which branch this desk is at."""
    branch = get_object_or_404(_branches(request), public_id=request.POST.get("branch") or None)
    request.session[session.DESK_BRANCH] = str(branch.public_id)
    return redirect(_back(request))


@gym_required(*CHECK_IN_ROLES)
@module_required(ATTENDANCE_ACCESS)
@require_POST
def check_in(request, public_id):
    member = get_object_or_404(Member.objects.for_gym(request.gym), public_id=public_id)
    try:
        record, is_new = services.check_in(
            by=request.assignment, member=member, branch=_desk_branch(request)
        )
    except services.NotAllowedIn:
        messages.error(
            request,
            _("%(name)s was NOT checked in: no membership valid today.")
            % {"name": member.full_name},
        )
    except services.BranchNeeded:
        messages.error(request, _("Choose which branch this desk is at first."))
    else:
        time = timezone.localtime(record.checked_in_at).strftime("%H:%M")
        if is_new:
            text = _("%(name)s checked in at %(time)s.")
        else:
            text = _("%(name)s was already checked in today at %(time)s.")
        messages.success(request, text % {"name": member.full_name, "time": time})
    return redirect(_back(request))


@gym_required(*FRONT_DESK_ROLES)
@module_required(ATTENDANCE_ACCESS)
def member_visits(request, public_id):
    member = get_object_or_404(Member.objects.for_gym(request.gym), public_id=public_id)
    today = timezone.localdate()
    visits = services.visits_for(member)
    context = {
        "member": member,
        "visits": visits[:10],
        "visits_this_month": visits.filter(
            checked_in_on__year=today.year, checked_in_on__month=today.month
        ).count(),
        "total_visits": visits.count(),
    }
    # The member's page asks for this part on its own (via HTMX) and places it inline.
    if request.headers.get("HX-Request"):
        return render(request, "attendance/_member_visits.html", context)
    return render(request, "attendance/member_visits.html", context)
