"""
Massage screens.

The desk sees the diary and answers requests; the owner sets the types and the
rules; a masseuse sees her own day. A masseuse's note about somebody is hers
alone, so it is never put in a page anybody else can open.
"""

from datetime import date, datetime, timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.classes.models import TrainerProfile
from apps.corporate import services as corporate
from apps.corporate.models import Employee
from apps.gyms import session
from apps.gyms.models import Branch
from apps.gyms.permissions import CHECK_IN_ROLES, FRONT_DESK_ROLES, OWNER_ONLY
from apps.gyms.tenancy import gym_required
from apps.massage import services
from apps.massage.forms import MassageRuleForm, MassageTypeForm, NoteForm, ReasonForm
from apps.massage.models import MassageBooking, MassageType
from apps.members import services as member_services
from apps.members.models import Member
from apps.members.services import InvalidDetails
from apps.modules.catalog import TRAINING_CLASSES
from apps.modules.decorators import module_required

ERRORS = {
    ("name", "required"): _("Enter a name."),
    ("name", "duplicate"): _("Another massage already has this name."),
    ("minutes", "out_of_range"): _("Enter a length between 5 and 240 minutes."),
    ("price_rwf", "out_of_range"): _("Enter a price above zero."),
    ("starts_at", "taken"): _("That time has gone. Pick one of the free times."),
    ("held_on", "out_of_range"): _("Pick a day from today onwards."),
    ("massage_type", "not_offered"): _("This massage is no longer offered."),
    ("masseuse", "does_not_give_massages"): _("This person does not give massages."),
    ("booking", "already_decided"): _("This one has already been answered."),
    ("booking", "not_booked"): _("Only a booked massage can be marked done."),
    ("reason", "required"): _("Give a reason."),
    ("cut_off_minutes", "out_of_range"): _("Enter a number of minutes up to a day."),
    ("gap_minutes", "out_of_range"): _("Enter a break of up to two hours."),
    ("reminder_minutes", "out_of_range"): _("Enter up to two hours."),
}


def error_text(problem: InvalidDetails) -> str:
    return ERRORS.get((problem.field, problem.reason), _("Check this value."))


def massage_page(*roles):
    def decorator(view):
        return gym_required(*roles)(module_required(TRAINING_CLASSES)(view))

    return decorator


def _branch(request) -> Branch | None:
    return session.desk_branch(request)


def _day(request, name: str = "day") -> date:
    return parse_date(request.GET.get(name, "")) or timezone.localdate()


def _days_ahead(start: date, count: int = 6) -> list[date]:
    return [start + timedelta(days=step) for step in range(count)]


# --- The desk's diary --------------------------------------------------------


@massage_page(*CHECK_IN_ROLES)
def diary(request):
    branch = _branch(request)
    today = timezone.localdate()
    day = _day(request)
    bookings = list(services.diary(request.gym, branch=branch, day=day)) if branch else []
    masseuses = list(services.masseuses_for(request.gym, branch)) if branch else []

    # One row per time, one column per masseuse: what the desk reads at a glance.
    times = sorted({booking.starts_at for booking in bookings})
    grid = [
        {
            "at": at,
            "cells": [
                next(
                    (
                        booking
                        for booking in bookings
                        if booking.starts_at == at and booking.masseuse_id == masseuse.pk
                    ),
                    None,
                )
                for masseuse in masseuses
            ],
        }
        for at in times
    ]
    return render(
        request,
        "massage/diary.html",
        {
            "branch": branch,
            "day": day,
            "days": _days_ahead(today),
            "today": today,
            "masseuses": masseuses,
            "grid": grid,
            "bookings": bookings,
            "waiting": services.waiting_for_the_gym(request.gym, branch),
            "types": services.types_for(request.gym),
        },
    )


# --- Booking somebody in -----------------------------------------------------


@massage_page(*FRONT_DESK_ROLES)
def find_person(request):
    """Who is the massage for? The gym's own members and its companies' staff."""
    query = request.GET.get("q", "")
    members = []
    staff = []
    if query.strip():
        members = list(member_services.search_members(request.gym, query)[:10])
        staff = list(corporate.search_employees(request.gym, query)[:10])
    return render(
        request,
        "massage/find_person.html",
        {"query": query, "members": members, "staff": staff},
    )


def _who(request):
    """The person the booking is for, from the query string."""
    member_id = request.GET.get("member") or request.POST.get("member")
    employee_id = request.GET.get("employee") or request.POST.get("employee")
    if member_id:
        member = get_object_or_404(
            Member.objects.for_gym(request.gym).select_related("person"), public_id=member_id
        )
        return member.person, member, None
    if employee_id:
        companies = corporate.companies_with_contracts(request.gym)
        employee = get_object_or_404(
            Employee.objects.filter(company__in=companies).select_related("person", "company"),
            public_id=employee_id,
        )
        return employee.person, None, employee
    raise Http404


@massage_page(*FRONT_DESK_ROLES)
def book(request):
    person, member, employee = _who(request)
    branch = _branch(request)
    today = timezone.localdate()
    day = _day(request)
    types = list(services.types_for(request.gym))
    chosen_type = next(
        (t for t in types if str(t.public_id) == request.GET.get("type", "")),
        types[0] if types else None,
    )
    masseuses = list(services.masseuses_for(request.gym, branch)) if branch else []
    chosen_masseuse = next(
        (m for m in masseuses if str(m.public_id) == request.GET.get("masseuse", "")), None
    )
    slots = (
        services.free_slots(
            request.gym,
            branch=branch,
            massage_type=chosen_type,
            day=day,
            masseuse=chosen_masseuse,
        )
        if branch and chosen_type
        else []
    )

    if request.method == "POST" and chosen_type is not None:
        at = parse_time(request.POST.get("at", ""))
        masseuse = chosen_masseuse or next((slot.masseuse for slot in slots if slot.at == at), None)
        try:
            if at is None or masseuse is None:
                raise InvalidDetails("starts_at", "taken")
            booking = services.book(
                by=request.assignment,
                massage_type=chosen_type,
                masseuse=masseuse,
                branch=branch,
                day=day,
                at=at,
                person=person,
                member=member,
                employee=employee,
            )
        except InvalidDetails as problem:
            messages.error(request, error_text(problem))
        else:
            messages.success(
                request,
                _("%(what)s booked for %(who)s on %(day)s at %(time)s.")
                % {
                    "what": booking.type_name,
                    "who": person.full_name,
                    "day": f"{booking.held_on:%A %d %B}",
                    "time": f"{booking.starts_at:%H:%M}",
                },
            )
            return redirect(f"{request.path.rsplit('/book', 1)[0]}/?day={day:%Y-%m-%d}")

    wanted = parse_time(request.GET.get("at", "") or "")
    return render(
        request,
        "massage/book.html",
        {
            "person": person,
            "member": member,
            "employee": employee,
            "branch": branch,
            "day": day,
            "days": _days_ahead(today),
            "types": types,
            "chosen_type": chosen_type,
            "masseuses": masseuses,
            "chosen_masseuse": chosen_masseuse,
            "slots": slots,
            "nearest": (
                services.nearest_free(
                    request.gym,
                    branch=branch,
                    massage_type=chosen_type,
                    day=day,
                    wanted=wanted,
                )
                if wanted and branch and chosen_type
                else []
            ),
            "wanted": wanted,
            "rules": services.rules_for(request.gym),
        },
    )


def _booking(request, public_id) -> MassageBooking:
    return get_object_or_404(
        MassageBooking.objects.for_gym(request.gym).select_related(
            "person", "masseuse__staff__person", "branch", "massage_type"
        ),
        public_id=public_id,
    )


@massage_page(*FRONT_DESK_ROLES)
@require_POST
def answer(request, public_id):
    """Accept or decline a request from the desk."""
    booking = _booking(request, public_id)
    wanted = request.POST.get("decision")
    try:
        if wanted == "accept":
            services.accept(by=request.assignment, booking=booking)
            messages.success(request, _("Booked. The slot is now held."))
        else:
            services.decline(
                by=request.assignment,
                booking=booking,
                reason=request.POST.get("reason") or _("No free time"),
            )
            messages.success(request, _("Declined. Offer them another time."))
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    return redirect(f"{_diary_url()}?day={booking.held_on:%Y-%m-%d}")


def _diary_url() -> str:
    from django.urls import reverse

    return reverse("massage:diary")


@massage_page(*FRONT_DESK_ROLES)
def cancel(request, public_id):
    booking = _booking(request, public_id)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            done = services.cancel(
                by=request.assignment, booking=booking, reason=form.cleaned_data["reason"]
            )
        except InvalidDetails as problem:
            messages.error(request, error_text(problem))
        else:
            if done.charged_anyway:
                messages.success(
                    request,
                    _("Called off. It is past the cut-off, so it is still charged in full."),
                )
            else:
                messages.success(request, _("Called off. Nothing is charged."))
            return redirect(f"{_diary_url()}?day={booking.held_on:%Y-%m-%d}")
    return render(
        request,
        "massage/cancel.html",
        {"booking": booking, "form": form, "too_late": services.too_late_to_cancel(booking)},
    )


@massage_page(*CHECK_IN_ROLES)
@require_POST
def mark_done(request, public_id):
    booking = _booking(request, public_id)
    try:
        services.mark_done(by=request.assignment, booking=booking)
    except InvalidDetails as problem:
        messages.error(request, error_text(problem))
    else:
        messages.success(request, _("Marked as done."))
    return redirect(request.POST.get("next") or _diary_url())


# --- The masseuse's own page -------------------------------------------------


@massage_page(*CHECK_IN_ROLES)
def my_day(request):
    """
    What a masseuse sees: her next massage, the rest of today, and her hours.
    Her notes are on this page and nowhere else.
    """
    profile = TrainerProfile.objects.for_gym(request.gym).filter(staff=request.assignment).first()
    if profile is None:
        raise Http404
    day = _day(request)
    bookings = list(services.day_for(profile, day))
    now = timezone.localtime()
    nexts = [
        booking
        for booking in bookings
        if day > now.date() or datetime.combine(day, booking.starts_at) >= now.replace(tzinfo=None)
    ]
    since = timezone.localdate().replace(day=1)
    return render(
        request,
        "massage/my_day.html",
        {
            "profile": profile,
            "day": day,
            "today": timezone.localdate(),
            "bookings": bookings,
            "next_one": nexts[0] if nexts else None,
            "hours": profile.hours.select_related("branch").order_by("weekday", "starts_at"),
            "done_this_month": services.count_for(request.gym, profile, since=since),
            "note_form": NoteForm(),
        },
    )


@massage_page(*CHECK_IN_ROLES)
@require_POST
def note(request, public_id):
    booking = _booking(request, public_id)
    form = NoteForm(request.POST)
    if form.is_valid():
        services.write_note(by=request.assignment, booking=booking, note=form.cleaned_data["note"])
        messages.success(request, _("Saved. Only you can see it."))
    return redirect(f"{_my_day_url()}?day={booking.held_on:%Y-%m-%d}")


def _my_day_url() -> str:
    from django.urls import reverse

    return reverse("massage:my_day")


# --- Settings (owner) --------------------------------------------------------


@massage_page(*OWNER_ONLY)
def type_list(request):
    return render(
        request,
        "massage/type_list.html",
        {
            "types": services.types_for(request.gym, active_only=False),
            "rules": services.rules_for(request.gym),
            "masseuses": services.masseuses_for(request.gym),
        },
    )


@massage_page(*OWNER_ONLY)
def type_new(request):
    form = MassageTypeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_type(by=request.assignment, **form.cleaned_data)
        except InvalidDetails as problem:
            form.add_error(
                problem.field if problem.field in form.fields else None, error_text(problem)
            )
        else:
            messages.success(request, _("Added."))
            return redirect("massage:type_list")
    return render(request, "massage/type_form.html", {"form": form})


@massage_page(*OWNER_ONLY)
def type_edit(request, public_id):
    massage_type = get_object_or_404(
        MassageType.objects.for_gym(request.gym).select_related("service"), public_id=public_id
    )
    form = MassageTypeForm(
        request.POST or None,
        initial={
            "name": massage_type.name,
            "minutes": massage_type.minutes,
            "price_rwf": massage_type.price_rwf,
            "what_to_expect": massage_type.what_to_expect,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.update_type(
                by=request.assignment, massage_type=massage_type, **form.cleaned_data
            )
        except InvalidDetails as problem:
            form.add_error(
                problem.field if problem.field in form.fields else None, error_text(problem)
            )
        else:
            messages.success(request, _("Changes saved."))
            return redirect("massage:type_list")
    return render(request, "massage/type_form.html", {"form": form, "massage_type": massage_type})


@massage_page(*OWNER_ONLY)
@require_POST
def type_set_active(request, public_id):
    massage_type = get_object_or_404(MassageType.objects.for_gym(request.gym), public_id=public_id)
    services.set_type_active(
        by=request.assignment,
        massage_type=massage_type,
        active=request.POST.get("active") == "yes",
    )
    return redirect("massage:type_list")


@massage_page(*OWNER_ONLY)
def rules(request):
    current = services.rules_for(request.gym)
    form = MassageRuleForm(
        request.POST or None,
        initial={
            "cut_off_minutes": current.cut_off_minutes,
            "gap_minutes": current.gap_minutes,
            "reminder_minutes": current.reminder_minutes,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.set_rules(by=request.assignment, **form.cleaned_data)
        except InvalidDetails as problem:
            form.add_error(
                problem.field if problem.field in form.fields else None, error_text(problem)
            )
        else:
            messages.success(request, _("Saved."))
            return redirect("massage:type_list")
    return render(request, "massage/rules.html", {"form": form})
