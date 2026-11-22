"""
Business rules for money: selling a membership, recording and reversing payments.

Every function that changes something takes `by` (the staff assignment of the
person doing it), checks their role and that the gym has the Payments and
Finance application, and does all of its work in one database transaction:
either everything is saved, including the ledger and audit lines, or nothing is.
"""

from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.audit import services as audit
from apps.billing.models import (
    Allocation,
    Invoice,
    InvoiceLine,
    LedgerEntry,
    LedgerKind,
    Payment,
    PaymentMethod,
    Reversal,
    Sequence,
    ShiftClose,
)
from apps.gyms.models import Gym, StaffAssignment
from apps.gyms.permissions import FRONT_DESK_ROLES, OWNER_ONLY, require_role
from apps.members import services as member_services
from apps.members.models import Member, Membership, Package, Service
from apps.members.services import InvalidDetails
from apps.modules import services as module_services
from apps.modules.catalog import PAYMENTS_FINANCE

# Methods where the provider gives a transaction reference that must be kept.
METHODS_NEEDING_REFERENCE = {
    PaymentMethod.MTN_MOMO,
    PaymentMethod.AIRTEL_MONEY,
    PaymentMethod.BANK_TRANSFER,
    PaymentMethod.CARD,
}


class AlreadyReversed(Exception):
    """This payment has already been reversed."""


@dataclass(frozen=True)
class PaymentInput:
    """A payment as typed in. `amount_rwf=None` means "the full amount owed"."""

    method: str
    amount_rwf: int | None = None
    reference: str = ""


@dataclass(frozen=True)
class Sale:
    membership: Membership
    invoice: Invoice
    payment: Payment | None


# --- Shared checks ----------------------------------------------------------


def payments_enabled(gym: Gym) -> bool:
    return module_services.is_enabled(gym, PAYMENTS_FINANCE)


def _require_payments(gym: Gym) -> None:
    if not payments_enabled(gym):
        raise PermissionDenied("Payments and Finance is not switched on for this gym.")


def _same_gym(by: StaffAssignment, record) -> None:
    if record.gym_id != by.gym_id:
        raise PermissionDenied("That record belongs to a different gym.")


def _lock_and_next_number(gym: Gym, name: str) -> int:
    """
    Take the gym's next invoice or receipt number.

    The row is locked until the transaction ends, so two people saving at the
    same moment wait for each other and can never get the same number.
    """
    sequence, _ = Sequence.objects.select_for_update().get_or_create(gym=gym, name=name)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return sequence.last_number


def _lock_billing(gym: Gym) -> None:
    """Make money changes for one gym happen one at a time."""
    Sequence.objects.select_for_update().get_or_create(gym=gym, name="lock")


# --- Reading ----------------------------------------------------------------


def paid_towards(invoice: Invoice) -> int:
    """How much has been paid towards an invoice, leaving out reversed payments."""
    total = Allocation.objects.for_gym(invoice.gym).filter(
        invoice=invoice, payment__reversal__isnull=True
    )
    return total.aggregate(sum=Sum("amount_rwf"))["sum"] or 0


def outstanding_on(invoice: Invoice) -> int:
    """How much is still owed on an invoice."""
    return invoice.total_rwf - paid_towards(invoice)


def member_balance(member: Member) -> int:
    """What the member owes the gym in total, from the ledger."""
    entries = LedgerEntry.objects.for_gym(member.gym).filter(member=member)
    return entries.aggregate(sum=Sum("amount_rwf"))["sum"] or 0


def _with_owed(invoices) -> list[Invoice]:
    """Turn invoices into a list, each with `owed_rwf` (total minus what was paid)."""
    result = list(invoices)
    for invoice in result:
        invoice.owed_rwf = invoice.total_rwf - invoice.paid_rwf
    return result


def invoices_for(member: Member) -> list[Invoice]:
    """A member's invoices, newest first, each with `paid_rwf` and `owed_rwf`."""
    invoices = Invoice.objects.for_gym(member.gym).filter(member=member)
    return _with_owed(invoices.with_paid().prefetch_related("lines"))


def payments_for(member: Member):
    return Payment.objects.for_gym(member.gym).filter(member=member).select_related("reversal")


def recent_payments(gym: Gym, limit: int = 25):
    return Payment.objects.for_gym(gym).select_related("member", "reversal")[:limit]


def collected_on(gym: Gym, day: date) -> int:
    """Money received on a day, leaving out reversed payments."""
    payments = Payment.objects.for_gym(gym).filter(received_on=day, reversal__isnull=True)
    return payments.aggregate(sum=Sum("amount_rwf"))["sum"] or 0


def unpaid_invoices(gym: Gym):
    """Invoices with something still owed, oldest first, as a list."""
    invoices = Invoice.objects.for_gym(gym).with_paid().select_related("member").order_by("number")
    return [invoice for invoice in _with_owed(invoices) if invoice.owed_rwf > 0]


def total_outstanding(gym: Gym) -> int:
    """Everything members owe the gym, from the ledger."""
    return LedgerEntry.objects.for_gym(gym).aggregate(sum=Sum("amount_rwf"))["sum"] or 0


# --- Changing ---------------------------------------------------------------


def _create_payment(
    *, by: StaffAssignment, invoice: Invoice, payment: PaymentInput, idempotency_key
) -> Payment:
    """Validate and save a payment towards an invoice. The caller holds the lock."""
    if payment.method not in PaymentMethod.values:
        raise InvalidDetails("method", "invalid")

    owed = outstanding_on(invoice)
    amount = owed if payment.amount_rwf is None else payment.amount_rwf
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise InvalidDetails("amount_rwf", "out_of_range")
    if amount > owed:
        raise InvalidDetails("amount_rwf", "more_than_owed")

    reference = " ".join((payment.reference or "").split())
    if payment.method in METHODS_NEEDING_REFERENCE and not reference:
        raise InvalidDetails("reference", "required")

    saved = Payment.objects.create(
        gym=by.gym,
        number=_lock_and_next_number(by.gym, "receipt"),
        member=invoice.member,
        amount_rwf=amount,
        method=payment.method,
        reference=reference,
        received_by=by.person,
        idempotency_key=idempotency_key,
    )
    Allocation.objects.create(gym=by.gym, payment=saved, invoice=invoice, amount_rwf=amount)
    LedgerEntry.objects.create(
        gym=by.gym,
        member=invoice.member,
        kind=LedgerKind.PAYMENT_RECEIVED,
        amount_rwf=-amount,
        invoice=invoice,
        payment=saved,
        created_by=by.person,
    )
    audit.record(
        action="payment.recorded",
        summary=(
            f"RWF {amount:,} received from {invoice.member.full_name} "
            f"by {saved.get_method_display()} ({saved.display_number})"
        ),
        gym=by.gym,
        actor=by.person,
        target=saved,
        details={"invoice": invoice.display_number, "amount_rwf": amount, "method": saved.method},
    )
    return saved


@transaction.atomic
def sell_membership(
    *,
    by: StaffAssignment,
    member: Member,
    package: Package,
    start_date: date,
    idempotency_key,
    payment: PaymentInput | None = None,
) -> Sale:
    """
    Sell a package to a member: create the membership and its invoice, and
    record a payment if one is given (`payment=None` means "pay later").

    If the same form arrives twice, the first sale is returned and nothing new
    is recorded. Raises InvalidDetails for wrong values.
    """
    require_role(by, FRONT_DESK_ROLES)
    _require_payments(by.gym)
    _same_gym(by, member)
    _same_gym(by, package)
    _lock_billing(by.gym)

    earlier = Invoice.objects.for_gym(by.gym).filter(idempotency_key=idempotency_key).first()
    if earlier is not None:
        line = earlier.lines.select_related("membership").first()
        first_payment = (
            Payment.objects.for_gym(by.gym).filter(idempotency_key=idempotency_key).first()
        )
        return Sale(membership=line.membership, invoice=earlier, payment=first_payment)

    membership = member_services.create_membership(
        by=by, member=member, package=package, start_date=start_date
    )
    invoice = Invoice.objects.create(
        gym=by.gym,
        number=_lock_and_next_number(by.gym, "invoice"),
        member=member,
        total_rwf=membership.price_rwf,
        idempotency_key=idempotency_key,
        created_by=by.person,
    )
    InvoiceLine.objects.create(
        gym=by.gym,
        invoice=invoice,
        description=(
            f"{membership.package_name}, {membership.start_date:%d %b %Y} "
            f"to {membership.end_date:%d %b %Y}"
        ),
        amount_rwf=membership.price_rwf,
        membership=membership,
    )
    LedgerEntry.objects.create(
        gym=by.gym,
        member=member,
        kind=LedgerKind.INVOICE_ISSUED,
        amount_rwf=invoice.total_rwf,
        invoice=invoice,
        created_by=by.person,
    )
    audit.record(
        action="invoice.issued",
        summary=(
            f"{invoice.display_number} for RWF {invoice.total_rwf:,} issued to {member.full_name}"
        ),
        gym=by.gym,
        actor=by.person,
        target=invoice,
        details={"membership": membership.public_id, "total_rwf": invoice.total_rwf},
    )

    saved_payment = None
    if payment is not None:
        saved_payment = _create_payment(
            by=by, invoice=invoice, payment=payment, idempotency_key=idempotency_key
        )
    return Sale(membership=membership, invoice=invoice, payment=saved_payment)


@dataclass(frozen=True)
class SessionSale:
    invoice: Invoice
    payment: Payment | None


MAX_SESSIONS_PER_SALE = 50


@transaction.atomic
def sell_sessions(
    *,
    by: StaffAssignment,
    member: Member,
    service: Service,
    quantity: int,
    idempotency_key,
    payment: PaymentInput | None = None,
) -> SessionSale:
    """
    Sell single sessions of a service (for example 2 x Sauna) to a member, with
    an invoice, and a payment if one is given. No membership is needed.
    """
    require_role(by, FRONT_DESK_ROLES)
    _require_payments(by.gym)
    _same_gym(by, member)
    _same_gym(by, service)
    _lock_billing(by.gym)

    earlier = Invoice.objects.for_gym(by.gym).filter(idempotency_key=idempotency_key).first()
    if earlier is not None:
        first_payment = (
            Payment.objects.for_gym(by.gym).filter(idempotency_key=idempotency_key).first()
        )
        return SessionSale(invoice=earlier, payment=first_payment)

    if not service.is_active:
        raise InvalidDetails("service", "archived")
    if not isinstance(quantity, int) or not 0 < quantity <= MAX_SESSIONS_PER_SALE:
        raise InvalidDetails("quantity", "out_of_range")

    total = service.price_rwf * quantity
    invoice = Invoice.objects.create(
        gym=by.gym,
        number=_lock_and_next_number(by.gym, "invoice"),
        member=member,
        total_rwf=total,
        idempotency_key=idempotency_key,
        created_by=by.person,
    )
    description = service.name if quantity == 1 else f"{service.name} x {quantity}"
    InvoiceLine.objects.create(
        gym=by.gym,
        invoice=invoice,
        description=f"{description}, {invoice.issued_on:%d %b %Y}",
        amount_rwf=total,
        service=service,
    )
    LedgerEntry.objects.create(
        gym=by.gym,
        member=member,
        kind=LedgerKind.INVOICE_ISSUED,
        amount_rwf=total,
        invoice=invoice,
        created_by=by.person,
    )
    audit.record(
        action="invoice.issued",
        summary=f"{invoice.display_number} for RWF {total:,} issued to {member.full_name}",
        gym=by.gym,
        actor=by.person,
        target=invoice,
        details={"service": str(service.public_id), "quantity": quantity, "total_rwf": total},
    )
    saved_payment = None
    if payment is not None:
        saved_payment = _create_payment(
            by=by, invoice=invoice, payment=payment, idempotency_key=idempotency_key
        )
    return SessionSale(invoice=invoice, payment=saved_payment)


@transaction.atomic
def record_payment(
    *, by: StaffAssignment, invoice: Invoice, payment: PaymentInput, idempotency_key
) -> Payment:
    """
    Record money received towards an invoice. Front-desk roles.

    If the same form arrives twice, the first payment is returned and nothing
    new is recorded. Raises InvalidDetails for wrong values.
    """
    require_role(by, FRONT_DESK_ROLES)
    _require_payments(by.gym)
    _same_gym(by, invoice)
    _lock_billing(by.gym)

    earlier = Payment.objects.for_gym(by.gym).filter(idempotency_key=idempotency_key).first()
    if earlier is not None:
        return earlier
    return _create_payment(by=by, invoice=invoice, payment=payment, idempotency_key=idempotency_key)


@transaction.atomic
def reverse_payment(*, by: StaffAssignment, payment: Payment, reason: str) -> Reversal:
    """
    Cancel a payment that was recorded wrongly. Owner only.

    Nothing is deleted: the payment stays, marked as reversed, and the ledger
    gets a new line that puts the amount back onto what the member owes.
    """
    require_role(by, OWNER_ONLY)
    _require_payments(by.gym)
    _same_gym(by, payment)
    _lock_billing(by.gym)

    reason = " ".join((reason or "").split())
    if not reason:
        raise InvalidDetails("reason", "required")
    if Reversal.objects.for_gym(by.gym).filter(payment=payment).exists():
        raise AlreadyReversed(f"{payment.display_number} has already been reversed.")

    reversal = Reversal.objects.create(
        gym=by.gym, payment=payment, reason=reason[:300], reversed_by=by.person
    )
    allocation = (
        Allocation.objects.for_gym(by.gym).filter(payment=payment).select_related("invoice").first()
    )
    LedgerEntry.objects.create(
        gym=by.gym,
        member=payment.member,
        kind=LedgerKind.PAYMENT_REVERSED,
        amount_rwf=payment.amount_rwf,
        invoice=allocation.invoice if allocation else None,
        payment=payment,
        created_by=by.person,
    )
    audit.record(
        action="payment.reversed",
        summary=(
            f"{payment.display_number} (RWF {payment.amount_rwf:,} from "
            f"{payment.member.full_name}) was reversed: {reason[:120]}"
        ),
        gym=by.gym,
        actor=by.person,
        target=payment,
        details={"amount_rwf": payment.amount_rwf, "reason": reason[:300]},
    )
    return reversal


# --- Figures for the owner --------------------------------------------------


def _live_payments(gym: Gym, start: date, end: date):
    return Payment.objects.for_gym(gym).filter(
        received_on__range=(start, end), reversal__isnull=True
    )


def collected_between(gym: Gym, start: date, end: date) -> int:
    """Money received between two days (both included), leaving out reversed payments."""
    return _live_payments(gym, start, end).aggregate(sum=Sum("amount_rwf"))["sum"] or 0


def collected_by_method(gym: Gym, start: date, end: date) -> list[dict]:
    """Money received between two days, per payment method, largest first."""
    labels = dict(PaymentMethod.choices)
    rows = (
        _live_payments(gym, start, end)
        .values("method")
        .annotate(amount=Sum("amount_rwf"), count=Count("id"))
        .order_by("-amount")
    )
    return [{"label": labels.get(r["method"], r["method"]), **r} for r in rows]


def sales_by_package(gym: Gym, start: date, end: date) -> list[dict]:
    """
    Memberships sold between two days, per package: how many and their value.

    This is what was sold (invoiced), whether or not it has been paid yet.
    """
    rows = (
        InvoiceLine.objects.for_gym(gym)
        .filter(invoice__issued_on__range=(start, end), membership__isnull=False)
        .values("membership__package_name")
        .annotate(amount=Sum("amount_rwf"), count=Count("id"))
        .order_by("-amount")
    )
    return [
        {"label": r["membership__package_name"], "amount": r["amount"], "count": r["count"]}
        for r in rows
    ]


def balances_for(gym: Gym, member_ids) -> dict[int, int]:
    """What each of these members owes, in one query. Members owing nothing are left out."""
    rows = (
        LedgerEntry.objects.for_gym(gym)
        .filter(member_id__in=list(member_ids))
        .values("member_id")
        .annotate(balance=Sum("amount_rwf"))
    )
    return {r["member_id"]: r["balance"] for r in rows if r["balance"] > 0}


# --- Shifts: what a receptionist hands over ---------------------------------------

MOBILE_MONEY = (PaymentMethod.MTN_MOMO, PaymentMethod.AIRTEL_MONEY)


def _day_start(at=None):
    local = timezone.localtime(at or timezone.now())
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def shift_start(gym: Gym, person) -> datetime:
    """When the person's current shift began: their last close today, or midnight."""
    midnight = _day_start()
    last = (
        ShiftClose.objects.for_gym(gym)
        .filter(person=person, closed_at__gte=midnight)
        .order_by("-closed_at")
        .values_list("closed_at", flat=True)
        .first()
    )
    return last or midnight


@dataclass(frozen=True)
class ShiftSummary:
    started_at: datetime
    cash_rwf: int
    mtn_rwf: int
    airtel_rwf: int
    other_rwf: int
    payments: int
    sales: int

    @property
    def mobile_money_rwf(self) -> int:
        return self.mtn_rwf + self.airtel_rwf

    @property
    def total_rwf(self) -> int:
        return self.cash_rwf + self.mobile_money_rwf + self.other_rwf


def shift_summary(gym: Gym, person, since=None) -> ShiftSummary:
    """Money the person received since the start of their shift (reversed payments left out)."""
    since = since or shift_start(gym, person)
    payments = Payment.objects.for_gym(gym).filter(
        received_by=person, created_at__gte=since, reversal__isnull=True
    )
    by_method = dict(
        payments.values("method").annotate(total=Sum("amount_rwf")).values_list("method", "total")
    )
    mtn = by_method.pop(PaymentMethod.MTN_MOMO, 0) or 0
    airtel = by_method.pop(PaymentMethod.AIRTEL_MONEY, 0) or 0
    cash = by_method.pop(PaymentMethod.CASH, 0) or 0
    return ShiftSummary(
        started_at=since,
        cash_rwf=cash,
        mtn_rwf=mtn,
        airtel_rwf=airtel,
        other_rwf=sum(v or 0 for v in by_method.values()),
        payments=payments.count(),
        sales=Invoice.objects.for_gym(gym).filter(created_by=person, created_at__gte=since).count(),
    )


@transaction.atomic
def close_shift(*, by: StaffAssignment) -> ShiftClose:
    """Close the person's shift and record what they hand over."""
    require_role(by, FRONT_DESK_ROLES)
    _lock_billing(by.gym)
    summary = shift_summary(by.gym, by.person)
    closed = ShiftClose.objects.create(
        gym=by.gym,
        person=by.person,
        started_at=summary.started_at,
        cash_rwf=summary.cash_rwf,
        mobile_money_rwf=summary.mobile_money_rwf,
        other_rwf=summary.other_rwf,
        payments=summary.payments,
    )
    audit.record(
        action="shift.closed",
        summary=f"{by.person.full_name} closed their shift: RWF {summary.cash_rwf:,} cash",
        gym=by.gym,
        actor=by.person,
        target=closed,
        details={"cash_rwf": summary.cash_rwf, "total_rwf": summary.total_rwf},
    )
    return closed


def cash_by_person_today(gym: Gym) -> list[dict]:
    """For the owner: cash each person received today, and whether their shift is closed."""
    midnight = _day_start()
    rows = (
        Payment.objects.for_gym(gym)
        .filter(created_at__gte=midnight, reversal__isnull=True, method=PaymentMethod.CASH)
        .values("received_by", "received_by__full_name")
        .annotate(cash=Sum("amount_rwf"))
    )
    closes = {}
    for close in ShiftClose.objects.for_gym(gym).filter(closed_at__gte=midnight):
        closes.setdefault(close.person_id, close)  # newest first
    result = []
    for row in rows:
        person_id = row["received_by"]
        last = closes.get(person_id)
        open_cash = row["cash"] or 0
        if last is not None:
            open_cash = shift_summary(gym, last.person, since=last.closed_at).cash_rwf
        result.append(
            {
                "name": row["received_by__full_name"] or "—",
                "cash_rwf": row["cash"] or 0,
                "closed_at": last.closed_at if last else None,
                "open_cash_rwf": open_cash,
            }
        )
    return sorted(result, key=lambda r: -r["cash_rwf"])
