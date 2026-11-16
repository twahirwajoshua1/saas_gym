"""Business rules for gyms, branches and staff."""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit import services as audit
from apps.gyms.models import Branch, Gym, Role, StaffAssignment
from apps.people.models import Person

MAIN_BRANCH_NAME = "Main branch"


class InvalidGymDetails(ValueError):
    """The details given are incomplete or do not fit together."""


class AlreadyAssigned(Exception):
    """This person already works at this gym."""


class GymLookupError(Exception):
    """Base for problems finding a gym."""


class GymNotFound(GymLookupError):
    """No gym matches."""


class AmbiguousGym(GymLookupError):
    """More than one gym has that name."""


def _clean_name(name: str, what: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise InvalidGymDetails(f"A {what} needs a name.")
    return name


def find_gym(identifier: str) -> Gym:
    """
    Find a gym by its id or its name (capitals do not matter).

    Raises GymNotFound, or AmbiguousGym if several gyms share the name.
    """
    identifier = (identifier or "").strip()
    try:
        return Gym.objects.get(public_id=uuid.UUID(identifier))
    except (ValueError, Gym.DoesNotExist):
        pass

    matches = list(Gym.objects.filter(name__iexact=identifier))
    if not matches:
        raise GymNotFound(f"No gym is called '{identifier}'.")
    if len(matches) > 1:
        ids = ", ".join(str(g.public_id) for g in matches)
        raise AmbiguousGym(f"Several gyms are called '{identifier}'. Use an id instead: {ids}")
    return matches[0]


@transaction.atomic
def create_gym(*, name: str, owner: Person, actor: Person | None = None) -> Gym:
    """
    Register a new gym with its first branch and its owner.

    All of it, including the audit entry, is saved together or not at all, so a
    gym can never exist without a branch, an owner or a record of its creation.
    """
    gym = Gym.objects.create(name=_clean_name(name, "gym"))
    Branch.objects.create(gym=gym, name=MAIN_BRANCH_NAME)
    StaffAssignment.objects.create(gym=gym, person=owner, role=Role.OWNER)
    audit.record(
        action="gym.created",
        summary=f"{gym.name} was registered with {owner.full_name} as owner",
        gym=gym,
        actor=actor,
        target=gym,
        details={"owner": owner.public_id},
    )
    return gym


@transaction.atomic
def add_branch(*, gym: Gym, name: str, actor: Person | None = None) -> Branch:
    """Add another location to a gym."""
    name = _clean_name(name, "branch")
    if Branch.objects.for_gym(gym).filter(name__iexact=name).exists():
        raise InvalidGymDetails(f"{gym.name} already has a branch called {name}.")
    branch = Branch.objects.create(gym=gym, name=name)
    audit.record(
        action="branch.added",
        summary=f"Branch '{branch.name}' was added to {gym.name}",
        gym=gym,
        actor=actor,
        target=branch,
    )
    return branch


@transaction.atomic
def assign_staff(
    *,
    gym: Gym,
    person: Person,
    role: str,
    branch: Branch | None = None,
    actor: Person | None = None,
) -> StaffAssignment:
    """Give a person a role at a gym, optionally limited to one branch."""
    if role not in Role.values:
        raise InvalidGymDetails(f"'{role}' is not a role.")
    if branch is not None and branch.gym_id != gym.id:
        raise InvalidGymDetails("That branch belongs to a different gym.")
    if StaffAssignment.objects.for_gym(gym).filter(person=person).exists():
        raise AlreadyAssigned(f"{person.full_name} already works at {gym.name}.")
    assignment = StaffAssignment.objects.create(gym=gym, person=person, role=role, branch=branch)
    audit.record(
        action="staff.assigned",
        summary=f"{person.full_name} was made {assignment.get_role_display()} at {gym.name}",
        gym=gym,
        actor=actor,
        target=assignment,
        details={
            "person": person.public_id,
            "role": role,
            "branch": branch.public_id if branch else None,
        },
    )
    return assignment


@transaction.atomic
def remove_staff(*, assignment: StaffAssignment, actor: Person | None = None) -> None:
    """Take away a person's access to a gym. The record is kept as history."""
    assignment.is_active = False
    assignment.save(update_fields=["is_active"])
    audit.record(
        action="staff.removed",
        summary=(
            f"{assignment.person.full_name} ({assignment.get_role_display()}) "
            f"lost access to {assignment.gym.name}"
        ),
        gym=assignment.gym,
        actor=actor,
        target=assignment,
        details={"person": assignment.person.public_id, "role": assignment.role},
    )


def active_assignments_for(person: Person):
    """Every gym this person can currently work in, with their role there."""
    return (
        StaffAssignment.objects.filter(person=person, is_active=True, gym__is_active=True)
        # Not the branch: it is only visible once a gym has been chosen, and a
        # join would silently turn "limited to a branch" into "no branch".
        .select_related("gym")
        .order_by("gym__name")
    )


def find_active_assignment(*, person: Person, gym_public_id) -> StaffAssignment | None:
    """This person's current role at one particular gym, or None if they have none."""
    try:
        return active_assignments_for(person).filter(gym__public_id=gym_public_id).first()
    except (ValueError, TypeError, ValidationError):
        # The identifier was missing or not a properly formed one.
        return None
