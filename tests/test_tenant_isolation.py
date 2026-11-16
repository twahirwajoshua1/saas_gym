"""
Tenant isolation tests (lock 1).

Two gyms exist. Each test signs in as staff of Alpha Fitness and tries to reach
Bravo Fitness. Every attempt must fail. These tests run on every change.
"""

import uuid

import pytest
from django.urls import reverse

from apps.accounts import services as account_services
from apps.gyms import services, tenancy
from apps.gyms.models import Branch, Role, StaffAssignment
from apps.people import services as people_services

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-41"


def make_person_with_login(name, phone):
    person = people_services.create_person(full_name=name, phone=phone)
    return account_services.create_user_account(person=person, password=PASSWORD)


@pytest.fixture
def owner_a():
    return make_person_with_login("Aline Uwase", "0788000001")


@pytest.fixture
def owner_b():
    return make_person_with_login("Bosco Kamanzi", "0788000002")


@pytest.fixture
def gym_a(owner_a):
    gym = services.create_gym(name="Alpha Fitness", owner=owner_a.person)
    services.add_branch(gym=gym, name="A Downtown")
    return gym


@pytest.fixture
def gym_b(owner_b):
    gym = services.create_gym(name="Bravo Fitness", owner=owner_b.person)
    services.add_branch(gym=gym, name="B Secret Branch")
    return gym


def sign_in(client, user):
    client.force_login(user)
    return client.get(reverse("gyms:enter"))


def test_staff_of_one_gym_go_straight_into_it(client, owner_a, gym_a, gym_b):
    response = sign_in(client, owner_a)
    assert response.url == reverse("gyms:home")

    page = client.get(reverse("gyms:home")).content.decode()
    assert "Alpha Fitness" in page
    assert "Bravo Fitness" not in page


def test_branch_list_shows_only_my_gyms_branches(client, owner_a, gym_a, gym_b):
    sign_in(client, owner_a)

    page = client.get(reverse("gyms:branch_list")).content.decode()

    assert "A Downtown" in page
    assert "B Secret Branch" not in page


def test_another_gyms_branch_address_is_not_found(client, owner_a, gym_a, gym_b):
    sign_in(client, owner_a)
    their_branch = Branch.objects.for_gym(gym_b).get(name="B Secret Branch")

    response = client.get(reverse("gyms:branch_detail", args=[their_branch.public_id]))

    assert response.status_code == 404


def test_i_cannot_choose_a_gym_i_do_not_work_at(client, owner_a, gym_a, gym_b):
    sign_in(client, owner_a)

    response = client.post(reverse("gyms:choose"), {"gym": str(gym_b.public_id)})

    assert response.status_code == 403
    assert "Alpha Fitness" in client.get(reverse("gyms:home")).content.decode()


@pytest.mark.parametrize("value", ["not-an-id", "", str(uuid.uuid4())])
def test_choosing_a_nonsense_gym_is_refused(client, owner_a, gym_a, value):
    sign_in(client, owner_a)

    assert client.post(reverse("gyms:choose"), {"gym": value}).status_code == 403


def test_a_tampered_session_does_not_open_another_gym(client, owner_a, gym_a, gym_b):
    sign_in(client, owner_a)
    session = client.session
    session[tenancy.SESSION_KEY] = str(gym_b.public_id)
    session.save()

    response = client.get(reverse("gyms:branch_list"))

    assert response.status_code == 302  # sent away, shown nothing
    assert response.url == reverse("gyms:enter")


def test_removed_staff_lose_access_immediately(client, owner_a, gym_a):
    receptionist = make_person_with_login("Claudine Mukamana", "0788000003")
    assignment = services.assign_staff(
        gym=gym_a, person=receptionist.person, role=Role.RECEPTIONIST
    )
    sign_in(client, receptionist)
    assert client.get(reverse("gyms:home")).status_code == 200

    services.remove_staff(assignment=assignment)

    assert client.get(reverse("gyms:home")).status_code == 302


def test_a_closed_gym_cannot_be_opened(client, owner_a, gym_a):
    sign_in(client, owner_a)
    gym_a.is_active = False
    gym_a.save()

    assert client.get(reverse("gyms:home")).status_code == 302


def test_a_receptionist_cannot_open_owner_screens(client, gym_a):
    receptionist = make_person_with_login("Claudine Mukamana", "0788000003")
    services.assign_staff(gym=gym_a, person=receptionist.person, role=Role.RECEPTIONIST)
    sign_in(client, receptionist)

    assert client.get(reverse("gyms:home")).status_code == 200
    assert client.get(reverse("gyms:branch_list")).status_code == 403


def test_someone_at_two_gyms_chooses_and_sees_only_the_chosen_one(client, owner_a, gym_a, gym_b):
    services.assign_staff(gym=gym_b, person=owner_a.person, role=Role.BRANCH_MANAGER)

    response = sign_in(client, owner_a)
    assert response.status_code == 200  # the "choose a gym" page
    assert client.get(reverse("gyms:home")).status_code == 302  # nothing chosen yet

    client.post(reverse("gyms:choose"), {"gym": str(gym_b.public_id)})
    page = client.get(reverse("gyms:branch_list")).content.decode()
    assert "B Secret Branch" in page
    assert "A Downtown" not in page


def test_someone_with_no_gym_sees_no_gym_data(client, gym_a):
    outsider = make_person_with_login("Patrick Niyonzima", "0788000009")

    response = sign_in(client, outsider)

    assert response.status_code == 200
    assert "not staff at any gym" in response.content.decode()
    assert client.get(reverse("gyms:branch_list")).status_code == 302


def test_gym_screens_require_sign_in(client, gym_a):
    for name in ["gyms:enter", "gyms:home", "gyms:branch_list"]:
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert response.url.startswith(reverse("accounts:login"))


def test_every_assignment_row_belongs_to_exactly_one_gym(gym_a, gym_b):
    assert StaffAssignment.objects.for_gym(gym_a).count() == 1
    assert StaffAssignment.objects.for_gym(gym_b).count() == 1
