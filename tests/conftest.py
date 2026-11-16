"""Helpers shared by the tests."""

from dataclasses import dataclass

import pytest
from django.urls import reverse

from apps.accounts import services as account_services
from apps.accounts.models import User
from apps.gyms import services as gym_services
from apps.gyms.models import Gym, StaffAssignment
from apps.people import services as people_services

TEST_PASSWORD = "correct-horse-battery-41"


@pytest.fixture(autouse=True)
def fast_passwords(settings):
    """
    Scramble passwords with a quick method while testing.

    The real method is slow on purpose (to resist guessing), which would make
    hundreds of tests crawl. Real installations are not affected.
    """
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@dataclass
class Staff:
    """A staff member of a gym, with everything a test needs to act as them."""

    user: User
    assignment: StaffAssignment

    @property
    def gym(self) -> Gym:
        return self.assignment.gym

    @property
    def person(self):
        return self.assignment.person


@pytest.fixture
def make_gym(db):
    """make_gym("Alpha Fitness", "0788000001") creates a gym and returns its owner as Staff."""

    def make(name: str, owner_phone: str, owner_name: str = "Owner") -> Staff:
        person = people_services.create_person(full_name=owner_name, phone=owner_phone)
        user = account_services.create_user_account(person=person, password=TEST_PASSWORD)
        gym = gym_services.create_gym(name=name, owner=person)
        assignment = StaffAssignment.objects.for_gym(gym).select_related("gym", "person").get()
        return Staff(user=user, assignment=assignment)

    return make


@pytest.fixture
def add_staff(db):
    """add_staff(gym, "0788000005", Role.RECEPTIONIST) adds a staff member with a login."""

    def add(gym: Gym, phone: str, role: str, name: str = "Staff Member") -> Staff:
        person = people_services.create_person(full_name=name, phone=phone)
        user = account_services.create_user_account(person=person, password=TEST_PASSWORD)
        assignment = gym_services.assign_staff(gym=gym, person=person, role=role)
        return Staff(user=user, assignment=assignment)

    return add


@pytest.fixture
def sign_in(client):
    """sign_in(staff) signs them in and enters their gym, like the real pages do."""

    def enter(staff: Staff):
        client.force_login(staff.user)
        client.get(reverse("gyms:enter"))
        return client

    return enter
