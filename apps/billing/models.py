"""
The money records.

  Invoice     what a member owes
  Payment     money actually received (it has the receipt number)
  Allocation  which invoice a payment went towards
  Reversal    cancels a payment that was recorded wrongly
  LedgerEntry one line in the member's running statement

All five are permanent: once saved, the database refuses to change or delete
them (see migration 0002). A mistake is corrected by adding a new record, never
by editing an old one. Amounts are whole Rwandan francs.
"""

import uuid

from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.gyms.models import GymOwnedModel, GymOwnedQuerySet
from apps.members.models import Member, Membership, Service, Ticketbook
from apps.people.models import Person


class Sequence(GymOwnedModel):
    """The last invoice or receipt number a gym has used. Each gym counts from 1."""

    name = models.CharField(max_length=20)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="one_sequence_per_name_per_gym"),
        ]


class InvoiceQuerySet(GymOwnedQuerySet):
    def with_paid(self):
        """Add `paid_rwf` to each invoice: payments towards it that were not reversed."""
        not_reversed = Q(allocations__payment__reversal__isnull=True)
        return self.annotate(
            paid_rwf=Coalesce(Sum("allocations__amount_rwf", filter=not_reversed), 0)
        )


class Invoice(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.PositiveIntegerField()
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="invoices")
    issued_on = models.DateField(default=timezone.localdate)
    total_rwf = models.PositiveIntegerField()

    # Sent with the form that created it, so the same form arriving twice
    # (a double click, a retry on a bad connection) creates one invoice.
    idempotency_key = models.UUIDField()

    created_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "number"], name="unique_invoice_number_per_gym"),
            models.UniqueConstraint(
                fields=["gym", "idempotency_key"], name="unique_invoice_key_per_gym"
            ),
        ]

    def __str__(self):
        return self.display_number

    @property
    def display_number(self) -> str:
        return f"INV-{self.number:06d}"


class InvoiceLine(GymOwnedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=200)
    amount_rwf = models.PositiveIntegerField()
    membership = models.ForeignKey(
        Membership, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines"
    )
    # Set when the line is for a package counted in visits (15 swimming tickets).
    ticketbook = models.ForeignKey(
        Ticketbook, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines"
    )
    # Set when the line is for single sessions of a service (sauna, massage...).
    service = models.ForeignKey(
        Service, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines"
    )

    def __str__(self):
        return self.description


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    MTN_MOMO = "mtn_momo", "MTN Mobile Money"
    AIRTEL_MONEY = "airtel_money", "Airtel Money"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    CARD = "card", "Card"


class Payment(GymOwnedModel):
    """
    Money received from a member.

    Recorded by staff. For mobile money, bank and card the transaction
    reference is kept, but nothing here is checked with the provider yet.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.PositiveIntegerField()  # the receipt number
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="payments")
    amount_rwf = models.PositiveIntegerField()
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    reference = models.CharField(max_length=60, blank=True)
    received_on = models.DateField(default=timezone.localdate)
    received_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    idempotency_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "number"], name="unique_receipt_number_per_gym"),
            models.UniqueConstraint(
                fields=["gym", "idempotency_key"], name="unique_payment_key_per_gym"
            ),
            models.CheckConstraint(condition=Q(amount_rwf__gt=0), name="payment_amount_positive"),
        ]

    def __str__(self):
        return self.display_number

    @property
    def display_number(self) -> str:
        return f"RCT-{self.number:06d}"

    @property
    def is_reversed(self) -> bool:
        return hasattr(self, "reversal")


class Allocation(GymOwnedModel):
    """Says how much of a payment went towards an invoice."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount_rwf = models.PositiveIntegerField()


class Reversal(GymOwnedModel):
    """Cancels a payment that was recorded wrongly. The payment itself stays visible."""

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="reversal")
    reason = models.CharField(max_length=300)
    reversed_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class LedgerKind(models.TextChoices):
    INVOICE_ISSUED = "invoice_issued", "Invoice"
    PAYMENT_RECEIVED = "payment_received", "Payment"
    PAYMENT_REVERSED = "payment_reversed", "Payment reversed"


class LedgerEntry(GymOwnedModel):
    """
    One line in a member's statement.

    `amount_rwf` is how the line changes what the member owes: an invoice adds
    to it (positive), a payment takes away from it (negative), and reversing a
    payment adds it back. The sum of a member's lines is their balance.
    """

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="ledger_entries")
    kind = models.CharField(max_length=20, choices=LedgerKind.choices)
    amount_rwf = models.BigIntegerField()
    invoice = models.ForeignKey(Invoice, null=True, on_delete=models.PROTECT, related_name="+")
    payment = models.ForeignKey(Payment, null=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "ledger entries"
        ordering = ["id"]


class ShiftClose(GymOwnedModel):
    """
    A receptionist closes their shift: what they received since the last close
    (or since midnight), by method. The cash part is what they hand over.
    Permanent, like every money record.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="+")
    started_at = models.DateTimeField()
    closed_at = models.DateTimeField(default=timezone.now)
    cash_rwf = models.PositiveIntegerField()
    mobile_money_rwf = models.PositiveIntegerField()
    other_rwf = models.PositiveIntegerField()
    payments = models.PositiveIntegerField()

    # What was still open when the shift ended. Closing a shift settles none of
    # it: these are counted so the owner can see what one shift handed to the
    # next, and so a desk that leaves work behind every evening shows up.
    left_signups = models.PositiveIntegerField(default=0)
    left_massages = models.PositiveIntegerField(default=0)
    left_part_paid = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-closed_at"]

    @property
    def total_rwf(self) -> int:
        return self.cash_rwf + self.mobile_money_rwf + self.other_rwf

    @property
    def left_open(self) -> int:
        return self.left_signups + self.left_massages + self.left_part_paid
