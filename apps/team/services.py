"""
Business rules for managing a gym's staff. Owner only.

This module joins three others: people (who the person is), gyms (their role
at this gym) and accounts (their login). It sits above gyms and accounts in
the import rules because neither of those may use the other.

Two protections matter here:

- A gym always keeps at least one active owner, and owners cannot remove or
  demote themselves, so nobody can lock a gym out by mistake.
- A login belongs to a person, not to a gym. An owner may only reset the
  password of someone who works at no other gym and does not run the platform;
  otherwise one gym's owner could take over an account used elsewhere.
"""

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.gyms import services as gyms
from apps.gyms.models import Branch, Gym, Role, StaffAssignment
from apps.gyms.permissions import OWNER_ONLY, require_role
from apps.people import services as people
from apps.people.phones import InvalidPhoneNumber, normalize_phone


class StaffProblem(Exception):
    """Something about the request cannot be done. `reason` says what."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class StaffRow:
    assignment: StaffAssignment
    has_login: bool
    last_login: object


@dataclass
class AddedStaff:
    assignment: StaffAssignment
    temporary_password: str | None  # None when the person already had a login
    reactivated: bool = False


def staff_of(gym: Gym) -> list[StaffRow]:
    """Everyone who works or worked at the gym, active first, then by name."""
    assignments = list(
        StaffAssignment.objects.for_gym(gym)
        .select_related("person", "branch")
        .order_by("-is_active", "person__full_name")
    )
    users = {
        u.person_id: u
        for u in User.objects.filter(person_id__in=[a.person_id for a in assignments])
    }
    return [
        StaffRow(
            assignment=a,
            has_login=a.person_id in users,
            last_login=users[a.person_id].last_login if a.person_id in users else None,
        )
        for a in assignments
    ]


def _check_branch(by: StaffAssignment, role: str, branch: Branch | None) -> Branch | None:
    if role not in Role.values:
        raise StaffProblem("role_invalid")
    if role == Role.OWNER:
        return None
    if branch is not None and branch.gym_id != by.gym_id:
        raise StaffProblem("branch_invalid")
    return branch


@transaction.atomic
def add_staff(
    *, by: StaffAssignment, full_name: str, phone: str, role: str, branch: Branch | None = None
) -> AddedStaff:
    """
    Give someone a role at the gym, with a login.

    If they have no login yet, one is created with a temporary password that is
    returned (to be shown once) and must be changed at the first sign-in. If
    they worked here before, their access is given back.
    """
    require_role(by, OWNER_ONLY)
    branch = _check_branch(by, role, branch)
    try:
        standard_phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        raise StaffProblem("phone_invalid") from None

    person = people.find_person_by_phone(standard_phone)
    if person is None:
        try:
            person = people.create_person(full_name=full_name, phone=standard_phone)
        except people.InvalidPersonDetails:
            raise StaffProblem("name_required") from None

    existing = StaffAssignment.objects.for_gym(by.gym).filter(person=person).first()
    if existing is not None and existing.is_active:
        raise StaffProblem("already_staff")
    if existing is not None:
        assignment = gyms.change_staff(
            assignment=existing, role=role, branch=branch, actor=by.person, reactivate=True
        )
        reactivated = True
    else:
        assignment = gyms.assign_staff(
            gym=by.gym, person=person, role=role, branch=branch, actor=by.person
        )
        reactivated = False

    password = None
    if not User.objects.filter(person=person).exists():
        password = accounts.generate_temporary_password()
        accounts.create_user_account(
            person=person, password=password, actor=by.person, gym=by.gym, temporary=True
        )
    return AddedStaff(assignment=assignment, temporary_password=password, reactivated=reactivated)


def _not_yourself(by: StaffAssignment, assignment: StaffAssignment) -> None:
    if assignment.gym_id != by.gym_id:
        raise PermissionDenied("That staff member belongs to a different gym.")
    if assignment.pk == by.pk:
        raise StaffProblem("yourself")


@transaction.atomic
def change_staff(
    *, by: StaffAssignment, assignment: StaffAssignment, role: str, branch: Branch | None
) -> StaffAssignment:
    """Change someone's role or branch. Owner only, not for yourself."""
    require_role(by, OWNER_ONLY)
    _not_yourself(by, assignment)
    if not assignment.is_active:
        raise StaffProblem("not_active")
    branch = _check_branch(by, role, branch)
    if assignment.role == Role.OWNER and role != Role.OWNER:
        if gyms.other_active_owners(assignment) == 0:
            raise StaffProblem("last_owner")
    return gyms.change_staff(assignment=assignment, role=role, branch=branch, actor=by.person)


@transaction.atomic
def remove_staff(*, by: StaffAssignment, assignment: StaffAssignment) -> None:
    """Take away someone's access. Owner only, not for yourself. History is kept."""
    require_role(by, OWNER_ONLY)
    _not_yourself(by, assignment)
    if not assignment.is_active:
        return
    if assignment.role == Role.OWNER and gyms.other_active_owners(assignment) == 0:
        raise StaffProblem("last_owner")
    gyms.remove_staff(assignment=assignment, actor=by.person)


def can_reset_password(by: StaffAssignment, assignment: StaffAssignment) -> bool:
    """See the module notes: only for people who work at this gym alone."""
    if assignment.pk == by.pk or not assignment.is_active:
        return False
    user = User.objects.filter(person=assignment.person).first()
    if user is None or user.is_staff:
        return False
    elsewhere = StaffAssignment.objects.filter(person=assignment.person, is_active=True).exclude(
        gym=by.gym
    )
    return not elsewhere.exists()


@transaction.atomic
def reset_password(*, by: StaffAssignment, assignment: StaffAssignment) -> str:
    """Give a staff member a temporary password. Owner only. Returns it, to be shown once."""
    require_role(by, OWNER_ONLY)
    if assignment.gym_id != by.gym_id:
        raise PermissionDenied("That staff member belongs to a different gym.")
    if not can_reset_password(by, assignment):
        raise StaffProblem("cannot_reset")
    user = User.objects.get(person=assignment.person)
    return accounts.set_temporary_password(user=user, actor=by.person, gym=by.gym)
