"""
Corporate wellness: a company pays for its employees' visits to gyms it has a
contract with.

  Company          the employer. Created by the platform; run by its logistics officer.
  CompanyOfficer   a person who manages a company's employee list and bills.
  Employee         someone on the company's list. Only listed employees get in.
  Contract         between one company and one gym: dates, payment terms, and a
                   price for each service at each branch. The company approves it.
  CorporateVisit   an employee's visit to a gym on one day.
  VisitActivity    one service used during a visit (gym, sauna, massage...).
  Flag             a warning on a visit that both sides see until it is settled.
  CorporateBill    the monthly bill from one gym to one company. Permanent.
  BillLine         one activity on a bill (or a credit taking one back). Permanent.
  BillPayment      a bank transfer received for a bill. Permanent.
  PaymentReversal  cancels a payment recorded by mistake. Permanent.
  Credit           a billed activity the gym agreed to take back; it appears as
                   a minus line on the next bill. Permanent.
  CompanyLogEntry  what was done on the company's side, by whom. Permanent.

Who sees what is enforced by the database (see migration 0002): a gym sees its
own contracts, visits and bills, and the employees of companies it has a
contract with; a company sees its own employees and everything gyms record
for it, at every gym.
"""

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.gyms.models import Branch, GymOwnedModel
from apps.members.models import Service
from apps.people.models import Person


class Company(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=150)
    # Short code the company gives to gyms so they can propose a contract,
    # for example "CO-7KQ2MX". It reveals nothing but the company's name.
    code = models.CharField(max_length=12, unique=True)
    tin = models.CharField("TIN", max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class CompanyOfficer(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="officers")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="company_roles")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "person"], name="one_officer_role_each"),
        ]

    def __str__(self):
        return f"{self.person.full_name}: officer of {self.company.name}"


class Employee(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employees")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="employments")
    employee_number = models.CharField(max_length=30)
    department = models.CharField(max_length=80, blank=True)

    # The secret in the employee's card link.
    card_token = models.CharField(max_length=64, unique=True)
    # The phone the card was first opened on (a hash of a secret kept on it).
    device_hash = models.CharField(max_length=64, blank=True)
    # For employees without a smartphone: a 4-digit PIN asked at the desk.
    pin_hash = models.CharField(max_length=128, blank=True)

    # Removing an employee keeps their history; they simply cannot check in.
    is_active = models.BooleanField(default=True)
    added_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["person__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "employee_number"], name="unique_employee_number"
            ),
            models.UniqueConstraint(fields=["company", "person"], name="one_listing_per_person"),
        ]

    def __str__(self):
        return f"{self.person.full_name} ({self.employee_number})"

    @property
    def full_name(self) -> str:
        return self.person.full_name

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_hash)


class ContractStatus(models.TextChoices):
    PROPOSED = "proposed", "Waiting for the company's approval"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined by the company"


class Contract(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="contracts")
    start_date = models.DateField()
    end_date = models.DateField()
    # How many days the company has to pay each bill.
    payment_days = models.PositiveSmallIntegerField(default=30)
    status = models.CharField(
        max_length=10, choices=ContractStatus.choices, default=ContractStatus.PROPOSED
    )
    proposed_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    decided_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    ended_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.company} at {self.gym}"

    @property
    def last_day(self):
        return self.ended_on or self.end_date

    def is_running(self, on) -> bool:
        return self.status == ContractStatus.APPROVED and self.start_date <= on <= self.last_day


class ContractPrice(GymOwnedModel):
    """The price the company pays for one service at one branch."""

    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="prices")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="+")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    price_rwf = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "service", "branch"], name="one_price_per_service_branch"
            ),
        ]


class VisitMethod(models.TextChoices):
    PHONE = "phone", "Card on the employee's phone"
    PIN = "pin", "Printed card or search, with PIN"
    STAFF = "staff", "Found by staff, not yet confirmed"


class CorporateVisit(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="visits")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="visits")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    checked_in_at = models.DateTimeField(default=timezone.now)
    checked_in_on = models.DateField(default=timezone.localdate)
    method = models.CharField(max_length=10, choices=VisitMethod.choices)
    admitted_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    # When the employee confirmed the visit (on their phone, or with their PIN).
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-checked_in_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "employee", "checked_in_on"], name="one_corporate_visit_per_day"
            ),
        ]
        indexes = [models.Index(fields=["company", "checked_in_on"])]


class VisitActivity(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    visit = models.ForeignKey(CorporateVisit, on_delete=models.PROTECT, related_name="activities")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="+")
    service_name = models.CharField(max_length=80)
    price_rwf = models.PositiveIntegerField()  # from the contract, at the time
    added_at = models.DateTimeField(default=timezone.now)
    added_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    # A mistake at the desk is cancelled (with a reason), never deleted.
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    cancel_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["added_at"]
        constraints = [
            models.UniqueConstraint(fields=["visit", "service"], name="one_activity_per_service"),
        ]


class FlagKind(models.TextChoices):
    TWO_PLACES = "two_places", "Checked in at two places within an hour"
    DISPUTED = "disputed", "Questioned by the company"
    QUESTION = "question", "Raised by the gym"


class FlagOutcome(models.TextChoices):
    KEPT = "kept", "Kept on the bill"
    REMOVED = "removed", "Taken off the bill"


class Flag(GymOwnedModel):
    """
    A warning on a visit, visible to the gym and the company until settled.
    While open, the visit's activities stay off the bill.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    visit = models.ForeignKey(CorporateVisit, on_delete=models.PROTECT, related_name="flags")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=FlagKind.choices)
    message = models.CharField(max_length=300, blank=True)
    raised_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    raised_at = models.DateTimeField(default=timezone.now)
    outcome = models.CharField(max_length=8, choices=FlagOutcome.choices, blank=True)
    settled_note = models.CharField(max_length=300, blank=True)
    settled_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-raised_at"]

    @property
    def is_open(self) -> bool:
        return self.settled_at is None


class CorporateBill(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.PositiveIntegerField()
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="bills")
    period_start = models.DateField()
    period_end = models.DateField()
    issued_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField()
    total_rwf = models.BigIntegerField()
    issued_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_start", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "number"], name="unique_bill_number_per_gym"),
            models.UniqueConstraint(
                fields=["contract", "period_start"], name="one_bill_per_contract_per_month"
            ),
        ]

    def __str__(self):
        return self.display_number

    @property
    def display_number(self) -> str:
        return f"CB-{self.number:06d}"


class BillLine(GymOwnedModel):
    """
    A copy of what was billed, so the bill reads the same forever even if a
    name or price changes later.
    """

    bill = models.ForeignKey(CorporateBill, on_delete=models.PROTECT, related_name="lines")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    activity = models.OneToOneField(
        VisitActivity, null=True, on_delete=models.PROTECT, related_name="bill_line"
    )
    credit = models.OneToOneField(
        "Credit", null=True, on_delete=models.PROTECT, related_name="bill_line"
    )
    employee_name = models.CharField(max_length=150)
    employee_number = models.CharField(max_length=30)
    department = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=16)
    branch_name = models.CharField(max_length=150)
    service_name = models.CharField(max_length=80)
    visit_date = models.DateField()
    check_in_time = models.TimeField()
    confirmed = models.BooleanField()
    amount_rwf = models.BigIntegerField()  # negative for a credit

    class Meta:
        ordering = ["service_name", "visit_date", "check_in_time", "employee_name"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(activity__isnull=False) & Q(credit__isnull=True))
                | (Q(activity__isnull=True) & Q(credit__isnull=False)),
                name="bill_line_is_activity_or_credit",
            ),
        ]


class Credit(GymOwnedModel):
    """A billed activity the gym takes back. It goes on the next bill as a minus line."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="credits")
    original_line = models.OneToOneField(
        BillLine, on_delete=models.PROTECT, related_name="credited_by"
    )
    reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class BillPayment(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    bill = models.ForeignKey(CorporateBill, on_delete=models.PROTECT, related_name="payments")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    amount_rwf = models.PositiveIntegerField()
    reference = models.CharField(max_length=60)
    received_on = models.DateField()
    recorded_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    idempotency_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_on", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "idempotency_key"], name="unique_bill_payment_key"
            ),
            models.CheckConstraint(condition=Q(amount_rwf__gt=0), name="bill_payment_positive"),
        ]

    @property
    def is_reversed(self) -> bool:
        return hasattr(self, "reversal")


class PaymentReversal(GymOwnedModel):
    payment = models.OneToOneField(BillPayment, on_delete=models.PROTECT, related_name="reversal")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=300)
    reversed_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class CorporateSequence(GymOwnedModel):
    """The last corporate bill number a gym has used."""

    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gym"], name="one_corporate_sequence_per_gym"),
        ]


class PinAttempt(GymOwnedModel):
    """A PIN typed at a gym's desk. Too many wrong ones lock the PIN for a while."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="+")
    succeeded = models.BooleanField()
    at = models.DateTimeField(default=timezone.now)


class CompanyLogEntry(models.Model):
    """The company's own activity log (employees added or removed, approvals...)."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    actor = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=60)
    summary = models.CharField(max_length=300)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.summary
