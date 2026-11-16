"""
Screens for members and packages.

Each screen collects input, calls a function in services.py and shows the
result. The words shown to people are chosen here; services only say what went
wrong (for example InvalidDetails("phone", "invalid")).
"""

from dataclasses import replace

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.gyms.permissions import FRONT_DESK_ROLES, OWNER_ONLY
from apps.gyms.tenancy import gym_required
from apps.members import services
from apps.members.forms import MemberForm, PackageForm
from apps.members.models import Member, Package

PAGE_SIZE = 25


def _error_message(error: services.InvalidDetails) -> str:
    known = {
        ("name", "required"): _("Enter a name."),
        ("name", "duplicate"): _("Another package already has this name."),
        ("price_rwf", "out_of_range"): _("Enter a price above zero."),
        ("duration_count", "out_of_range"): _("Enter how long it lasts, from 1 to 999."),
        ("full_name", "required"): _("Enter the member's name."),
        ("phone", "invalid"): _("Enter a valid phone number, for example 0788 123 456."),
        ("branch", "required"): _("Choose a branch."),
        ("branch", "invalid"): _("Choose one of this gym's branches."),
        ("date_of_birth", "in_future"): _("The date of birth cannot be in the future."),
        ("emergency_contact_phone", "invalid"): _(
            "Enter a valid phone number, or leave this empty."
        ),
    }
    return known.get((error.field, error.reason), _("Check this value."))


def _show_error(form, error: services.InvalidDetails) -> None:
    field = error.field if error.field in form.fields else None
    form.add_error(field, _error_message(error))


# --- Members ----------------------------------------------------------------


@gym_required(*FRONT_DESK_ROLES)
def member_list(request):
    query = request.GET.get("q", "")
    members = services.search_members(request.gym, query)
    page = Paginator(members, PAGE_SIZE).get_page(request.GET.get("page"))
    context = {"page": page, "query": query}

    # When the search box asks (via HTMX), send back only the results part.
    if request.headers.get("HX-Request"):
        return render(request, "members/_member_results.html", context)
    return render(request, "members/member_list.html", context)


def _member_details(form) -> services.MemberDetails:
    data = form.cleaned_data
    return services.MemberDetails(
        full_name=data["full_name"],
        branch=data.get("branch"),
        gender=data["gender"],
        date_of_birth=data["date_of_birth"],
        emergency_contact_name=data["emergency_contact_name"],
        emergency_contact_phone=data["emergency_contact_phone"],
        notes=data["notes"],
    )


@gym_required(*FRONT_DESK_ROLES)
def member_register(request):
    form = MemberForm(request.POST or None, gym=request.gym)
    existing_member = None
    if request.method == "POST" and form.is_valid():
        try:
            member = services.register_member(
                by=request.assignment,
                phone=form.cleaned_data["phone"],
                details=_member_details(form),
            )
        except services.AlreadyMember as error:
            existing_member = error.member
            form.add_error("phone", _("This phone number already belongs to a member."))
        except services.InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(request, _("%(name)s was registered.") % {"name": member.full_name})
            return redirect("members:member_detail", public_id=member.public_id)

    return render(
        request, "members/member_form.html", {"form": form, "existing_member": existing_member}
    )


def _get_member(request, public_id) -> Member:
    members = Member.objects.for_gym(request.gym).select_related("person", "branch")
    return get_object_or_404(members, public_id=public_id)


@gym_required(*FRONT_DESK_ROLES)
def member_detail(request, public_id):
    return render(
        request, "members/member_detail.html", {"member": _get_member(request, public_id)}
    )


@gym_required(*FRONT_DESK_ROLES)
def member_edit(request, public_id):
    member = _get_member(request, public_id)
    initial = {
        "full_name": member.full_name,
        "branch": member.branch.public_id,
        "gender": member.gender,
        "date_of_birth": member.date_of_birth,
        "emergency_contact_name": member.emergency_contact_name,
        "emergency_contact_phone": member.emergency_contact_phone,
        "notes": member.notes,
    }
    form = MemberForm(request.POST or None, gym=request.gym, editing=True, initial=initial)
    if request.method == "POST" and form.is_valid():
        details = _member_details(form)
        if form.single_branch:
            details = replace(details, branch=member.branch)
        try:
            services.update_member(by=request.assignment, member=member, details=details)
        except services.InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(request, _("Changes saved."))
            return redirect("members:member_detail", public_id=member.public_id)

    return render(request, "members/member_form.html", {"form": form, "member": member})


# --- Packages ---------------------------------------------------------------


@gym_required(*FRONT_DESK_ROLES)
def package_list(request):
    packages = Package.objects.for_gym(request.gym)
    return render(
        request,
        "members/package_list.html",
        {
            "active_packages": [p for p in packages if p.is_active],
            "archived_packages": [p for p in packages if not p.is_active],
        },
    )


@gym_required(*OWNER_ONLY)
def package_create(request):
    form = PackageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            package = services.create_package(by=request.assignment, **form.cleaned_data)
        except services.InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(request, _("%(name)s was added.") % {"name": package.name})
            return redirect("members:package_list")
    return render(request, "members/package_form.html", {"form": form})


def _get_package(request, public_id) -> Package:
    return get_object_or_404(Package.objects.for_gym(request.gym), public_id=public_id)


@gym_required(*OWNER_ONLY)
def package_edit(request, public_id):
    package = _get_package(request, public_id)
    initial = {
        "name": package.name,
        "price_rwf": package.price_rwf,
        "duration_count": package.duration_count,
        "duration_unit": package.duration_unit,
    }
    form = PackageForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_package(by=request.assignment, package=package, **form.cleaned_data)
        except services.InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(request, _("Changes saved."))
            return redirect("members:package_list")
    return render(request, "members/package_form.html", {"form": form, "package": package})


@gym_required(*OWNER_ONLY)
@require_POST
def package_set_active(request, public_id):
    package = _get_package(request, public_id)
    active = request.POST.get("active") == "yes"
    services.set_package_active(by=request.assignment, package=package, active=active)
    if active:
        messages.success(request, _("%(name)s can be sold again.") % {"name": package.name})
    else:
        messages.success(request, _("%(name)s was archived.") % {"name": package.name})
    return redirect("members:package_list")
