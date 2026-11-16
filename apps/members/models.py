import uuid

from django.db import models
from django.utils import timezone

from apps.gyms.models import Branch, GymOwnedModel
from apps.people.models import Person


class DurationUnit(models.TextChoices):
    DAY = "day", "day"
    WEEK = "week", "week"
    MONTH = "month", "month"
    YEAR = "year", "year"


class Package(GymOwnedModel):
    """
    Something the gym sells, for example "Monthly, RWF 30,000, 1 month".

    Packages are never deleted. One that is no longer sold is archived
    (is_active = False) so that past sales keep their history.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=80)

    # Whole Rwandan francs. Money is never stored as a decimal fraction.
    price_rwf = models.PositiveIntegerField()

    # How long the membership lasts: a count and a unit, for example 3 + month.
    duration_count = models.PositiveSmallIntegerField()
    duration_unit = models.CharField(max_length=5, choices=DurationUnit.choices)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price_rwf", "name"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_package_name_per_gym"),
        ]

    def __str__(self):
        return self.name

    @property
    def duration_label(self) -> str:
        plural = "" if self.duration_count == 1 else "s"
        return f"{self.duration_count} {self.duration_unit}{plural}"


class Gender(models.TextChoices):
    FEMALE = "female", "Female"
    MALE = "male", "Male"


class Member(GymOwnedModel):
    """
    A person as a member of one gym.

    The shared Person record is the platform identity (the phone number).
    Everything here belongs to the gym: the name as this gym wrote it, the home
    branch, emergency contact and notes. Another gym that has the same person as
    a member cannot see or change any of it.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="member_records")

    full_name = models.CharField(max_length=150)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")

    gender = models.CharField(max_length=6, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=16, blank=True)
    notes = models.TextField(blank=True)

    joined_on = models.DateField(default=timezone.localdate)
    registered_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "person"], name="one_member_record_per_gym"),
        ]

    def __str__(self):
        return self.full_name

    @property
    def phone(self) -> str:
        return self.person.phone


class MembershipStatus(models.TextChoices):
    UPCOMING = "upcoming", "Starts later"
    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class Membership(GymOwnedModel):
    """
    One purchase of a package by a member: valid from start_date to end_date.

    It keeps its own copy of the package's name, price and length as they were
    on the day of the sale, so changing the package later does not rewrite history.

    Whether it is active or expired is worked out from the dates each time it is
    asked, so memberships expire by themselves with nothing to run overnight.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="memberships")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="+")

    # Copied from the package at the moment of sale.
    package_name = models.CharField(max_length=80)
    price_rwf = models.PositiveIntegerField()
    duration_count = models.PositiveSmallIntegerField()
    duration_unit = models.CharField(max_length=5, choices=DurationUnit.choices)

    start_date = models.DateField()
    end_date = models.DateField()  # the last valid day

    sold_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)

    # True for a membership brought in from the gym's previous records (for
    # example a spreadsheet) rather than sold in this system. It has no invoice
    # and does not count as a sale.
    imported = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="membership_ends_on_or_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.package_name}: {self.start_date} to {self.end_date}"

    def status_on(self, day) -> str:
        if self.cancelled_at is not None:
            return MembershipStatus.CANCELLED
        if day < self.start_date:
            return MembershipStatus.UPCOMING
        if day > self.end_date:
            return MembershipStatus.EXPIRED
        return MembershipStatus.ACTIVE

    @property
    def status(self) -> str:
        return self.status_on(timezone.localdate())

    @property
    def status_label(self) -> str:
        return MembershipStatus(self.status).label

    @property
    def days_left(self) -> int:
        """Days remaining including today. Zero or less means it has ended."""
        return (self.end_date - timezone.localdate()).days + 1


class MembershipFreeze(GymOwnedModel):
    """
    A pause in a membership, for example while the member travels or recovers
    from an injury. During a freeze the member may not come in, and the
    membership's end date is moved later by the length of the freeze.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="freezes")
    start_date = models.DateField()
    end_date = models.DateField()  # the last frozen day
    reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    # Set when the freeze was ended before its planned end.
    ended_early_at = models.DateTimeField(null=True, blank=True)
    # True when it was called off before it began. It is kept as history only.
    cancelled = models.BooleanField(default=False)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="freeze_ends_on_or_after_start",
            ),
        ]

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1
