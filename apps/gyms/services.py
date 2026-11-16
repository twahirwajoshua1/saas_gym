"""Business rules for gyms, branches and staff."""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.gyms.models import Branch, Gym, Role, StaffAssignment
from apps.people.models import Person

MAIN_BRANCH_NAME = "Main branch"


class InvalidGymDetails(ValueError):
    """The details given are incomplete or do not fit together."""


class AlreadyAssigned(Exception):
    """This person already works at this gym."""


def _clean_name(name: str, what: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise InvalidGymDetails(f"A {what} needs a name.")
    return name


@transaction.atomic
def create_gym(*, name: str, owner: Person) -> Gym:
    """
    Register a new gym with its first branch and its owner.

    All three are created together or not at all, so a gym can never exist
    without a branch or without an owner.
    """
    gym = Gym.objects.create(name=_clean_name(name, "gym"))
    Branch.objects.create(gym=gym, name=MAIN_BRANCH_NAME)
    StaffAssignment.objects.create(gym=gym, person=owner, role=Role.OWNER)
    return gym


def add_branch(*, gym: Gym, name: str) -> Branch:
    """Add another location to a gym."""
    name = _clean_name(name, "branch")
    if Branch.objects.for_gym(gym).filter(name__iexact=name).exists():
        raise InvalidGymDetails(f"{gym.name} already has a branch called {name}.")
    return Branch.objects.create(gym=gym, name=name)


def assign_staff(
    *, gym: Gym, person: Person, role: str, branch: Branch | None = None
) -> StaffAssignment:
    """Give a person a role at a gym, optionally limited to one branch."""
    if role not in Role.values:
        raise InvalidGymDetails(f"'{role}' is not a role.")
    if branch is not None and branch.gym_id != gym.id:
        raise InvalidGymDetails("That branch belongs to a different gym.")
    if StaffAssignment.objects.for_gym(gym).filter(person=person).exists():
        raise AlreadyAssigned(f"{person.full_name} already works at {gym.name}.")
    return StaffAssignment.objects.create(gym=gym, person=person, role=role, branch=branch)


def remove_staff(*, assignment: StaffAssignment) -> None:
    """Take away a person's access to a gym. The record is kept as history."""
    assignment.is_active = False
    assignment.save(update_fields=["is_active"])


def active_assignments_for(person: Person):
    """Every gym this person can currently work in, with their role there."""
    return (
        StaffAssignment.objects.filter(person=person, is_active=True, gym__is_active=True)
        .select_related("gym", "branch")
        .order_by("gym__name")
    )


def find_active_assignment(*, person: Person, gym_public_id) -> StaffAssignment | None:
    """This person's current role at one particular gym, or None if they have none."""
    try:
        return active_assignments_for(person).filter(gym__public_id=gym_public_id).first()
    except (ValueError, TypeError, ValidationError):
        # The identifier was missing or not a properly formed one.
        return None
