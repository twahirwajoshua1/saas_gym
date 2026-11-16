"""
Business rules for money: selling a membership, recording and reversing payments.

Every function that changes something takes `by` (the staff assignment of the
person doing it), checks their role and that the gym has the Payments and
Finance application, and does all of its work in one database transaction:
either everything is saved, including the ledger and audit lines, or nothing is.
"""

from dataclasses import dataclass
from datetime import date

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum

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
)
from apps.gyms.models import Gym, StaffAssignment
from apps.gyms.permissions import FRONT_DESK_ROLES, OWNER_ONLY, require_role
from apps.members import services as member_services
from apps.members.models import Member, Membership, Package
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
