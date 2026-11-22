"""
The front desk and the entrance tablet.

  EntranceTablet  a tablet at a branch's entrance where members and company
                  staff scan themselves in. Connected once with a short code;
                  after that it carries a secret (a cookie) that identifies it.
  ScanProblem     something the tablet could not let through (membership
                  ended, unknown card...). It appears on the desk under
                  "Needs you" until someone deals with it.
"""

import uuid

from django.db import models
from django.utils import timezone

from apps.corporate.models import Employee
from apps.gyms.models import Branch, GymOwnedModel
from apps.members.models import Member
from apps.people.models import Person


class EntranceTablet(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=60)
    # A 6-digit code typed on the tablet once, to connect it. Short-lived.
    pairing_code = models.CharField(max_length=6, blank=True)
    pairing_expires_at = models.DateTimeField(null=True, blank=True)
    # A hash of the secret the tablet keeps. Empty until connected.
    secret_hash = models.CharField(max_length=64, blank=True)
    paired_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    # Switching this off disconnects the tablet at once (lost, stolen, replaced).
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["branch__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.branch})"

    @property
    def is_connected(self) -> bool:
        return self.is_active and bool(self.secret_hash)


class ProblemKind(models.TextChoices):
    MEMBERSHIP_ENDED = "ended", "Membership ended"
    NO_MEMBERSHIP = "no_membership", "No membership"
    NOT_STARTED = "not_started", "Membership not started yet"
    FROZEN = "frozen", "Membership frozen"
    UNKNOWN_CARD = "unknown", "Card not recognised"
    NOT_ON_LIST = "removed", "No longer on the company's list"
    NO_CONTRACT = "no_contract", "Company has no running contract"
    PIN_LOCKED = "pin_locked", "Too many wrong PINs"


class ScanProblem(GymOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=16, choices=ProblemKind.choices)
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.PROTECT)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(Person, null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
