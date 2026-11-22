"""Demo names on the sign-in page: only on a developer's computer, never otherwise."""

import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import reverse

from apps.accounts import services as accounts
from apps.accounts.models import DemoLogin

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner_user(make_gym):
    return make_gym("Demo Gym", "0788000001").user


@override_settings(DEMO_LOGINS=True)
def test_a_demo_name_signs_in_as_its_account(client, owner_user):
    user = owner_user
    accounts.set_demo_login(name="GymOwner", password="gymower", user=user)
    response = client.post(reverse("accounts:login"), {"phone": "gymowner", "password": "gymower"})
    assert response.status_code == 302
    assert int(client.session["_auth_user_id"]) == user.pk


@override_settings(DEMO_LOGINS=True)
def test_the_password_must_match_exactly(client, owner_user):
    accounts.set_demo_login(name="company", password="Company", user=owner_user)
    response = client.post(reverse("accounts:login"), {"phone": "company", "password": "company"})
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


@override_settings(DEMO_LOGINS=True)
def test_a_member_demo_name_opens_the_card_without_a_login(client):
    accounts.set_demo_login(name="member", password="member", url="/card/abc/")
    response = client.post(reverse("accounts:login"), {"phone": "member", "password": "member"})
    assert response.status_code == 302 and response["Location"] == "/card/abc/"
    assert "_auth_user_id" not in client.session


def test_demo_names_do_nothing_when_switched_off(client, owner_user):
    DemoLogin.objects.create(
        name="gymowner",
        password=make_password("gymower"),
        user=owner_user,
    )
    response = client.post(reverse("accounts:login"), {"phone": "gymowner", "password": "gymower"})
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_they_cannot_be_created_when_switched_off():
    with pytest.raises(RuntimeError):
        accounts.set_demo_login(name="x", password="y", url="/")
    with pytest.raises(CommandError):
        call_command("create_demo_logins")
