"""
Massage: the types a gym offers, who gives them, and the bookings.

A massage is unlike a class. It is one person with one masseuse, in a slot
that nobody else can have, and the type of massage decides both how long it
takes and what it costs. That is why it has its own tables rather than sharing
the class timetable.

Each massage type is also a service the gym sells, so the door price and the
price a company agreed in its contract are kept in one place: the service.
"""

import uuid

from django.db import models
from django.utils import timezone

from apps.classes.models import TrainerProfile
from apps.corporate.models import Employee
from apps.gyms.models import Branch, GymOwnedModel
from apps.members.models import Member, Service
from apps.people.models import Person


class MassageType(GymOwnedModel):
    """
    One kind of massage, for example "Deep tissue, 60 minutes".

    The length and the price belong to the type, not to the booking: that is
    what the receptionist picks, and everything else follows from it.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=80)
    minutes = models.PositiveSmallIntegerField(default=60)

    # The service this type is sold as. The door price lives there, and so do
    # the prices each company agreed in its contract.
    service = models.OneToOneField(Service, on_delete=models.PROTECT, related_name="massage_type")

    what_to_expect = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_massage_type_per_gym"),
            models.CheckConstraint(
                condition=models.Q(minutes__gte=5, minutes__lte=240),
                name="massage_length_is_sensible",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def price_rwf(self) -> int:
        return self.service.price_rwf

    @property
    def label(self) -> str:
        return f"{self.name} · {self.minutes} min"


class MassageRule(GymOwnedModel):
    """
    How this gym runs its massage bookings. One row per gym.

    `cut_off_minutes` is how long before the start a booking may still be
    called off for nothing. After that the slot is held and the full price is
    charged even if nobody comes, because the masseuse turned other people away.

    `gap_minutes` is the break kept between two massages.
    """

    cut_off_minutes = models.PositiveSmallIntegerField(default=120)
    gap_minutes = models.PositiveSmallIntegerField(default=10)
    reminder_minutes = models.PositiveSmallIntegerField(default=10)
    updated_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)


class BookingStatus(models.TextChoices):
    REQUESTED = "requested", "Waiting for the gym"
    ACCEPTED = "accepted", "Booked"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Called off"
    NO_SHOW = "no_show", "Did not come"
    DONE = "done", "Done"


class MassageBooking(GymOwnedModel):
    """
    One massage, for one person, at one time, with one masseuse.

    Nothing is held until the gym accepts: a request is only a request. Once
    accepted the slot is taken and no second booking can have it.

    The person is either one of the gym's members or a member of staff at a
    company that pays for them. Whoever pays, the price is copied here when the
    booking is made, so a later change to a price list cannot rewrite it.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    massage_type = models.ForeignKey(MassageType, on_delete=models.PROTECT, related_name="+")
    type_name = models.CharField(max_length=80)
    minutes = models.PositiveSmallIntegerField()
    price_rwf = models.PositiveIntegerField()

    masseuse = models.ForeignKey(TrainerProfile, on_delete=models.PROTECT, related_name="massages")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="+")
    member = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.PROTECT, related_name="massages"
    )
    employee = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="massages"
    )
    # Who pays, in words, as it was when the booking was made.
    paid_by = models.CharField(max_length=80, blank=True)

    held_on = models.DateField()
    starts_at = models.TimeField()

    status = models.CharField(
        max_length=10, choices=BookingStatus.choices, default=BookingStatus.REQUESTED
    )
    asked_at = models.DateTimeField(default=timezone.now)
    asked_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancel_reason = models.CharField(max_length=200, blank=True)
    # True when it was called off too late to give the slot to anyone else.
    charged_anyway = models.BooleanField(default=False)

    # The masseuse's own note about the person, for next time. Nobody else
    # sees it: not the receptionist, not the owner, not the company.
    note = models.CharField(max_length=300, blank=True)
    note_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["held_on", "starts_at", "id"]
        indexes = [models.Index(fields=["gym", "held_on"])]

    def __str__(self):
        return f"{self.type_name} for {self.person.full_name} on {self.held_on}"

    @property
    def holds_the_slot(self) -> bool:
        """Whether this booking stops anyone else having the time."""
        return self.status in (BookingStatus.ACCEPTED, BookingStatus.DONE, BookingStatus.NO_SHOW)

    @property
    def waiting(self) -> bool:
        return self.status == BookingStatus.REQUESTED

    @property
    def who(self) -> str:
        return self.person.full_name
