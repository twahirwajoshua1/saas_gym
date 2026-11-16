"""Tests for login accounts and the sign-in pages."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts import services
from apps.accounts.models import User
from apps.people import services as people_services

pytestmark = pytest.mark.django_db

GOOD_PASSWORD = "correct-horse-battery-41"


@pytest.fixture
def person():
    return people_services.create_person(full_name="Aline Uwase", phone="0788123456")


@pytest.fixture
def user(person):
    return services.create_user_account(person=person, password=GOOD_PASSWORD)


def test_account_uses_the_persons_phone_and_hides_the_password(person):
    user = services.create_user_account(person=person, password=GOOD_PASSWORD)

    assert user.phone == person.phone
    assert user.password != GOOD_PASSWORD  # stored scrambled, never as typed
    assert user.check_password(GOOD_PASSWORD)
    assert user.is_staff is False


def test_a_person_cannot_have_two_accounts(person, user):
    with pytest.raises(services.AccountAlreadyExists):
        services.create_user_account(person=person, password=GOOD_PASSWORD)


def test_weak_passwords_are_refused(person):
    with pytest.raises(ValidationError):
        services.create_user_account(person=person, password="12345")

    assert User.objects.count() == 0


def test_authenticate_accepts_the_number_written_differently(user):
    assert services.authenticate_by_phone(None, phone="788 123 456", password=GOOD_PASSWORD) == user


def test_authenticate_refuses_wrong_password_and_unknown_number(user):
    assert services.authenticate_by_phone(None, phone="0788123456", password="wrong") is None
    assert services.authenticate_by_phone(None, phone="0788999999", password=GOOD_PASSWORD) is None
    assert services.authenticate_by_phone(None, phone="nonsense", password=GOOD_PASSWORD) is None


def test_inactive_account_cannot_sign_in(user):
    user.is_active = False
    user.save()

    assert services.authenticate_by_phone(None, phone="0788123456", password=GOOD_PASSWORD) is None


def test_create_platform_admin(db):
    admin = services.create_platform_admin(
        full_name="Platform Owner", phone="0788555555", password=GOOD_PASSWORD
    )

    assert admin.is_staff and admin.is_superuser
    assert admin.person.full_name == "Platform Owner"


def test_home_page_requires_sign_in(client):
    response = client.get(reverse("accounts:home"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


def test_sign_in_and_sign_out_through_the_pages(client, user):
    response = client.post(
        reverse("accounts:login"), {"phone": "0788 123 456", "password": GOOD_PASSWORD}
    )
    assert response.status_code == 302
    assert response.url == reverse("accounts:home")

    home = client.get(reverse("accounts:home"))
    assert "Welcome, Aline Uwase" in home.content.decode()

    client.post(reverse("accounts:logout"))
    assert client.get(reverse("accounts:home")).status_code == 302


def test_sign_in_with_wrong_password_shows_one_generic_message(client, user):
    response = client.post(reverse("accounts:login"), {"phone": "0788123456", "password": "wrong"})

    assert response.status_code == 200
    assert "The phone number or password is not correct." in response.content.decode()


def test_sign_out_must_be_a_post(client, user):
    client.force_login(user)

    assert client.get(reverse("accounts:logout")).status_code == 405
