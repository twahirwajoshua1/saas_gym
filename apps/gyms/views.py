"""Gym screens. They call services and the tenancy helper; no rules live here."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit import services as audit_services
from apps.gyms import services, tenancy
from apps.gyms.models import Branch, Role


@login_required
def enter(request):
    """After sign-in: go straight into your gym, or choose one if you have several."""
    assignments = list(services.active_assignments_for(request.user.person))
    if len(assignments) == 1:
        tenancy.select_gym(request, assignments[0])
        return redirect("gyms:home")
    return render(request, "gyms/enter.html", {"assignments": assignments})


@login_required
@require_POST
def choose(request):
    """Switch to one of the gyms you work at."""
    assignment = services.find_active_assignment(
        person=request.user.person, gym_public_id=request.POST.get("gym")
    )
    if assignment is None:
        raise PermissionDenied("You do not work at that gym.")
    tenancy.select_gym(request, assignment)
    return redirect("gyms:home")


@tenancy.gym_required()
def home(request):
    return render(
        request,
        "gyms/home.html",
        {
            "can_manage": request.assignment.role in (Role.OWNER, Role.BRANCH_MANAGER),
            "is_owner": request.assignment.role == Role.OWNER,
            "other_gyms": services.active_assignments_for(request.user.person).count() > 1,
        },
    )


@tenancy.gym_required(Role.OWNER, Role.BRANCH_MANAGER)
def branch_list(request):
    branches = Branch.objects.for_gym(request.gym)
    return render(request, "gyms/branch_list.html", {"branches": branches})


@tenancy.gym_required(Role.OWNER, Role.BRANCH_MANAGER)
def branch_detail(request, public_id):
    # Looking the branch up inside the current gym means another gym's branch
    # is simply "not found", even if someone guesses its address.
    branch = get_object_or_404(Branch.objects.for_gym(request.gym), public_id=public_id)
    return render(request, "gyms/branch_detail.html", {"branch": branch})


@tenancy.gym_required(Role.OWNER)
def activity(request):
    """The permanent record of what has happened at this gym, newest first."""
    entries = audit_services.entries_for(request.gym)
    return render(request, "gyms/activity.html", {"entries": entries})
