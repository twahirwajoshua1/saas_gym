"""Business rules for login accounts."""

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from apps.accounts.models import User
from apps.audit import services as audit
from apps.people import services as people_services
from apps.people.models import Person
from apps.people.phones import InvalidPhoneNumber, normalize_phone


class AccountAlreadyExists(Exception):
    """This person already has a login account."""


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
    """Return the user if the phone number and password are correct, otherwise None."""
    try:
        standard_phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        return None
    return authenticate(request, username=standard_phone, password=password)


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
