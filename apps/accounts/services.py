"""Business rules for login accounts."""

import math
import secrets

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone

from apps.accounts import throttle
from apps.accounts.models import User
from apps.audit import services as audit
from apps.people import services as people_services
from apps.people.models import Person
from apps.people.phones import InvalidPhoneNumber, normalize_phone


class AccountAlreadyExists(Exception):
    """This person already has a login account."""


class TooManyAttempts(Exception):
    """Sign-in for this phone number is locked after too many wrong passwords."""

    def __init__(self, locked_until):
        self.locked_until = locked_until
        super().__init__("Too many wrong attempts.")

    @property
    def minutes_left(self) -> int:
        seconds = (self.locked_until - timezone.now()).total_seconds()
        return max(1, math.ceil(seconds / 60))


# Letters and digits that cannot be mistaken for each other when read aloud
# or copied from a screen (no 0/o, 1/l/i).
_EASY_CHARACTERS = "abcdefghjkmnpqrstuvwxyz23456789"


def generate_temporary_password() -> str:
    """A random password that is easy to read out, for example "kp7m-x3ra-9wth"."""
    groups = ["".join(secrets.choice(_EASY_CHARACTERS) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)


@transaction.atomic
def create_user_account(
    *,
    person: Person,
    password: str,
    actor: Person | None = None,
    gym=None,
    temporary: bool = False,
) -> User:
    """
    Give a person a login account, using their phone number to sign in.

    `gym` is the gym this happened in (for the audit log), if any. With
    `temporary=True` the person must choose a new password at their first sign-in.

    Raises AccountAlreadyExists, or Django's ValidationError if the password
    is too weak.
    """
    if User.objects.filter(person=person).exists():
        raise AccountAlreadyExists(f"{person} already has a login account.")

    validate_password(password)
    user = User.objects.create_user(
        phone=person.phone, password=password, person=person, must_change_password=temporary
    )
    audit.record(
        action="account.created",
        summary=f"A login account was created for {person.full_name}",
        gym=gym,
        actor=actor,
        target=user,
        details={"person": person.public_id},
    )
    return user


@transaction.atomic
def set_temporary_password(*, user: User, actor: Person, gym) -> str:
    """
    Give an account a new random password that must be changed at the next
    sign-in, and remove any sign-in lock. Returns the password, to be shown once.

    Who may do this is decided by the caller (see apps/team).
    """
    password = generate_temporary_password()
    user.set_password(password)
    user.must_change_password = True
    user.save(update_fields=["password", "must_change_password"])
    throttle.clear(user.phone)
    audit.record(
        action="account.password_reset",
        summary=f"{actor.full_name} set a temporary password for {user.person.full_name}",
        gym=gym,
        actor=actor,
        target=user,
    )
    return password


class WrongPassword(Exception):
    """The current password given is not correct."""


@transaction.atomic
def change_own_password(*, user: User, current_password: str, new_password: str) -> None:
    """
    A person changes their own password. Raises WrongPassword, or Django's
    ValidationError if the new one is too weak.
    """
    if not user.check_password(current_password):
        raise WrongPassword()
    validate_password(new_password, user=user)
    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])


def authenticate_by_phone(request, *, phone: str, password: str) -> User | None:
    """
    Return the user if the phone number and password are correct, otherwise None.

    Raises TooManyAttempts if the number is locked (see throttle.py). The
    counting itself happens in backends.py, so that every way of signing in
    is covered.
    """
    try:
        standard_phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        return None

    user = authenticate(request, username=standard_phone, password=password)
    if user is None:
        locked_until = throttle.locked_until(standard_phone)
        if locked_until:
            raise TooManyAttempts(locked_until)
    return user


@transaction.atomic
def unlock_sign_in(*, phone: str) -> bool:
    """
    Remove a sign-in lock early (a platform action). Returns False if there was none.

    Raises InvalidPhoneNumber.
    """
    standard_phone = normalize_phone(phone)
    if not throttle.clear(standard_phone):
        return False
    audit.record(
        action="account.unlocked",
        summary=f"The sign-in lock on the number ending {standard_phone[-3:]} was removed",
        details={"phone": standard_phone},
    )
    return True


@transaction.atomic
def create_platform_admin(*, full_name: str, phone: str, password: str) -> User:
    """
    Create an account for someone who runs the platform itself.

    If the person is already registered, their existing record is used.
    """
    person = people_services.find_person_by_phone(phone)
    if person is None:
        person = people_services.create_person(full_name=full_name, phone=phone)

    if User.objects.filter(person=person).exists():
        raise AccountAlreadyExists(f"{person} already has a login account.")

    validate_password(password)
    user = User.objects.create_user(
        phone=person.phone,
        password=password,
        person=person,
        is_staff=True,
        is_superuser=True,
    )
    audit.record(
        action="platform_admin.created",
        summary=f"{person.full_name} was made a platform administrator",
        target=user,
        details={"person": person.public_id},
    )
    return user
