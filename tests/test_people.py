"""Tests for registering people."""

import pytest

from apps.people import services
from apps.people.models import Person
from apps.people.phones import InvalidPhoneNumber

pytestmark = pytest.mark.django_db


def test_create_person_stores_the_phone_in_standard_form():
    person = services.create_person(full_name="  Aline   Uwase ", phone="0788 123 456")

    assert person.full_name == "Aline Uwase"
    assert person.phone == "+250788123456"
    assert person.public_id is not None


def test_the_same_number_written_differently_is_a_duplicate():
    services.create_person(full_name="Aline Uwase", phone="0788123456")

    with pytest.raises(services.DuplicatePhoneNumber):
        services.create_person(full_name="Someone Else", phone="+250 788 123 456")

    assert Person.objects.count() == 1


def test_a_person_needs_a_name():
    with pytest.raises(services.InvalidPersonDetails):
        services.create_person(full_name="   ", phone="0788123456")


def test_a_person_needs_a_valid_phone():
    with pytest.raises(InvalidPhoneNumber):
        services.create_person(full_name="Aline Uwase", phone="12345")


def test_find_person_by_phone():
    person = services.create_person(full_name="Aline Uwase", phone="0788123456")

    assert services.find_person_by_phone("788 123 456") == person
    assert services.find_person_by_phone("0788000000") is None
    assert services.find_person_by_phone("not a number") is None
