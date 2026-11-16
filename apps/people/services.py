"""Business rules for people. Screens and other modules call these functions."""

from django.db import IntegrityError, transaction

from apps.people.models import Person
from apps.people.phones import InvalidPhoneNumber, normalize_phone


class DuplicatePhoneNumber(Exception):
    """Someone with this phone number is already registered."""


class InvalidPersonDetails(ValueError):
    """The details given for a person are incomplete or wrong."""


def find_person_by_phone(phone: str) -> Person | None:
    """Return the person with this phone number, or None if there is nobody."""
    try:
        standard_phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        return None
    return Person.objects.filter(phone=standard_phone).first()


def create_person(*, full_name: str, phone: str, email: str = "") -> Person:
    """
    Register a new person.

    Raises InvalidPhoneNumber, InvalidPersonDetails or DuplicatePhoneNumber.
    """
    full_name = " ".join((full_name or "").split())
    if not full_name:
        raise InvalidPersonDetails("A person needs a name.")

    standard_phone = normalize_phone(phone)

    if Person.objects.filter(phone=standard_phone).exists():
        raise DuplicatePhoneNumber(f"{standard_phone} is already registered.")

    try:
        # The database also refuses duplicates. This covers the rare case of
        # two receptionists registering the same number at the same moment.
        with transaction.atomic():
            return Person.objects.create(
                full_name=full_name, phone=standard_phone, email=(email or "").strip()
            )
    except IntegrityError as error:
        raise DuplicatePhoneNumber(f"{standard_phone} is already registered.") from error
