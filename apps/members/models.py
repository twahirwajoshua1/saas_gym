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


class PackageKind(models.TextChoices):
    TIME = "time", "For a length of time"
    COUNT = "count", "A number of visits"


class Package(GymOwnedModel):
    """
    Something the gym sells. A package is three things: which services it
    includes, a rule, and a price.

    The rule is one of two kinds.

      time   Use the included services as often as you like for a length of
             time, for example "Gym + swimming, monthly". This is a membership.
      count  A number of visits, for example "15 swimming tickets". The visits
             may run out after a length of time, or never. This is a ticketbook.

    Packages are never deleted. One that is no longer sold is archived
    (is_active = False) so that past sales keep their history.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=80)

    # Whole Rwandan francs. Money is never stored as a decimal fraction.
    price_rwf = models.PositiveIntegerField()

    kind = models.CharField(max_length=5, choices=PackageKind.choices, default=PackageKind.TIME)

    # What the buyer may use. A package with no services included sells nothing,
    # so every package has at least one.
    included_services = models.ManyToManyField(
        "members.Service", through="members.PackageService", related_name="packages"
    )

    # time: how long the membership lasts, for example 3 + month.
    # count: how long until unused visits run out. Both empty means never.
    duration_count = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_unit = models.CharField(max_length=5, choices=DurationUnit.choices, blank=True)

    # count only: how many visits are bought.
    visit_count = models.PositiveSmallIntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price_rwf", "name"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_package_name_per_gym"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="time",
                        duration_count__isnull=False,
                        duration_unit__gt="",
                        visit_count__isnull=True,
                    )
                    | models.Q(kind="count", visit_count__isnull=False)
                ),
                name="package_rule_matches_kind",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_count(self) -> bool:
        return self.kind == PackageKind.COUNT

    @property
    def expires(self) -> bool:
        """Whether a ticketbook from this package runs out. Time packages always do."""
        return self.duration_count is not None and bool(self.duration_unit)

    @property
    def duration_label(self) -> str:
        if not self.expires:
            return "never expires"
        plural = "" if self.duration_count == 1 else "s"
        return f"{self.duration_count} {self.duration_unit}{plural}"

    @property
    def rule_label(self) -> str:
        """The rule in words, for example "15 visits, expires in 3 months"."""
        if not self.is_count:
            return f"{self.duration_label}, as often as you like"
        if not self.expires:
            return f"{self.visit_count} visits, never expires"
        return f"{self.visit_count} visits, expires in {self.duration_label}"


class PackageService(GymOwnedModel):
    """One service a package includes. The owner picks these in Settings."""

    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="service_links")
    service = models.ForeignKey("members.Service", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["package", "service"], name="one_service_row_per_package"
            ),
        ]


class Service(GymOwnedModel):
    """
    A single session the gym sells, for example "Sauna, RWF 5,000".

    Members pay per use (no membership needed). Company contracts give each
    service their own price. Like packages, services are archived, never deleted.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=80)
    price_rwf = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_service_name_per_gym"),
        ]

    def __str__(self):
        return self.name


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

    # The secret part of the member's card link (/card/<token>/). Anyone with
    # the link can see the card, so it is long and random, and can be renewed.
    card_token = models.CharField(max_length=64, unique=True, null=True, blank=True)

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


# Memberships long enough that the gym follows up on renewing them.
RENEWABLE_UNITS = (DurationUnit.MONTH, DurationUnit.YEAR)


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

    # Which services this membership lets the member use, as the package listed
    # them on the day of the sale. Changing the package later does not take a
    # service away from someone who already paid for it.
    included_services = models.ManyToManyField(
        "members.Service", through="members.MembershipService", related_name="+"
    )

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
    def is_renewable(self) -> bool:
        """
        Whether staff should chase a renewal. Day and week passes are bought
        visit by visit, so they are left out of renewal lists and warnings.
        """
        return self.duration_unit in RENEWABLE_UNITS

    @property
    def days_left(self) -> int:
        """Days remaining including today. Zero or less means it has ended."""
        return (self.end_date - timezone.localdate()).days + 1


class MembershipService(GymOwnedModel):
    """One service a membership lets the member use, copied at the sale."""

    membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="service_links"
    )
    service = models.ForeignKey("members.Service", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "service"], name="one_service_row_per_membership"
            ),
        ]


class Ticketbook(GymOwnedModel):
    """
    A member's prepaid visits: one purchase of a package counted in visits,
    for example "15 swimming tickets".

    Like a membership it keeps its own copy of the package's name, price and
    rule as they were on the day of the sale. Visits used are never deleted:
    a mistake is corrected by cancelling one use, with a reason.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="ticketbooks")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="+")

    # Copied from the package at the moment of sale.
    package_name = models.CharField(max_length=80)
    price_rwf = models.PositiveIntegerField()
    visits_allowed = models.PositiveSmallIntegerField()

    included_services = models.ManyToManyField(
        "members.Service", through="members.TicketbookService", related_name="+"
    )

    bought_on = models.DateField(default=timezone.localdate)
    # The last day the visits may be used. Empty means they never run out.
    expires_on = models.DateField(null=True, blank=True)

    sold_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-bought_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(visits_allowed__gt=0), name="ticketbook_has_visits"
            ),
        ]

    def __str__(self):
        return f"{self.package_name}: {self.visits_left} of {self.visits_allowed} left"

    @property
    def visits_used(self) -> int:
        return self.uses.filter(cancelled_at__isnull=True).count()

    @property
    def visits_left(self) -> int:
        return max(self.visits_allowed - self.visits_used, 0)

    def is_usable_on(self, day) -> bool:
        """Whether a visit may still be taken from this book on that day."""
        if self.cancelled_at is not None or self.visits_left == 0:
            return False
        if self.expires_on is not None and day > self.expires_on:
            return False
        return day >= self.bought_on

    @property
    def expiry_label(self) -> str:
        return "never expires" if self.expires_on is None else f"until {self.expires_on:%d %b %Y}"


class TicketbookService(GymOwnedModel):
    """One service a ticketbook's visits may be spent on, copied at the sale."""

    ticketbook = models.ForeignKey(
        Ticketbook, on_delete=models.PROTECT, related_name="service_links"
    )
    service = models.ForeignKey("members.Service", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticketbook", "service"], name="one_service_row_per_ticketbook"
            ),
        ]


class TicketUse(GymOwnedModel):
    """
    One visit taken from a ticketbook.

    These are never deleted. A use recorded by mistake is cancelled with a
    reason, and the visit goes back to the member.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    ticketbook = models.ForeignKey(Ticketbook, on_delete=models.PROTECT, related_name="uses")
    service = models.ForeignKey("members.Service", on_delete=models.PROTECT, related_name="+")
    service_name = models.CharField(max_length=80)

    used_on = models.DateField(default=timezone.localdate)
    used_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cancel_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-used_at", "-id"]
        indexes = [models.Index(fields=["gym", "used_on"])]

    def __str__(self):
        return f"{self.service_name} on {self.used_on}"


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


class SignUpStatus(models.TextChoices):
    WAITING = "waiting", "Waiting for payment"
    JOINED = "joined", "Joined"
    DROPPED = "dropped", "Did not come"


class SignUp(GymOwnedModel):
    """
    Somebody who filled in the gym's sign-up page on their own phone.

    Nothing is a membership yet. The person walks to reception, says their
    code, pays, and only then does the receptionist turn this into a member.
    That is the honest order while the system cannot take money itself.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.CharField(max_length=4)

    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=16)
    gender = models.CharField(max_length=6, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=16, blank=True)

    branch = models.ForeignKey(
        "gyms.Branch", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="+")
    package_name = models.CharField(max_length=80)
    price_rwf = models.PositiveIntegerField()

    # Ticked on the form: may the gym send greetings and offers? Birthday
    # messages and promotions only go to people who said yes.
    wants_messages = models.BooleanField(default=False)

    status = models.CharField(
        max_length=7, choices=SignUpStatus.choices, default=SignUpStatus.WAITING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    member = models.ForeignKey(
        Member, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["gym", "status"])]

    def __str__(self):
        return f"{self.full_name} ({self.code})"
