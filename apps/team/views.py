"""Screens for managing the gym's staff. Owner only."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.gyms.models import StaffAssignment
from apps.gyms.permissions import OWNER_ONLY
from apps.gyms.tenancy import gym_required
from apps.team import services
from apps.team.forms import StaffForm


def _message(reason: str) -> str:
    return {
        "role_invalid": _("Choose a role."),
        "branch_invalid": _("Choose one of this gym's branches."),
        "phone_invalid": _("Enter a valid phone number, for example 0788 123 456."),
        "name_required": _("Enter the person's name."),
        "already_staff": _("This person already works at this gym."),
        "yourself": _("You cannot change or remove your own access."),
        "not_active": _("This person no longer works here. Add them again instead."),
        "last_owner": _("The gym must keep at least one owner. Make someone else owner first."),
        "cannot_reset": _(
            "This password cannot be reset here, because the person also works at another "
            "gym or runs the platform. Ask them to contact platform support."
        ),
    }.get(reason, _("This cannot be done."))


def _get_assignment(request, public_id) -> StaffAssignment:
    assignments = StaffAssignment.objects.for_gym(request.gym).select_related("person", "branch")
    return get_object_or_404(assignments, person__public_id=public_id)


@gym_required(*OWNER_ONLY)
def staff_list(request):
    rows = services.staff_of(request.gym)
    for row in rows:
        row.can_reset = services.can_reset_password(request.assignment, row.assignment)
    return render(request, "team/staff_list.html", {"rows": rows})


@gym_required(*OWNER_ONLY)
def staff_add(request):
    form = StaffForm(request.POST or None, gym=request.gym)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            added = services.add_staff(
                by=request.assignment,
                full_name=data["full_name"],
                phone=data["phone"],
                role=data["role"],
                branch=data.get("branch"),
            )
        except services.StaffProblem as problem:
            form.add_error(None, _message(problem.reason))
        else:
            # Shown once, now. It is not stored anywhere readable.
            return render(request, "team/staff_added.html", {"added": added})
    return render(request, "team/staff_form.html", {"form": form})


@gym_required(*OWNER_ONLY)
def staff_edit(request, public_id):
    assignment = _get_assignment(request, public_id)
    initial = {
        "role": assignment.role,
        "branch": assignment.branch.public_id if assignment.branch else None,
    }
    form = StaffForm(request.POST or None, gym=request.gym, editing=True, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.change_staff(
                by=request.assignment,
                assignment=assignment,
                role=form.cleaned_data["role"],
                branch=form.cleaned_data.get("branch"),
            )
        except services.StaffProblem as problem:
            form.add_error(None, _message(problem.reason))
        else:
            messages.success(request, _("Changes saved."))
            return redirect("team:staff_list")
    return render(request, "team/staff_form.html", {"form": form, "assignment": assignment})


@gym_required(*OWNER_ONLY)
@require_POST
def staff_remove(request, public_id):
    assignment = _get_assignment(request, public_id)
    try:
        services.remove_staff(by=request.assignment, assignment=assignment)
    except services.StaffProblem as problem:
        messages.error(request, _message(problem.reason))
    else:
        messages.success(
            request,
            _("%(name)s no longer has access to this gym.") % {"name": assignment.person.full_name},
        )
    return redirect("team:staff_list")


@gym_required(*OWNER_ONLY)
@require_POST
def staff_reset_password(request, public_id):
    assignment = _get_assignment(request, public_id)
    try:
        password = services.reset_password(by=request.assignment, assignment=assignment)
    except services.StaffProblem as problem:
        messages.error(request, _message(problem.reason))
        return redirect("team:staff_list")
    return render(
        request, "team/password_reset.html", {"assignment": assignment, "password": password}
    )
