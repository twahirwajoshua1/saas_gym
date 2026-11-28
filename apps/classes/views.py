"""
Screens for classes and trainers.

Three audiences, one module: the owner builds the timetable, the desk and
trainers see who is coming, and members book from their own card (the card
pages live in memberapp and call these services).
"""

from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.classes import services
from apps.classes.forms import ClassTypeForm, HoursForm, NewTrainerForm, ScheduleForm, TrainerForm
from apps.classes.models import ClassSession, ClassType, Review, TrainerProfile
from apps.gyms.models import Branch, Role, StaffAssignment
from apps.gyms.permissions import CHECK_IN_ROLES, MANAGER_ROLES
from apps.gyms.session import desk_branch
from apps.gyms.tenancy import gym_required
from apps.members.services import InvalidDetails
from apps.modules.catalog import TRAINING_CLASSES
from apps.modules.decorators import module_required
from apps.team import services as team

MESSAGES = {
    ("name", "required"): _("Give the class a name."),
    ("name", "already_used"): _("There is already a class with this name."),
    ("minutes", "out_of_range"): _("Enter a length between 5 and 300 minutes."),
    ("places", "out_of_range"): _("Enter a number of places between 1 and 500."),
    ("repeat_weeks", "out_of_range"): _("Repeat for between 1 and 12 weeks."),
    ("first_day", "in_the_past"): _("Start today or later."),
    ("hours", "ends_before_it_starts"): _("The finish time must be after the start time."),
    ("staff", "not_a_trainer"): _("Only a staff member whose role is Trainer can have a page."),
    ("reason", "required"): _("Give a reason."),
}


def _show_error(form, error: InvalidDetails) -> None:
    field = error.field if error.field in form.fields else None
    form.add_error(field, MESSAGES.get((error.field, error.reason), _("Check this value.")))


def _week_start(request):
    """The Monday of the week being looked at, from ?week=YYYY-MM-DD."""
    today = timezone.localdate()
    asked = request.GET.get("week")
    if asked:
        try:
            day = timezone.datetime.strptime(asked, "%Y-%m-%d").date()
            return day - timedelta(days=day.weekday())
        except ValueError:
            pass
    return today - timedelta(days=today.weekday())


# --- The timetable ----------------------------------------------------------


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
def timetable(request):
    """The week's classes. Everyone who works the floor can see it."""
    start = _week_start(request)
    branch = desk_branch(request)
    sessions = services.timetable(request.gym, start, days=7, branch=branch)
    days = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        days.append({"day": day, "sessions": [s for s in sessions if s.held_on == day]})
    return render(
        request,
        "classes/timetable.html",
        {
            "week_start": start,
            "next_week": start + timedelta(days=7),
            "pattern": services.weekly_pattern(request.gym, branch),
            "days": days,
            "branch": branch,
            "branches": Branch.objects.for_gym(request.gym).filter(is_active=True),
            "is_manager": request.assignment.role in MANAGER_ROLES,
            "today": timezone.localdate(),
        },
    )


@gym_required(*MANAGER_ROLES)
@module_required(TRAINING_CLASSES)
def schedule(request):
    """Put a class on the timetable, once or every week."""
    form = ScheduleForm(request.POST or None, gym=request.gym, at_branch=desk_branch(request))
    if request.method == "POST" and form.is_valid():
        try:
            made = services.add_to_timetable(
                by=request.assignment,
                class_type=form.cleaned_data["class_type"],
                branch=form.cleaned_data["branch"],
                first_day=form.cleaned_data["first_day"],
                at=form.cleaned_data["at"],
                trainer=form.cleaned_data["trainer"],
                repeat_weeks=form.cleaned_data["repeat_weeks"],
            )
        except InvalidDetails as error:
            _show_error(form, error)
        else:
            if made:
                messages.success(
                    request,
                    _("%(n)s class(es) added to the timetable.") % {"n": len(made)},
                )
            else:
                messages.info(request, _("Those classes were already on the timetable."))
            return redirect("classes:timetable")
    return render(request, "classes/schedule.html", {"form": form})


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
def session_detail(request, public_id):
    """One class: who is coming, and the trainer taking it."""
    sessions = ClassSession.objects.for_gym(request.gym).select_related(
        "class_type", "branch", "trainer__staff__person"
    )
    session = get_object_or_404(sessions, public_id=public_id)
    average, count = (None, 0)
    if session.trainer is not None:
        average, count = services.rating(session.trainer)
    return render(
        request,
        "classes/session_detail.html",
        {
            "session": session,
            "coming": services.who_is_coming(session),
            "places_left": services.places_left(session),
            "rating": average,
            "rating_count": count,
            "is_manager": request.assignment.role in MANAGER_ROLES,
        },
    )


@gym_required(*MANAGER_ROLES)
@module_required(TRAINING_CLASSES)
@require_POST
def session_cancel(request, public_id):
    sessions = ClassSession.objects.for_gym(request.gym)
    session = get_object_or_404(sessions, public_id=public_id)
    services.cancel_session(
        by=request.assignment, session=session, reason=request.POST.get("reason", "")
    )
    messages.success(request, _("The class was called off. Tell the people who booked."))
    return redirect("classes:session_detail", public_id=public_id)


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
@require_POST
def mark_came(request, public_id):
    """Tick someone off at the class."""
    session = get_object_or_404(ClassSession.objects.for_gym(request.gym), public_id=public_id)
    booking = get_object_or_404(session.bookings, public_id=request.POST.get("booking", ""))
    services.mark_came(by=request.assignment, booking=booking, came=not booking.came_at)
    return redirect("classes:session_detail", public_id=public_id)


# --- What the gym teaches ---------------------------------------------------


@gym_required(*MANAGER_ROLES)
@module_required(TRAINING_CLASSES)
def class_types(request):
    return render(
        request,
        "classes/class_types.html",
        {"class_types": services.list_class_types(request.gym, active_only=False)},
    )


@gym_required(*MANAGER_ROLES)
@module_required(TRAINING_CLASSES)
def class_type_edit(request, public_id=None):
    class_type = None
    if public_id is not None:
        class_type = get_object_or_404(ClassType.objects.for_gym(request.gym), public_id=public_id)
    initial = (
        {
            "name": class_type.name,
            "what_to_expect": class_type.what_to_expect,
            "minutes": class_type.minutes,
            "places": class_type.places,
        }
        if class_type
        else {"minutes": 60, "places": 20}
    )
    form = ClassTypeForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.save_class_type(
                by=request.assignment, class_type=class_type, **form.cleaned_data
            )
        except InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(request, _("Saved."))
            return redirect("classes:class_types")
    return render(request, "classes/class_type_form.html", {"form": form, "class_type": class_type})


@gym_required(*MANAGER_ROLES)
@module_required(TRAINING_CLASSES)
@require_POST
def class_type_archive(request, public_id):
    class_type = get_object_or_404(ClassType.objects.for_gym(request.gym), public_id=public_id)
    services.archive_class_type(by=request.assignment, class_type=class_type)
    messages.success(request, _("%(name)s is no longer taught.") % {"name": class_type.name})
    return redirect("classes:class_types")


# --- Trainers ---------------------------------------------------------------


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
def trainers(request):
    people = []
    for trainer in services.list_trainers(request.gym, listed_only=False):
        average, count = services.rating(trainer)
        people.append({"trainer": trainer, "rating": average, "count": count})
    unlisted = (
        StaffAssignment.objects.for_gym(request.gym)
        .filter(role=Role.TRAINER, is_active=True, trainer__isnull=True)
        .select_related("person")
    )
    return render(
        request,
        "classes/trainers.html",
        {
            "people": people,
            "without_page": unlisted,
            "is_manager": request.assignment.role in MANAGER_ROLES,
        },
    )


def _trainer_or_404(request, public_id) -> TrainerProfile:
    trainers = TrainerProfile.objects.for_gym(request.gym).select_related("staff__person")
    return get_object_or_404(trainers, public_id=public_id)


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
def trainer_detail(request, public_id):
    trainer = _trainer_or_404(request, public_id)
    average, count = services.rating(trainer)
    today = timezone.localdate()
    return render(
        request,
        "classes/trainer_detail.html",
        {
            "trainer": trainer,
            "rating": average,
            "rating_count": count,
            "reviews": services.reviews_of(trainer),
            "diary": services.trainer_diary(trainer, today, days=7),
            "hours": trainer.hours.select_related("branch"),
            "is_manager": request.assignment.role in MANAGER_ROLES,
            "is_them": request.assignment.pk == trainer.staff_id,
        },
    )


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
def trainer_edit(request, staff_id=None, public_id=None):
    """A trainer's page. A manager writes any of them; a trainer their own."""
    if public_id is not None:
        trainer = _trainer_or_404(request, public_id)
        staff = trainer.staff
    else:
        staff = get_object_or_404(
            StaffAssignment.objects.for_gym(request.gym).select_related("person"),
            person__public_id=staff_id,
        )
        trainer = getattr(staff, "trainer", None)
    if request.assignment.pk != staff.pk and request.assignment.role not in MANAGER_ROLES:
        messages.error(request, _("Only a manager can change another trainer's page."))
        return redirect("classes:trainers")

    initial = (
        {
            "headline": trainer.headline,
            "about": trainer.about,
            "takes_personal_sessions": trainer.takes_personal_sessions,
            "gives_massages": trainer.gives_massages,
            "is_listed": trainer.is_listed,
        }
        if trainer
        else {"takes_personal_sessions": True, "is_listed": True}
    )
    form = TrainerForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_trainer(by=request.assignment, staff=staff, **form.cleaned_data)
        except InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(request, _("Saved."))
            return redirect("classes:trainer_detail", public_id=saved.public_id)
    return render(
        request, "classes/trainer_form.html", {"form": form, "staff": staff, "trainer": trainer}
    )


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
def trainer_hours(request, public_id):
    """The hours a trainer is free for one-to-one sessions."""
    trainer = _trainer_or_404(request, public_id)
    if request.assignment.pk != trainer.staff_id and request.assignment.role not in MANAGER_ROLES:
        messages.error(request, _("Only a manager can change another trainer's hours."))
        return redirect("classes:trainers")

    form = HoursForm(request.POST or None, gym=request.gym)
    windows = [
        (h.weekday, h.starts_at, h.ends_at, h.branch)
        for h in trainer.hours.select_related("branch")
    ]
    if request.method == "POST":
        if request.POST.get("remove"):
            keep = [w for index, w in enumerate(windows) if str(index) != request.POST["remove"]]
            services.set_trainer_hours(by=request.assignment, trainer=trainer, windows=keep)
            return redirect("classes:trainer_hours", public_id=public_id)
        if form.is_valid():
            new = (
                form.cleaned_data["weekday"],
                form.cleaned_data["starts_at"],
                form.cleaned_data["ends_at"],
                form.cleaned_data["branch"],
            )
            try:
                services.set_trainer_hours(
                    by=request.assignment, trainer=trainer, windows=[*windows, new]
                )
            except InvalidDetails as error:
                _show_error(form, error)
            else:
                messages.success(request, _("Saved."))
                return redirect("classes:trainer_hours", public_id=public_id)
    return render(
        request,
        "classes/trainer_hours.html",
        {
            "trainer": trainer,
            "form": form,
            "hours": list(enumerate(trainer.hours.select_related("branch"))),
        },
    )


@gym_required(*MANAGER_ROLES)
@module_required(TRAINING_CLASSES)
@require_POST
def review_hide(request, public_id):
    review = get_object_or_404(Review.objects.for_gym(request.gym), public_id=public_id)
    try:
        services.hide_review(
            by=request.assignment, review=review, reason=request.POST.get("reason", "")
        )
    except InvalidDetails:
        messages.error(request, _("Say why this review is being hidden."))
    else:
        messages.success(request, _("The review is hidden. The reason is in the activity log."))
    return redirect("classes:trainer_detail", public_id=review.trainer.public_id)


# Why adding a trainer can fail, in words the desk can act on.
STAFF_PROBLEMS = {
    "phone_invalid": _("Enter a valid phone number, for example 0788 123 456."),
    "name_required": _("Enter the trainer's name."),
    "already_staff": _("This person already works at this gym."),
}


@gym_required(*CHECK_IN_ROLES)
@module_required(TRAINING_CLASSES)
def trainer_add(request):
    """
    Add a trainer from the desk, so they can be put on the timetable at once.

    No login is created here: that is the owner's to give on the staff page.
    """
    form = NewTrainerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            assignment = team.add_trainer(
                by=request.assignment,
                full_name=data["full_name"],
                phone=data["phone"],
                branch=desk_branch(request),
            )
        except team.StaffProblem as problem:
            form.add_error(None, STAFF_PROBLEMS.get(problem.reason, _("Check these details.")))
        else:
            trainer = services.save_trainer(
                by=request.assignment,
                staff=assignment,
                headline=data["headline"],
                gives_massages=data["gives_massages"],
            )
            messages.success(
                request,
                _("%(name)s was added. Set their hours so people can book them.")
                % {"name": assignment.person.full_name},
            )
            return redirect("classes:trainer_hours", public_id=trainer.public_id)
    return render(request, "classes/trainer_add.html", {"form": form})
