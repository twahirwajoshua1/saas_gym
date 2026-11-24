"""
The member's card page and the card API, opened without signing in, and the
staff panel for sharing a member's card link.

Opening a card: the token in the link is checked with the database's narrow
card lookup (see members.services.gym_id_for_card). Only then is the database
told which gym this request acts for, exactly as signing in does for staff.
"""

from datetime import timedelta
from urllib.parse import quote

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.attendance import services as attendance
from apps.classes import services as classes
from apps.classes.models import ClassSession, PersonalSession, TrainerProfile
from apps.core import streaks, tenant_db
from apps.gyms.models import Branch
from apps.gyms.permissions import FRONT_DESK_ROLES
from apps.gyms.tenancy import gym_required
from apps.memberapp import services
from apps.members import services as members
from apps.members.models import Member
from apps.modules import services as module_services
from apps.modules.catalog import TRAINING_CLASSES

PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def _member_for_token(token: str) -> Member:
    gym_id = members.gym_id_for_card(token)
    if gym_id is None:
        raise Http404("No card with this link.")
    tenant_db.set_current_gym(gym_id)
    queryset = Member.objects.filter(gym_id=gym_id, card_token=token)
    return get_object_or_404(queryset.select_related("person", "gym"))


def _private(response):
    for header, value in PRIVATE_HEADERS.items():
        response[header] = value
    return response


def _waiting_for_a_review(member):
    """Classes the member came to in the last fortnight and has not reviewed."""
    if not module_services.is_enabled(member.gym, TRAINING_CLASSES):
        return []
    today = timezone.localdate()
    recent = (
        ClassSession.objects.for_gym(member.gym)
        .filter(
            bookings__member=member,
            bookings__cancelled_at__isnull=True,
            cancelled_at__isnull=True,
            held_on__range=(today - timedelta(days=14), today),
        )
        .select_related("class_type", "trainer__staff__person")
        .order_by("-held_on")[:5]
    )
    return [s for s in recent if classes.may_review(member, s)]


@require_GET
def card(request, token):
    member = _member_for_token(token)
    summary = services.card_summary(member)
    url = request.build_absolute_uri(reverse("memberapp:card", args=[token]))
    today = timezone.localdate()
    streak = streaks.compute(attendance.visit_days(member, today - timedelta(weeks=60)), today)
    month_visits = attendance.visits_for(member).filter(
        checked_in_on__year=today.year, checked_in_on__month=today.month
    )
    return _private(
        render(
            request,
            "memberapp/card.html",
            {
                "card": summary,
                "qr": services.qr_svg(url),
                "streak": streak,
                "month_visits": month_visits.count(),
                "recent": attendance.visits_for(member)[:5],
                "classes_on": module_services.is_enabled(member.gym, TRAINING_CLASSES),
                "my_classes": classes.bookings_for(member)[:5],
                "my_trainer_sessions": classes.personal_sessions_for(member)[:5],
                "to_review": _waiting_for_a_review(member),
            },
        )
    )


@require_GET
def card_api(request, token):
    """GET /api/v1/card/<token>/ : the card as JSON. The first API endpoint."""
    member = _member_for_token(token)
    data = services.card_as_data(services.card_summary(member))
    return _private(JsonResponse(data))


# --- For staff ---------------------------------------------------------------


def _staff_member(request, public_id) -> Member:
    return get_object_or_404(
        Member.objects.for_gym(request.gym).select_related("person"), public_id=public_id
    )


@gym_required(*FRONT_DESK_ROLES)
def card_panel(request, public_id):
    member = _staff_member(request, public_id)
    context = {"member": member}
    if member.card_token:
        url = request.build_absolute_uri(reverse("memberapp:card", args=[member.card_token]))
        message = _("Your %(gym)s membership card: %(url)s") % {"gym": request.gym.name, "url": url}
        digits = member.person.phone.lstrip("+")
        context |= {
            "card_url": url,
            "qr": services.qr_svg(url, scale=4),
            "whatsapp_url": f"https://wa.me/{digits}?text={quote(message)}",
            "sms_url": f"sms:{member.person.phone}?body={quote(message)}",
        }
    if request.headers.get("HX-Request"):
        return render(request, "memberapp/_card_panel.html", context)
    return render(request, "memberapp/card_panel.html", context)


@gym_required(*FRONT_DESK_ROLES)
@require_POST
def card_link(request, public_id):
    member = _staff_member(request, public_id)
    members.create_card_link(
        by=request.assignment, member=member, renew=request.POST.get("renew") == "yes"
    )
    return redirect("members:member_detail", public_id=member.public_id)


# --- Classes, from the member's own card ------------------------------------
#
# These pages are reached with the card link, so there is no login. The link
# itself is the key, exactly as for the card. Everything that changes data is
# a POST with a CSRF token, and the member can only ever act as themselves.


def _classes_or_404(member) -> None:
    if not module_services.is_enabled(member.gym, TRAINING_CLASSES):
        raise Http404("Classes are not switched on at this gym.")


def _chosen_branch(request, member, branches):
    """
    Which branch's classes to show. The one asked for, else the one the member
    last came to, else where they signed up. They may always switch.
    """
    asked = request.GET.get("branch")
    if asked:
        for branch in branches:
            if str(branch.public_id) == asked:
                return branch
    last = attendance.visits_for(member).first()
    if last is not None:
        return last.branch
    return member.branch


@require_GET
def card_classes(request, token):
    """The week's classes, with the member's own places marked."""
    member = _member_for_token(token)
    _classes_or_404(member)
    today = timezone.localdate()
    branches = list(Branch.objects.for_gym(member.gym).filter(is_active=True))
    chosen = _chosen_branch(request, member, branches)
    sessions = classes.timetable(member.gym, today, days=7, branch=chosen)
    mine = {b.session_id for b in classes.bookings_for(member)}
    days = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        rows = [
            {
                "session": s,
                "booked": s.id in mine,
                "left": max(0, s.places - s.taken),
                "past": classes.starts(s) <= timezone.now(),
            }
            for s in sessions
            if s.held_on == day and not s.is_cancelled
        ]
        if rows:
            days.append({"day": day, "rows": rows})
    return _private(
        render(
            request,
            "memberapp/classes.html",
            {
                "card": services.card_summary(member),
                "token": token,
                "days": days,
                "branches": branches,
                "branch": chosen,
            },
        )
    )


@require_POST
def card_book(request, token, public_id):
    """Take or give up a place. One button, so it cannot be done twice by mistake."""
    member = _member_for_token(token)
    _classes_or_404(member)
    session = get_object_or_404(
        ClassSession.objects.for_gym(member.gym).select_related("class_type"),
        public_id=public_id,
    )
    try:
        if request.POST.get("give_up"):
            classes.give_up_place(member=member, session=session)
            messages.success(request, _("Your place is given up."))
        else:
            classes.book(member=member, session=session)
            messages.success(
                request,
                _("You have a place in %(name)s.") % {"name": session.class_type.name},
            )
    except classes.ClassFull:
        messages.error(request, _("Sorry, that class is full."))
    except classes.TooLate:
        messages.error(request, _("It is too late to change this one."))
    return redirect("memberapp:card_classes", token=token)


@require_GET
def card_trainers(request, token):
    """The gym's trainers, with their rating and free hours."""
    member = _member_for_token(token)
    _classes_or_404(member)
    today = timezone.localdate()
    people = []
    for trainer in classes.list_trainers(member.gym):
        average, count = classes.rating(trainer)
        people.append(
            {
                "trainer": trainer,
                "rating": average,
                "count": count,
                "slots": classes.free_slots(trainer, today)[:8]
                if trainer.takes_personal_sessions
                else [],
            }
        )
    return _private(
        render(
            request,
            "memberapp/trainers.html",
            {
                "card": services.card_summary(member),
                "token": token,
                "people": people,
                "mine": classes.personal_sessions_for(member),
            },
        )
    )


@require_POST
def card_book_trainer(request, token, public_id):
    """Book one hour with a chosen trainer."""
    member = _member_for_token(token)
    _classes_or_404(member)
    trainer = get_object_or_404(TrainerProfile.objects.for_gym(member.gym), public_id=public_id)
    slot = request.POST.get("slot", "")
    try:
        held_on, at = slot.split("T")
        day = timezone.datetime.strptime(held_on, "%Y-%m-%d").date()
        hour = timezone.datetime.strptime(at, "%H:%M").time()
    except ValueError:
        messages.error(request, _("Choose an hour first."))
        return redirect("memberapp:card_trainers", token=token)
    try:
        classes.book_trainer(
            member=member,
            trainer=trainer,
            held_on=day,
            at=hour,
            note=request.POST.get("note", ""),
        )
    except classes.TooLate:
        messages.error(request, _("Somebody else took that hour. Please pick another."))
    except members.InvalidDetails:
        messages.error(request, _("That trainer is not taking bookings."))
    else:
        messages.success(
            request,
            _("Booked with %(name)s.") % {"name": trainer.full_name},
        )
    return redirect("memberapp:card_trainers", token=token)


@require_POST
def card_cancel_trainer(request, token, public_id):
    member = _member_for_token(token)
    _classes_or_404(member)
    session = get_object_or_404(PersonalSession.objects.for_gym(member.gym), public_id=public_id)
    classes.cancel_personal_session(session=session, member=member)
    messages.success(request, _("Cancelled."))
    return redirect("memberapp:card_trainers", token=token)


@require_POST
def card_review(request, token, public_id):
    """Stars and words about the trainer who took a class the member came to."""
    member = _member_for_token(token)
    _classes_or_404(member)
    session = get_object_or_404(ClassSession.objects.for_gym(member.gym), public_id=public_id)
    try:
        stars = int(request.POST.get("stars", "0"))
    except ValueError:
        stars = 0
    try:
        classes.leave_review(
            member=member, session=session, stars=stars, words=request.POST.get("words", "")
        )
    except members.InvalidDetails:
        messages.error(request, _("That review could not be saved."))
    else:
        messages.success(request, _("Thank you. Everyone at the gym can read it."))
    return redirect("memberapp:card", token=token)
