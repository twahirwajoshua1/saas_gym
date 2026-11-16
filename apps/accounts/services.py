"""Business rules for login accounts."""

import math

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


@transaction.atomic
def create_user_account(*, person: Person, password: str, actor: Person | None = None) -> User:
    """
    Give a person a login account, using their phone number to sign in.

    Raises AccountAlreadyExists, or Django's ValidationError if the password
    is too weak.
    """
    if User.objects.filter(person=person).exists():
        raise AccountAlreadyExists(f"{person} already has a login account.")

    validate_password(password)
    user = User.objects.create_user(phone=person.phone, password=password, person=person)
    audit.record(
        action="account.created",
        summary=f"A login account was created for {person.full_name}",
        actor=actor,
        target=user,
        details={"person": person.public_id},
    )
    return user


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
