"""Tests for the rules about gyms, branches and staff."""

import pytest

from apps.gyms import services
from apps.gyms.models import Branch, Gym, Role, StaffAssignment
from apps.people import services as people_services

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return people_services.create_person(full_name="Aline Uwase", phone="0788000001")


@pytest.fixture
def gym(owner):
    return services.create_gym(name="Kigali Fit Club", owner=owner)


def test_a_new_gym_gets_a_main_branch_and_its_owner(gym, owner):
    assert [b.name for b in Branch.objects.for_gym(gym)] == ["Main branch"]

    assignment = StaffAssignment.objects.for_gym(gym).get()
    assert assignment.person == owner
    assert assignment.role == Role.OWNER


def test_a_gym_without_a_name_creates_nothing(owner):
    with pytest.raises(services.InvalidGymDetails):
        services.create_gym(name="  ", owner=owner)

    assert Gym.objects.count() == 0
    assert Branch.objects.count() == 0


def test_branch_names_are_unique_within_a_gym_but_not_across_gyms(gym, owner):
    services.add_branch(gym=gym, name="Remera")
    with pytest.raises(services.InvalidGymDetails):
        services.add_branch(gym=gym, name="remera")

    other_gym = services.create_gym(name="Musanze Gym", owner=owner)
    services.add_branch(gym=other_gym, name="Remera")  # allowed: a different gym


def test_staff_cannot_be_assigned_to_another_gyms_branch(gym, owner):
    other_gym = services.create_gym(name="Musanze Gym", owner=owner)
    other_branch = Branch.objects.for_gym(other_gym).get()
    trainer = people_services.create_person(full_name="Eric Mugisha", phone="0788000002")

    with pytest.raises(services.InvalidGymDetails):
        services.assign_staff(gym=gym, person=trainer, role=Role.TRAINER, branch=other_branch)


def test_a_person_has_one_role_per_gym_and_roles_must_exist(gym, owner):
    with pytest.raises(services.AlreadyAssigned):
        services.assign_staff(gym=gym, person=owner, role=Role.RECEPTIONIST)

    someone = people_services.create_person(full_name="Eric Mugisha", phone="0788000002")
    with pytest.raises(services.InvalidGymDetails):
        services.assign_staff(gym=gym, person=someone, role="king")


def test_active_assignments_leave_out_removed_staff_and_closed_gyms(gym, owner):
    second = services.create_gym(name="Musanze Gym", owner=owner)
    assert services.active_assignments_for(owner).count() == 2

    services.remove_staff(assignment=StaffAssignment.objects.for_gym(second).get())
    assert [a.gym for a in services.active_assignments_for(owner)] == [gym]

    gym.is_active = False
    gym.save()
    assert services.active_assignments_for(owner).count() == 0
