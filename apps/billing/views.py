"""
Screens for selling memberships, taking payments and showing receipts.

Each screen collects input, calls a function in services.py and shows the result.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.billing import services
from apps.billing.forms import (
    PaymentForm,
    PayNow,
    ReversalForm,
    SellMembershipForm,
    SellSessionsForm,
)
from apps.billing.models import Invoice, Payment
from apps.gyms.permissions import FRONT_DESK_ROLES, OWNER_ONLY
from apps.gyms.tenancy import gym_required
from apps.members import services as member_services
from apps.members.models import Member
from apps.modules.catalog import PAYMENTS_FINANCE
from apps.modules.decorators import module_required


def _error_message(error: services.InvalidDetails) -> str:
    known = {
        ("package", "archived"): _("This package is no longer sold."),
        ("start_date", "out_of_range"): _(
            "Choose a start date within the last 31 days or the coming year."
        ),
        ("start_date", "overlaps"): _(
            "The member already has a membership covering these dates. Choose a later start."
        ),
        ("amount_rwf", "out_of_range"): _("Enter an amount above zero."),
        ("amount_rwf", "more_than_owed"): _("This is more than what is owed."),
        ("method", "invalid"): _("Choose how it was paid."),
        ("reason", "required"): _("Give a reason."),
        ("service", "archived"): _("This service is no longer sold."),
        ("quantity", "out_of_range"): _("Enter between 1 and 50 sessions."),
    }
    return known.get((error.field, error.reason), _("Check this value."))


def _show_error(form, error: services.InvalidDetails) -> None:
    field = error.field if error.field in form.fields else None
    form.add_error(field, _error_message(error))


def _get_member(request, public_id) -> Member:
    members = Member.objects.for_gym(request.gym).select_related("person", "branch")
    return get_object_or_404(members, public_id=public_id)


def _payment_input(data, *, full: bool = False) -> services.PaymentInput:
    return services.PaymentInput(
        method=data["method"],
        amount_rwf=None if full else data["amount_rwf"],
    )


# --- Selling ----------------------------------------------------------------


@gym_required(*FRONT_DESK_ROLES)
@module_required(PAYMENTS_FINANCE)
def sell_package(request, public_id):
    member = _get_member(request, public_id)
    initial = {"start_date": member_services.next_start_date(member)}
    form = SellMembershipForm(request.POST or None, gym=request.gym, initial=initial)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        payment = _payment_input(data, full=data["pay_now"] == PayNow.FULL)
        try:
            sale = services.sell_package(
                by=request.assignment,
                member=member,
                package=data["package"],
                start_date=data["start_date"],
                idempotency_key=data["idempotency_key"],
                payment=payment,
            )
        except services.InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(
                request,
                _("%(package)s sold to %(name)s.")
                % {"package": sale.package_name, "name": member.full_name},
            )
            if sale.payment is not None:
                return redirect("billing:receipt", public_id=sale.payment.public_id)
            return redirect("members:member_detail", public_id=member.public_id)

    return render(
        request,
        "billing/sell_form.html",
        {"form": form, "member": member, "has_packages": form.fields["package"].queryset.exists()},
    )


@gym_required(*FRONT_DESK_ROLES)
@module_required(PAYMENTS_FINANCE)
def sell_sessions(request, public_id):
    member = _get_member(request, public_id)
    form = SellSessionsForm(request.POST or None, gym=request.gym)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        payment = _payment_input(data, full=data["pay_now"] == PayNow.FULL)
        try:
            sale = services.sell_sessions(
                by=request.assignment,
                member=member,
                service=data["service"],
                quantity=data["quantity"],
                idempotency_key=data["idempotency_key"],
                payment=payment,
            )
        except services.InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(
                request,
                _("%(service)s sold to %(name)s.")
                % {"service": data["service"].name, "name": member.full_name},
            )
            if sale.payment is not None:
                return redirect("billing:receipt", public_id=sale.payment.public_id)
            return redirect("members:member_detail", public_id=member.public_id)

    return render(
        request,
        "billing/sell_sessions_form.html",
        {"form": form, "member": member, "has_services": form.fields["service"].queryset.exists()},
    )


# --- A member's account -----------------------------------------------------


@gym_required(*FRONT_DESK_ROLES)
@module_required(PAYMENTS_FINANCE)
def member_account(request, public_id):
    member = _get_member(request, public_id)
    context = {
        "member": member,
        "balance": services.member_balance(member),
        "invoices": services.invoices_for(member),
        "payments": services.payments_for(member),
    }
    # The member's page asks for this part on its own (via HTMX) and places it inline.
    if request.headers.get("HX-Request"):
        return render(request, "billing/_member_account.html", context)
    return render(request, "billing/member_account.html", context)


# --- Payments ---------------------------------------------------------------


def _get_invoice(request, public_id) -> Invoice:
    invoices = Invoice.objects.for_gym(request.gym).select_related("member")
    return get_object_or_404(invoices, public_id=public_id)


@gym_required(*FRONT_DESK_ROLES)
@module_required(PAYMENTS_FINANCE)
def record_payment(request, public_id):
    invoice = _get_invoice(request, public_id)
    owed = services.outstanding_on(invoice)
    form = PaymentForm(request.POST or None, initial={"amount_rwf": owed})

    if request.method == "POST" and form.is_valid():
        try:
            payment = services.record_payment(
                by=request.assignment,
                invoice=invoice,
                payment=_payment_input(form.cleaned_data),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except services.InvalidDetails as error:
            _show_error(form, error)
        else:
            messages.success(request, _("Payment recorded."))
            return redirect("billing:receipt", public_id=payment.public_id)

    return render(
        request, "billing/payment_form.html", {"form": form, "invoice": invoice, "owed": owed}
    )


def _get_payment(request, public_id) -> Payment:
    payments = Payment.objects.for_gym(request.gym).select_related(
        "member__person", "received_by", "reversal__reversed_by"
    )
    return get_object_or_404(payments, public_id=public_id)


@gym_required(*FRONT_DESK_ROLES)
@module_required(PAYMENTS_FINANCE)
def receipt(request, public_id):
    payment = _get_payment(request, public_id)
    allocation = payment.allocations.select_related("invoice").prefetch_related("invoice__lines")
    invoice = allocation.first().invoice
    return render(
        request,
        "billing/receipt.html",
        {
            "payment": payment,
            "invoice": invoice,
            "still_owed": services.outstanding_on(invoice),
            "message": services.receipt_message(gym=request.gym, payment=payment, invoice=invoice),
        },
    )


@gym_required(*OWNER_ONLY)
@module_required(PAYMENTS_FINANCE)
def reverse_payment(request, public_id):
    payment = _get_payment(request, public_id)
    if payment.is_reversed:
        return redirect("billing:receipt", public_id=payment.public_id)

    form = ReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reverse_payment(
                by=request.assignment, payment=payment, reason=form.cleaned_data["reason"]
            )
        except services.InvalidDetails as error:
            _show_error(form, error)
        except services.AlreadyReversed:
            return redirect("billing:receipt", public_id=payment.public_id)
        else:
            messages.success(
                request, _("%(number)s was reversed.") % {"number": payment.display_number}
            )
            return redirect("billing:receipt", public_id=payment.public_id)

    return render(request, "billing/reverse_form.html", {"form": form, "payment": payment})


@gym_required(*FRONT_DESK_ROLES)
@module_required(PAYMENTS_FINANCE)
def payments_home(request):
    today = timezone.localdate()
    return render(
        request,
        "billing/payments_home.html",
        {
            "today": today,
            "collected_today": services.collected_on(request.gym, today),
            "total_outstanding": services.total_outstanding(request.gym),
            "payments": services.recent_payments(request.gym),
            "unpaid_invoices": services.unpaid_invoices(request.gym),
        },
    )
