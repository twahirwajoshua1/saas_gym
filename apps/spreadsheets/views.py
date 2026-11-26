"""
Screens for importing members and exporting data. Owner and branch manager.

Importing has two steps. The upload is checked and shown as a preview, and the
rows are kept in the person's session (not saved to the gym's data). Confirming
saves them. The session copy is removed at that moment, so pressing Confirm
twice imports once.
"""

import uuid
from datetime import date, datetime

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.gyms.permissions import MANAGER_ROLES
from apps.gyms.tenancy import gym_required
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE
from apps.spreadsheets import exporting, importing
from apps.spreadsheets.forms import DateRangeForm, UploadForm
from apps.spreadsheets.reading import MAX_ROWS, UnreadableSpreadsheet, read_rows

SESSION_KEY = "member_import"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _problem_message(code: str) -> str:
    field, _sep, rest = code.partition(":")
    reason, _sep, extra = rest.partition(":")
    if (field, reason) == ("phone", "repeated"):
        return _("Same phone number as row %(row)s.") % {"row": extra}
    known = {
        ("full_name", "required"): _("Name is missing."),
        ("phone", "invalid"): _("Phone number is missing or not valid."),
        ("branch", "not_found"): _("No branch with this name."),
        ("branch", "required"): _("Branch is missing (this gym has several)."),
        ("branch", "invalid"): _("No branch with this name."),
        ("gender", "invalid"): _("Gender not recognised. Use F or M."),
        ("date_of_birth", "invalid"): _("Date of birth not recognised. Write it as 14/03/1995."),
        ("date_of_birth", "in_future"): _("Date of birth is in the future."),
        ("emergency_contact_phone", "invalid"): _("Emergency contact phone is not valid."),
        ("package", "not_found"): _(
            "No package with this name. Add it on the Packages page first."
        ),
        ("package", "required"): _("Valid until is filled in but Package is empty."),
        ("valid_until", "required"): _("Package is filled in but Valid until is empty."),
        ("valid_until", "invalid"): _("Valid until is not a date. Write it as 15/12/2026."),
        ("valid_until", "in_past"): _(
            "Valid until is in the past. For a membership that has ended, "
            "leave Package and Valid until empty."
        ),
        ("valid_until", "too_far"): _("Valid until is more than 5 years away. Check the year."),
    }
    return known.get((field, reason), _("Check this row."))


def _unreadable_message(error: UnreadableSpreadsheet) -> str:
    known = {
        "not_excel": _("This file could not be read as an Excel file."),
        "not_csv": _("This CSV file could not be read."),
        "wrong_type": _(
            "Upload an Excel (.xlsx) or CSV file. An older .xls file can be opened "
            "in Excel and saved as .xlsx."
        ),
        "too_big": _("The file is larger than 5 MB."),
        "empty": _("The file is empty."),
        "missing_columns": _("These columns are missing: %(cols)s. Use the template.")
        % {"cols": error.detail},
        "too_many_rows": _("The file has more than %(max)s members. Split it into smaller files.")
        % {"max": f"{MAX_ROWS:,}"},
        "no_rows": _("No members were found below the column headings."),
    }
    return known.get(error.reason, _("This file could not be used."))


def _for_session(rows: list[dict]) -> list[dict]:
    """Dates become text so the rows can be kept in the session."""

    def plain(value):
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value

    return [{key: plain(value) for key, value in row.items()} for row in rows]


def _home_context(request, **extra):
    gym = request.gym
    return {
        "upload_form": UploadForm(),
        "range_form": DateRangeForm.this_month(),
        "payments_on": modules.is_enabled(gym, PAYMENTS_FINANCE),
        "attendance_on": modules.is_enabled(gym, ATTENDANCE_ACCESS),
        **extra,
    }


@gym_required(*MANAGER_ROLES)
def home(request):
    return render(request, "spreadsheets/home.html", _home_context(request))


@gym_required(*MANAGER_ROLES)
def template(request):
    response = HttpResponse(exporting.template(), content_type=XLSX)
    response["Content-Disposition"] = 'attachment; filename="members-import-template.xlsx"'
    return response


@gym_required(*MANAGER_ROLES)
@require_POST
def upload(request):
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "spreadsheets/home.html", _home_context(request, upload_form=form))

    uploaded = form.cleaned_data["file"]
    try:
        rows = read_rows(uploaded.name, uploaded.read())
    except UnreadableSpreadsheet as error:
        form.add_error("file", _unreadable_message(error))
        return render(request, "spreadsheets/home.html", _home_context(request, upload_form=form))

    check = importing.check_rows(request.gym, rows)
    token = str(uuid.uuid4())
    request.session[SESSION_KEY] = {
        "token": token,
        "gym": str(request.gym.public_id),
        "file_name": uploaded.name[:120],
        "rows": _for_session(rows),
    }
    for row in check.problems:
        row.messages = [_problem_message(code) for code in row.problems]
    return render(
        request,
        "spreadsheets/preview.html",
        {"check": check, "token": token, "file_name": uploaded.name},
    )


@gym_required(*MANAGER_ROLES)
@require_POST
def confirm(request):
    pending = request.session.get(SESSION_KEY)
    if (
        not pending
        or pending.get("token") != request.POST.get("token")
        or pending.get("gym") != str(request.gym.public_id)
    ):
        messages.error(
            request, _("This import was already done or has expired. Upload the file again.")
        )
        return redirect("spreadsheets:home")

    del request.session[SESSION_KEY]  # removed first, so Confirm twice imports once
    result = importing.import_rows(
        by=request.assignment, rows=pending["rows"], file_name=pending["file_name"]
    )
    messages.success(
        request,
        _(
            "%(added)s members imported, %(memberships)s with a current membership. "
            "%(skipped)s rows skipped."
        )
        % {
            "added": result.members_added,
            "memberships": result.memberships_added,
            "skipped": result.skipped,
        },
    )
    return redirect("frontdesk:people")


@gym_required(*MANAGER_ROLES)
@require_POST
def cancel(request):
    request.session.pop(SESSION_KEY, None)
    return redirect("spreadsheets:home")


def _download(content: bytes, name: str) -> HttpResponse:
    stamp = timezone.localdate().isoformat()
    response = HttpResponse(content, content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{name}-{stamp}.xlsx"'
    return response


@gym_required(*MANAGER_ROLES)
@require_POST
def export_members(request):
    return _download(exporting.export_members(by=request.assignment), "members")


def _export_range(request, what, function):
    form = DateRangeForm(request.POST)
    if not form.is_valid():
        context = _home_context(request, range_form=form, range_for=what)
        return render(request, "spreadsheets/home.html", context, status=400)
    content = function(
        by=request.assignment, start=form.cleaned_data["start"], end=form.cleaned_data["end"]
    )
    return _download(content, what)


@gym_required(*MANAGER_ROLES)
@require_POST
def export_payments(request):
    return _export_range(request, "payments", exporting.export_payments)


@gym_required(*MANAGER_ROLES)
@require_POST
def export_visits(request):
    return _export_range(request, "visits", exporting.export_visits)
