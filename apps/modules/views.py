"""The applications page: what this gym has, and asking for what it has not."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.gyms.models import Role
from apps.gyms.tenancy import gym_required
from apps.modules import services


@gym_required(Role.OWNER, Role.BRANCH_MANAGER)
def module_list(request):
    asked = services.requested_modules(request.gym)
    statuses = services.module_status(request.gym)
    for status in statuses:
        status["asked"] = asked.get(status["module"].code)
    return render(request, "modules/module_list.html", {"statuses": statuses})


@gym_required(Role.OWNER, Role.BRANCH_MANAGER)
@require_POST
def ask(request, code):
    """
    Put this gym on the list for an application it does not have.

    Nothing is switched on here. Only the platform side does that, and it now
    has a list of who is waiting instead of a phone that has to ring.
    """
    try:
        if request.POST.get("withdraw"):
            services.withdraw_request(by=request.assignment, code=code)
            messages.success(request, _("Taken off the list."))
        else:
            services.ask_for_module(by=request.assignment, code=code)
            messages.success(request, _("Asked for. We will be in touch about switching it on."))
    except services.ModuleError as problem:
        messages.error(request, str(problem))
    return redirect("modules:list")
