"""The owner's figures and the renewals list. Read only."""

from django.shortcuts import render
from django.utils import timezone

from apps.dashboard import services
from apps.dashboard.services import RenewalView
from apps.gyms.permissions import FRONT_DESK_ROLES, MANAGER_ROLES
from apps.gyms.tenancy import gym_required


@gym_required(*MANAGER_ROLES)
def figures(request):
    context = {"figures": services.overview(request.gym, timezone.localdate())}
    # The Overview page asks for this part on its own (via HTMX) and places it inline.
    if request.headers.get("HX-Request"):
        return render(request, "dashboard/_figures.html", context)
    return render(request, "dashboard/figures.html", context)


@gym_required(*FRONT_DESK_ROLES)
def renewals(request):
    view = request.GET.get("show", RenewalView.SOON)
    if view not in (RenewalView.SOON, RenewalView.LATER, RenewalView.LAPSED):
        view = RenewalView.SOON
    today = timezone.localdate()
    return render(
        request,
        "dashboard/renewals.html",
        {"view": view, "today": today, "memberships": services.renewals(request.gym, today, view)},
    )
