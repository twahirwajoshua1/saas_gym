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
