"""Screens for platform staff: gyms, their owners and their applications."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEntry
from apps.gyms.models import Branch, Gym
from apps.modules import services as modules
from apps.platformadmin import services

MESSAGES = {
    "phone_invalid": "Enter a valid phone number for the owner.",
    "owner_name_required": "Enter the owner's name.",
    "gym_name_required": "Enter the gym's name.",
    "reason_required": "Give a reason for suspending the gym.",
}


def platform_staff_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied("Only platform staff may open this page.")
        return view(request, *args, **kwargs)

    return wrapped


@platform_staff_required
def gym_list(request):
    return render(request, "platformadmin/gym_list.html", {"rows": services.gyms_with_counts()})


@platform_staff_required
def gym_create(request):
    values = {"gym_name": "", "owner_name": "", "owner_phone": ""}
    error = ""
    if request.method == "POST":
        values = {key: request.POST.get(key, "") for key in values}
        try:
            created = services.register_gym(user=request.user, **values)
        except services.PlatformProblem as problem:
            error = MESSAGES.get(problem.reason, "This cannot be done.")
        else:
            return render(request, "platformadmin/gym_created.html", {"created": created})
    return render(request, "platformadmin/gym_form.html", {"values": values, "error": error})


@platform_staff_required
def gym_detail(request, public_id):
    gym = get_object_or_404(Gym, public_id=public_id)
    return render(
        request,
        "platformadmin/gym_detail.html",
        {
            "gym": gym,
            "statuses": modules.module_status(gym),
            "branches": Branch.objects.for_gym(gym),
            "recent": AuditEntry.objects.for_gym(gym).filter(action__startswith="module.")[:10],
        },
    )


@platform_staff_required
@require_POST
def gym_module(request, public_id):
    gym = get_object_or_404(Gym, public_id=public_id)
    try:
        services.set_module(
            user=request.user,
            gym=gym,
            code=request.POST.get("module", ""),
            on=request.POST.get("on") == "yes",
        )
    except modules.ModuleError as problem:
        messages.error(request, str(problem))
    else:
        messages.success(request, "Saved.")
    return redirect("platformadmin:gym_detail", public_id=gym.public_id)


@platform_staff_required
@require_POST
def gym_active(request, public_id):
    gym = get_object_or_404(Gym, public_id=public_id)
    try:
        services.set_gym_active(
            user=request.user,
            gym=gym,
            active=request.POST.get("active") == "yes",
            reason=request.POST.get("reason", ""),
        )
    except services.PlatformProblem as problem:
        messages.error(request, MESSAGES.get(problem.reason, "This cannot be done."))
    else:
        messages.success(request, "Saved.")
    return redirect("platformadmin:gym_detail", public_id=gym.public_id)
