"""
Classes, trainers and bookings.

A gym writes down what it teaches (a class type: "Aerobics", with what to
expect), puts it on the timetable at a time with a trainer and a number of
places, and members book a place from their own card. Members can also book a
one-to-one session in a trainer's free hours, and review a trainer afterwards.

Everything here belongs to one gym and is protected by row-level security as
well as `.for_gym()`.
"""

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.gyms.models import Branch, GymOwnedModel, StaffAssignment
from apps.members.models import Member


class TrainerProfile(GymOwnedModel):
    """
    What members see about a trainer: their own words, not a photo.

    One profile per staff assignment, so a trainer who also works at another
    gym has a separate profile there.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    staff = models.OneToOneField(StaffAssignment, on_delete=models.PROTECT, related_name="trainer")

    # A short line under the name, for example "Strength and conditioning".
    headline = models.CharField(max_length=120, blank=True)
    about = models.TextField(blank=True)

    # Whether members may book a one-to-one session with this trainer.
    takes_personal_sessions = models.BooleanField(default=True)
    # Masseuses are staff with a profile too: this says they give massages.
    gives_massages = models.BooleanField(default=False)
    is_listed = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def full_name(self) -> str:
        return self.staff.person.full_name

    def __str__(self) -> str:
        return self.full_name


class ClassType(GymOwnedModel):
    """
    Something the gym teaches, for example "Aerobics" or "Spinning".

    Never deleted: one no longer taught is archived, so past classes keep
    their name and description.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=80)

    # Shown to members before they book: what happens, what to bring, who it
    # suits. The gym writes this in its own words.
    what_to_expect = models.TextField(blank=True)

    minutes = models.PositiveSmallIntegerField(default=60)
    places = models.PositiveSmallIntegerField(default=20)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_class_name_per_gym")
        ]

    def __str__(self) -> str:
        return self.name


class ClassSession(GymOwnedModel):
    """
    One class at one time, at one branch: Tuesday 18:00, Aerobics, with Eric.

    Date and time are kept apart because a gym thinks in opening hours, not in
    instants, and the whole platform runs on Kigali time.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    class_type = models.ForeignKey(ClassType, on_delete=models.PROTECT, related_name="sessions")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")

    # Empty while the gym has not said who is teaching it.
    trainer = models.ForeignKey(
        TrainerProfile, null=True, blank=True, on_delete=models.PROTECT, related_name="sessions"
    )

    held_on = models.DateField()
    starts_at = models.TimeField()
    minutes = models.PositiveSmallIntegerField()
    places = models.PositiveSmallIntegerField()

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)

    created_by = models.ForeignKey(
        "people.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["held_on", "starts_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "branch", "class_type", "held_on", "starts_at"],
                name="one_class_per_branch_and_time",
            )
        ]

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    def __str__(self) -> str:
        return f"{self.class_type.name} {self.held_on} {self.starts_at:%H:%M}"


class Booking(GymOwnedModel):
    """
    A member's place in a class. Cancelling leaves the row and sets
    cancelled_at, so the gym can see who keeps booking and not coming.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session = models.ForeignKey(ClassSession, on_delete=models.PROTECT, related_name="bookings")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="class_bookings")

    booked_at = models.DateTimeField(default=timezone.now)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    came_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["booked_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["session", "member"], name="one_booking_per_member")
        ]

    @property
    def is_open(self) -> bool:
        return self.cancelled_at is None


WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class TrainerHours(GymOwnedModel):
    """
    When a trainer is free for one-to-one sessions: a weekday and a window,
    for example Monday 14:00-17:00. Members book inside these hours.
    """

    MONDAY = 0

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.PROTECT, related_name="hours")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")

    # 0 = Monday, like Python's date.weekday().
    weekday = models.PositiveSmallIntegerField(validators=[MaxValueValidator(6)])
    starts_at = models.TimeField()
    ends_at = models.TimeField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["weekday", "starts_at"]

    @property
    def weekday_name(self) -> str:
        return WEEKDAY_NAMES[self.weekday]


class PersonalSession(GymOwnedModel):
    """
    One member with one trainer at an agreed time. Booked by the member from
    the trainer's free hours; the trainer or the desk can cancel it.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    trainer = models.ForeignKey(
        TrainerProfile, on_delete=models.PROTECT, related_name="personal_sessions"
    )
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="personal_sessions")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="+")

    held_on = models.DateField()
    starts_at = models.TimeField()
    minutes = models.PositiveSmallIntegerField(default=60)

    note = models.CharField(max_length=200, blank=True)  # what the member wants to work on
    cancelled_at = models.DateTimeField(null=True, blank=True)
    booked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["held_on", "starts_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["trainer", "held_on", "starts_at"],
                name="one_personal_session_per_trainer_and_time",
            )
        ]

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None


class Review(GymOwnedModel):
    """
    A member's stars and words about a trainer, shown to everyone next to that
    trainer's classes. Only a member who attended can write one, and only one
    per class attended, so the score cannot be stuffed.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.PROTECT, related_name="reviews")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="+")
    session = models.ForeignKey(
        ClassSession, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    stars = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    words = models.TextField(blank=True)

    # Set when a manager hides a review (abuse, a real name, an insult). The
    # review is kept, not deleted, so the decision can be looked at later.
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_reason = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["member", "session"], name="one_review_per_member_and_class"
            )
        ]

    @property
    def is_shown(self) -> bool:
        return self.hidden_at is None
