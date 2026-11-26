import uuid

from django.db import models
from django.utils import timezone

from apps.gyms.models import Branch, GymOwnedModel
from apps.members.models import Member, Membership, Service, TicketUse
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
    # The membership that let them in. Empty when they came in on a book of
    # prepaid visits instead, for example 15 swimming tickets.
    membership = models.ForeignKey(
        Membership, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

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


class GuestRule(GymOwnedModel):
    """
    How this gym treats a member's guest. One row per gym.

    free_per_month is how many friends a member may bring free in a calendar
    month. Zero means the gym gives none away: the friend pays the day price
    at reception like anybody else.
    """

    free_per_month = models.PositiveSmallIntegerField(default=1)
    updated_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)


class GuestVisit(GymOwnedModel):
    """
    A friend a member brought in.

    The guest is not a member and has not signed anything, so the gym keeps
    only what it needs to let them train and to call them back: a name, and a
    phone number if they give one. Nothing else.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    host = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="guests")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")

    guest_name = models.CharField(max_length=150)
    guest_phone = models.CharField(max_length=16, blank=True)

    came_on = models.DateField(default=timezone.localdate)
    # False means the gym expects the day price for this visit.
    was_free = models.BooleanField()

    recorded_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-came_on", "-id"]


class CheckInActivity(GymOwnedModel):
    """
    What a member said they were going to do on a visit.

    A package says which services it includes, so a member who paid for
    swimming only cannot tick the gym. Where the service comes out of a book
    of prepaid visits, the visit taken is recorded too, so the count is never
    guessed at.

    Nothing is deleted: a service ticked by mistake is taken off with a reason,
    the same way as for a company visit.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    check_in = models.ForeignKey(CheckIn, on_delete=models.PROTECT, related_name="activities")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="+")
    service_name = models.CharField(max_length=80)

    # Set when the visit came out of a book of prepaid visits.
    ticket_use = models.ForeignKey(
        TicketUse, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancel_reason = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["service_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["check_in", "service"], name="one_row_per_service_per_check_in"
            ),
        ]

    def __str__(self):
        return self.service_name
