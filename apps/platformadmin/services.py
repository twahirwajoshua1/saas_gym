"""
Business rules for running the platform: registering gyms with their owner,
switching applications on and off, and suspending a gym.

Only platform staff (`is_staff`) may do these things. Their pages work across
all gyms on purpose: they are a recorded bypass of tenant isolation (see
decision 0005), and every change here is written to the gym's audit log with
the platform person as the actor.
"""

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.audit import services as audit
from apps.gyms import services as gyms
from apps.gyms.models import Gym, Role, StaffAssignment
from apps.members.models import Member
from apps.modules import services as modules
from apps.people import services as people
from apps.people.phones import InvalidPhoneNumber, normalize_phone


class PlatformProblem(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def require_platform_staff(user) -> None:
    if not (user and user.is_authenticated and user.is_staff and user.is_active):
        raise PermissionDenied("Only platform staff may do this.")


@dataclass
class NewGym:
    gym: Gym
    owner: object
    owner_temporary_password: str | None


def gyms_with_counts() -> list[dict]:
    """Every gym with its owners and simple counts. No members' personal data."""
    member_counts = dict(
        Member.objects.values("gym_id").annotate(n=Count("id")).values_list("gym_id", "n")
    )
    owners = {}
    for a in StaffAssignment.objects.filter(role=Role.OWNER, is_active=True).select_related(
        "person"
    ):
        owners.setdefault(a.gym_id, []).append(a.person.full_name)
    return [
        {
            "gym": gym,
            "members": member_counts.get(gym.id, 0),
            "owners": owners.get(gym.id, []),
            "modules": len(modules.enabled_codes(gym)),
        }
        for gym in Gym.objects.order_by("name")
    ]


@transaction.atomic
def register_gym(*, user, gym_name: str, owner_name: str, owner_phone: str) -> NewGym:
    """
    Register a gym with its main branch and its owner. If the owner has no login
    yet, one is made with a temporary password, returned to be shown once.
    """
    require_platform_staff(user)
    try:
        phone = normalize_phone(owner_phone)
    except InvalidPhoneNumber:
        raise PlatformProblem("phone_invalid") from None
    owner = people.find_person_by_phone(phone)
    if owner is None:
        try:
            owner = people.create_person(full_name=owner_name, phone=phone)
        except people.InvalidPersonDetails:
            raise PlatformProblem("owner_name_required") from None
    try:
        gym = gyms.create_gym(name=gym_name, owner=owner, actor=user.person)
    except gyms.InvalidGymDetails:
        raise PlatformProblem("gym_name_required") from None

    password = None
    if not User.objects.filter(person=owner).exists():
        password = accounts.generate_temporary_password()
        accounts.create_user_account(
            person=owner, password=password, actor=user.person, gym=gym, temporary=True
        )
    return NewGym(gym=gym, owner=owner, owner_temporary_password=password)


def set_module(*, user, gym: Gym, code: str, on: bool) -> bool:
    """Switch an application on or off for a gym, with the usual dependency rules."""
    require_platform_staff(user)
    if on:
        return modules.enable_module(gym=gym, code=code, actor=user.person)
    return modules.disable_module(gym=gym, code=code, actor=user.person)


@transaction.atomic
def set_gym_active(*, user, gym: Gym, active: bool, reason: str = "") -> Gym:
    """
    Suspend a gym (nobody can open it; nothing is deleted) or restore it.
    A reason is required to suspend.
    """
    require_platform_staff(user)
    reason = " ".join((reason or "").split())
    if not active and not reason:
        raise PlatformProblem("reason_required")
    if gym.is_active == active:
        return gym
    gym.is_active = active
    gym.save(update_fields=["is_active"])
    audit.record(
        action="gym.restored" if active else "gym.suspended",
        summary=f"{gym.name} was {'restored' if active else 'suspended'}"
        + (f": {reason[:120]}" if reason else ""),
        gym=gym,
        actor=user.person,
        target=gym,
        details={"reason": reason[:300]},
    )
    return gym
