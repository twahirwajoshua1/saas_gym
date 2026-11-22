import uuid

from django.db import models
from django.utils import timezone

from apps.gyms.models import Branch, GymOwnedModel
from apps.members.models import Member, Membership
from apps.people.models import Person


class CheckInMethod(models.TextChoices):
    MANUAL = "manual", "Search at the desk"
    QR = "qr", "QR code"
    TABLET = "tablet", "Scanned themselves at the entrance tablet"


class CheckIn(GymOwnedModel):
    """
    A member's visit on one day.

    There is at most one per member per day: checking in again the same day
    finds this record instead of making another.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="check_ins")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")

    # The membership that made the visit allowed.
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")

    checked_in_at = models.DateTimeField(default=timezone.now)
    # The local calendar day, kept separately so "one per day" can be enforced.
    checked_in_on = models.DateField(default=timezone.localdate)

    method = models.CharField(
        max_length=10, choices=CheckInMethod.choices, default=CheckInMethod.MANUAL
    )
    admitted_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-checked_in_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "member", "checked_in_on"], name="one_check_in_per_member_per_day"
            ),
        ]
        indexes = [models.Index(fields=["gym", "checked_in_on"])]

    def __str__(self):
        return f"{self.member} on {self.checked_in_on}"
