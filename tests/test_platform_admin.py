"""Tests for the platform staff pages: registering gyms, applications, suspending."""

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.audit.models import AuditEntry
from apps.gyms.models import Branch, Gym, Role, StaffAssignment
from apps.modules import services as modules
from apps.modules.catalog import PAYMENTS_FINANCE, RETENTION_LOYALTY
from apps.platformadmin import services
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    return accounts.create_platform_admin(
        full_name="Platform Owner", phone="0788999999", password=TEST_PASSWORD
    )


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


def test_registering_a_gym_creates_branch_owner_and_temporary_login(admin_user):
    created = services.register_gym(
        user=admin_user,
        gym_name="Imbaraga Fitness",
        owner_name="Aline Uwase",
        owner_phone="0788 100 200",
    )

    gym = created.gym
    assert Branch.objects.for_gym(gym).count() == 1
    owner = StaffAssignment.objects.for_gym(gym).get()
    assert owner.role == Role.OWNER and owner.person.phone == "+250788100200"
    user = User.objects.get(person=owner.person)
    assert user.check_password(created.owner_temporary_password) and user.must_change_password
    entry = AuditEntry.objects.for_gym(gym).get(action="gym.created")
    assert entry.actor == admin_user.person


def test_an_owner_who_already_has_a_login_keeps_it(admin_user, make_gym):
    existing = make_gym("First Gym", "0788000001", "Aline Uwase")

    created = services.register_gym(
        user=admin_user, gym_name="Second Gym", owner_name="ignored", owner_phone="0788000001"
    )

    assert created.owner_temporary_password is None
    assert StaffAssignment.objects.for_gym(created.gym).get().person == existing.person


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"owner_phone": "123"}, "phone_invalid"),
        ({"owner_name": " "}, "owner_name_required"),
        ({"gym_name": " "}, "gym_name_required"),
    ],
)
def test_registration_problems(admin_user, values, reason):
    data = {"gym_name": "Gym", "owner_name": "Owner", "owner_phone": "0788100200", **values}

    with pytest.raises(services.PlatformProblem) as problem:
        services.register_gym(user=admin_user, **data)

    assert problem.value.reason == reason
    assert not Gym.objects.exists()


def test_only_platform_staff_can_run_the_platform(make_gym):
    owner = make_gym("Alpha Fitness", "0788000001")

    with pytest.raises(PermissionDenied):
        services.register_gym(
            user=owner.user, gym_name="Mine", owner_name="Me", owner_phone="0788100200"
        )
    with pytest.raises(PermissionDenied):
        services.set_module(user=owner.user, gym=owner.gym, code=PAYMENTS_FINANCE, on=True)


def test_switching_applications_follows_the_rules_and_is_recorded(admin_user, make_gym):
    gym = make_gym("Alpha Fitness", "0788000001").gym

    services.set_module(user=admin_user, gym=gym, code=PAYMENTS_FINANCE, on=True)
    with pytest.raises(modules.MissingDependencies):
        services.set_module(user=admin_user, gym=gym, code=RETENTION_LOYALTY, on=True)

    entry = AuditEntry.objects.for_gym(gym).get(action="module.enabled")
    assert entry.actor == admin_user.person


def test_suspending_a_gym_locks_its_staff_out_and_restoring_lets_them_back(
    admin_user, make_gym, sign_in
):
    alpha = make_gym("Alpha Fitness", "0788000001")
    with pytest.raises(services.PlatformProblem):
        services.set_gym_active(user=admin_user, gym=alpha.gym, active=False, reason=" ")

    services.set_gym_active(user=admin_user, gym=alpha.gym, active=False, reason="Unpaid")
    client = sign_in(alpha)
    assert client.get(reverse("gyms:home")).status_code == 302

    services.set_gym_active(user=admin_user, gym=alpha.gym, active=True)
    client.get(reverse("gyms:enter"))
    assert client.get(reverse("gyms:home")).status_code == 200
    actions = list(
        AuditEntry.objects.for_gym(alpha.gym)
        .filter(action__in=["gym.suspended", "gym.restored"])
        .order_by("id")
        .values_list("action", flat=True)
    )
    assert actions == ["gym.suspended", "gym.restored"]


# --- Screens ----------------------------------------------------------------


def test_platform_pages_list_every_gym(admin_client, make_gym):
    make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")

    page = admin_client.get(reverse("platformadmin:gym_list")).content.decode()

    assert "Alpha Fitness" in page and "Bravo Fitness" in page
    assert "Aline Uwase" in page and "1 of 9" in page


def test_register_a_gym_through_the_screen(admin_client):
    response = admin_client.post(
        reverse("platformadmin:gym_create"),
        {
            "gym_name": "Ubuzima Wellness",
            "owner_name": "Diane Umutoni",
            "owner_phone": "0722100200",
        },
    )

    page = response.content.decode()
    assert "Ubuzima Wellness is registered" in page and "shown only once" in page
    bad = admin_client.post(
        reverse("platformadmin:gym_create"),
        {"gym_name": "X", "owner_name": "Y", "owner_phone": "12"},
    )
    assert "Enter a valid phone number" in bad.content.decode()


def test_switch_an_application_on_through_the_screen(admin_client, make_gym):
    gym = make_gym("Alpha Fitness", "0788000001").gym
    url = reverse("platformadmin:gym_module", args=[gym.public_id])

    admin_client.post(url, {"module": PAYMENTS_FINANCE, "on": "yes"})
    refused = admin_client.post(url, {"module": RETENTION_LOYALTY, "on": "yes"}, follow=True)

    assert modules.is_enabled(gym, PAYMENTS_FINANCE)
    assert "needs" in refused.content.decode()


def test_gym_staff_cannot_open_platform_pages(make_gym, sign_in):
    client = sign_in(make_gym("Alpha Fitness", "0788000001"))

    assert client.get(reverse("platformadmin:gym_list")).status_code == 403
    assert client.post(reverse("platformadmin:gym_create"), {"gym_name": "x"}).status_code == 403


def test_signed_out_visitors_are_sent_to_sign_in(client):
    response = client.get(reverse("platformadmin:gym_list"))

    assert response.status_code == 302 and reverse("accounts:login") in response.url


def test_platform_staff_land_on_the_platform_link(admin_client):
    page = admin_client.get(reverse("gyms:enter")).content.decode()

    assert reverse("platformadmin:gym_list") in page
