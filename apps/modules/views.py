"""A read-only page showing which applications the gym has."""

from django.shortcuts import render

from apps.gyms.models import Role
from apps.gyms.tenancy import gym_required
from apps.modules import services


@gym_required(Role.OWNER, Role.BRANCH_MANAGER)
def module_list(request):
    return render(
        request, "modules/module_list.html", {"statuses": services.module_status(request.gym)}
    )
