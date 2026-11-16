import uuid

from django.db import models

from apps.people.models import Person


class Gym(models.Model):
    """
    A gym business using the platform.

    A gym is a "tenant": it owns a set of data (branches, staff and, later,
    members and payments) that no other gym may ever see.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=150)

    # Switching this off blocks all access to the gym without deleting anything.
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class GymOwnedQuerySet(models.QuerySet):
    def for_gym(self, gym):
        """Only the rows that belong to this gym."""
        return self.filter(gym=gym)


class GymOwnedModel(models.Model):
    """
    The base for every table whose rows belong to one gym.

    Rule for the whole project: code that reads gym-owned data always starts
    with `.for_gym(gym)`, for example `Branch.objects.for_gym(request.gym)`.
    """

    gym = models.ForeignKey(Gym, on_delete=models.PROTECT, related_name="+")

    objects = GymOwnedQuerySet.as_manager()

    class Meta:
        abstract = True


class Branch(GymOwnedModel):
    """A location of a gym. Every gym has at least one."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "branches"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_branch_name_per_gym"),
        ]

    def __str__(self):
        return self.name


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    BRANCH_MANAGER = "branch_manager", "Branch manager"
    RECEPTIONIST = "receptionist", "Receptionist"
    SALES_REP = "sales_rep", "Sales representative"
    TRAINER = "trainer", "Trainer"


class StaffAssignment(GymOwnedModel):
    """Says that a person works at a gym, and in which role."""

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="staff_assignments")
    role = models.CharField(max_length=20, choices=Role.choices)

    # Empty means the person works across all branches of the gym.
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, null=True, blank=True)

    # Switching this off removes the person's access at once, and keeps the history.
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "person"], name="one_assignment_per_person_per_gym"
            ),
        ]

    def __str__(self):
        return f"{self.person.full_name}: {self.get_role_display()} at {self.gym.name}"
