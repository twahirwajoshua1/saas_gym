"""Tests for the packages a gym sells."""

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.audit.models import AuditEntry
from apps.gyms.models import Role
from apps.members import services
from apps.members.models import Package

pytestmark = pytest.mark.django_db

MONTHLY = {"name": "Monthly", "price_rwf": 30000, "duration_count": 1, "duration_unit": "month"}


@pytest.fixture
def alpha(make_gym):
    return make_gym("Alpha Fitness", "0788000001", "Aline Uwase")


@pytest.fixture
def bravo(make_gym):
    return make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")


# --- Rules ------------------------------------------------------------------


def test_the_owner_adds_a_package_and_it_is_recorded(alpha):
    package = services.create_package(by=alpha.assignment, **{**MONTHLY, "name": "  Monthly  "})

    assert package.name == "Monthly"
    assert package.price_rwf == 30000
    assert package.duration_label == "1 month"
    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="package.created")
    assert entry.actor == alpha.person
    assert entry.details["price_rwf"] == 30000


def test_duration_labels():
    assert Package(duration_count=3, duration_unit="month").duration_label == "3 months"
    assert Package(duration_count=1, duration_unit="day").duration_label == "1 day"


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.RECEPTIONIST, Role.SALES_REP])
def test_only_the_owner_may_add_or_change_packages(alpha, add_staff, role):
    staff = add_staff(alpha.gym, "0788000005", role)
    package = services.create_package(by=alpha.assignment, **MONTHLY)

    with pytest.raises(PermissionDenied):
        services.create_package(by=staff.assignment, **{**MONTHLY, "name": "Other"})
    with pytest.raises(PermissionDenied):
        services.update_package(by=staff.assignment, package=package, **MONTHLY)
    with pytest.raises(PermissionDenied):
        services.set_package_active(by=staff.assignment, package=package, active=False)


def test_removed_staff_cannot_act_even_with_an_old_assignment(alpha, add_staff):
    from apps.gyms import services as gym_services

    second_owner = add_staff(alpha.gym, "0788000005", Role.OWNER)
    gym_services.remove_staff(assignment=second_owner.assignment)

    with pytest.raises(PermissionDenied):
        services.create_package(by=second_owner.assignment, **MONTHLY)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": "   "}, "name"),
        ({"price_rwf": 0}, "price_rwf"),
        ({"price_rwf": -5}, "price_rwf"),
        ({"price_rwf": 30000.5}, "price_rwf"),
        ({"price_rwf": 100_000_001}, "price_rwf"),
        ({"duration_count": 0}, "duration_count"),
        ({"duration_unit": "fortnight"}, "duration_unit"),
    ],
)
def test_wrong_values_are_refused(alpha, change, field):
    with pytest.raises(services.InvalidDetails) as problem:
        services.create_package(by=alpha.assignment, **{**MONTHLY, **change})

    assert problem.value.field == field
    assert not Package.objects.exists()


def test_names_are_unique_within_a_gym_but_not_across_gyms(alpha, bravo):
    services.create_package(by=alpha.assignment, **MONTHLY)

    with pytest.raises(services.InvalidDetails) as problem:
        services.create_package(by=alpha.assignment, **{**MONTHLY, "name": "monthly"})
    assert (problem.value.field, problem.value.reason) == ("name", "duplicate")

    services.create_package(by=bravo.assignment, **MONTHLY)  # a different gym: fine


def test_changing_a_package_records_old_and_new_values(alpha):
    package = services.create_package(by=alpha.assignment, **MONTHLY)

    services.update_package(by=alpha.assignment, package=package, **{**MONTHLY, "price_rwf": 35000})

    package.refresh_from_db()
    assert package.price_rwf == 35000
    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="package.updated")
    assert entry.details == {"price_rwf": {"from": 30000, "to": 35000}}


def test_saving_without_changes_records_nothing(alpha):
    package = services.create_package(by=alpha.assignment, **MONTHLY)

    services.update_package(by=alpha.assignment, package=package, **MONTHLY)

    assert not AuditEntry.objects.filter(action="package.updated").exists()


def test_archive_and_restore_keep_the_package_and_are_recorded(alpha):
    package = services.create_package(by=alpha.assignment, **MONTHLY)

    services.set_package_active(by=alpha.assignment, package=package, active=False)
    assert Package.objects.for_gym(alpha.gym).get().is_active is False

    services.set_package_active(by=alpha.assignment, package=package, active=True)
    actions = list(
        AuditEntry.objects.for_gym(alpha.gym)
        .filter(action__startswith="package.")
        .order_by("id")
        .values_list("action", flat=True)
    )
    assert actions == ["package.created", "package.archived", "package.restored"]


def test_an_owner_cannot_change_another_gyms_package(alpha, bravo):
    theirs = services.create_package(by=bravo.assignment, **MONTHLY)

    with pytest.raises(PermissionDenied):
        services.update_package(by=alpha.assignment, package=theirs, **MONTHLY)
    with pytest.raises(PermissionDenied):
        services.set_package_active(by=alpha.assignment, package=theirs, active=False)


# --- Screens ----------------------------------------------------------------


def test_owner_adds_a_package_through_the_form_typing_the_price_with_a_comma(alpha, sign_in):
    client = sign_in(alpha)

    response = client.post(
        reverse("members:package_create"),
        {"name": "Monthly", "price_rwf": "30,000", "duration_count": "1", "duration_unit": "month"},
        follow=True,
    )

    page = response.content.decode()
    assert "Monthly was added." in page
    assert "RWF 30,000" in page
    assert "1 month, as often as you like" in page
    assert Package.objects.for_gym(alpha.gym).get().price_rwf == 30000


@pytest.mark.parametrize("typed", ["abc", "30.5", "-100", ""])
def test_a_price_that_is_not_a_whole_number_is_explained(alpha, sign_in, typed):
    client = sign_in(alpha)

    response = client.post(
        reverse("members:package_create"),
        {"name": "Monthly", "price_rwf": typed, "duration_count": "1", "duration_unit": "month"},
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()
    assert not Package.objects.exists()


def test_a_duplicate_name_is_explained_on_the_form(alpha, sign_in):
    services.create_package(by=alpha.assignment, **MONTHLY)
    client = sign_in(alpha)

    response = client.post(
        reverse("members:package_create"),
        {"name": "monthly", "price_rwf": "25000", "duration_count": "1", "duration_unit": "month"},
    )

    assert "Another package already has this name." in response.content.decode()


def test_edit_and_archive_through_the_screens(alpha, sign_in):
    package = services.create_package(by=alpha.assignment, **MONTHLY)
    client = sign_in(alpha)

    edit_page = client.get(reverse("members:package_edit", args=[package.public_id]))
    assert 'value="30000"' in edit_page.content.decode()

    client.post(
        reverse("members:package_edit", args=[package.public_id]),
        {"name": "Monthly", "price_rwf": "35000", "duration_count": "1", "duration_unit": "month"},
    )
    response = client.post(
        reverse("members:package_set_active", args=[package.public_id]),
        {"active": "no"},
        follow=True,
    )

    page = response.content.decode()
    assert "Monthly was archived." in page
    assert "Archived" in page and "Restore" in page
    package.refresh_from_db()
    assert (package.price_rwf, package.is_active) == (35000, False)


def test_a_receptionist_sees_packages_but_has_no_way_to_change_them(alpha, add_staff, sign_in):
    package = services.create_package(by=alpha.assignment, **MONTHLY)
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST))

    page = client.get(reverse("members:package_list")).content.decode()
    assert "Monthly" in page
    assert "Add package" not in page and "Archive" not in page

    assert client.get(reverse("members:package_create")).status_code == 403
    assert client.get(reverse("members:package_edit", args=[package.public_id])).status_code == 403
    toggle = client.post(reverse("members:package_set_active", args=[package.public_id]))
    assert toggle.status_code == 403


def test_a_trainer_cannot_open_packages_or_members(alpha, add_staff, sign_in):
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.TRAINER))

    assert client.get(reverse("members:package_list")).status_code == 403
    assert client.get(reverse("frontdesk:people")).status_code == 403
    page = client.get(reverse("gyms:home")).content.decode()
    assert reverse("frontdesk:people") not in page


def test_archiving_needs_a_post(alpha, sign_in):
    package = services.create_package(by=alpha.assignment, **MONTHLY)
    client = sign_in(alpha)

    url = reverse("members:package_set_active", args=[package.public_id])
    assert client.get(url).status_code == 405
