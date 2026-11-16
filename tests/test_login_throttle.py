"""Tests for the limit on wrong passwords."""

import io
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services, throttle
from apps.accounts.models import LoginThrottle
from apps.audit.models import AuditEntry
from apps.people import services as people_services

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-41"
PHONE = "0788123456"


@pytest.fixture
def user():
    person = people_services.create_person(full_name="Aline Uwase", phone=PHONE)
    return services.create_user_account(person=person, password=PASSWORD)


def wrong_attempts(count, phone=PHONE):
    """Make this many wrong attempts, ignoring the lock error on the last one."""
    for _ in range(count):
        try:
            services.authenticate_by_phone(None, phone=phone, password="wrong")
        except services.TooManyAttempts:
            pass


def test_four_wrong_attempts_do_not_lock_and_success_forgets_them(user):
    wrong_attempts(4)

    assert services.authenticate_by_phone(None, phone=PHONE, password=PASSWORD) == user
    assert not LoginThrottle.objects.exists()


def test_the_fifth_wrong_attempt_locks_the_number(user):
    wrong_attempts(4)

    with pytest.raises(services.TooManyAttempts) as locked:
        services.authenticate_by_phone(None, phone=PHONE, password="wrong")

    assert locked.value.minutes_left == 15


def test_while_locked_even_the_correct_password_is_refused(user):
    wrong_attempts(5)

    with pytest.raises(services.TooManyAttempts):
        services.authenticate_by_phone(None, phone=PHONE, password=PASSWORD)


def test_the_lock_ends_by_itself(user):
    wrong_attempts(5)
    LoginThrottle.objects.update(locked_until=timezone.now() - timedelta(seconds=1))

    assert services.authenticate_by_phone(None, phone=PHONE, password=PASSWORD) == user


def test_old_wrong_attempts_are_forgotten(user):
    wrong_attempts(4)
    LoginThrottle.objects.update(window_started_at=timezone.now() - timedelta(minutes=16))

    wrong_attempts(1)  # would be the fifth, but the first four are too old to count

    assert throttle.locked_until("+250788123456") is None
    assert LoginThrottle.objects.get().failures == 1


def test_the_same_number_written_differently_counts_together(user):
    for written in ["0788123456", "+250788123456", "788 123 456", "250788123456"]:
        wrong_attempts(1, phone=written)

    with pytest.raises(services.TooManyAttempts):
        services.authenticate_by_phone(None, phone="0788 123 456", password="wrong")


def test_numbers_are_counted_separately(user):
    wrong_attempts(5, phone="0788999999")

    assert services.authenticate_by_phone(None, phone=PHONE, password=PASSWORD) == user


def test_a_number_with_no_account_is_locked_the_same_way(db):
    """So the lock message cannot be used to find out who has an account."""
    wrong_attempts(4, phone="0788999999")

    with pytest.raises(services.TooManyAttempts):
        services.authenticate_by_phone(None, phone="0788999999", password="wrong")


def test_the_sign_in_page_shows_the_lock_and_does_not_sign_in(client, user):
    for _ in range(5):
        response = client.post(reverse("accounts:login"), {"phone": PHONE, "password": "wrong"})
    assert "Too many wrong attempts" in response.content.decode()

    response = client.post(reverse("accounts:login"), {"phone": PHONE, "password": PASSWORD})

    assert response.status_code == 200  # still on the sign-in page
    assert "locked for about 15 more minute" in response.content.decode()
    assert client.get(reverse("accounts:home")).status_code == 302


def test_the_platform_admin_sign_in_is_limited_too(client):
    admin = services.create_platform_admin(
        full_name="Platform Owner", phone="0788555555", password=PASSWORD
    )
    admin_login = reverse("admin:login")
    for _ in range(5):
        client.post(admin_login, {"username": "0788555555", "password": "wrong"})

    client.post(admin_login, {"username": "0788555555", "password": PASSWORD})

    assert "_auth_user_id" not in client.session
    assert admin.is_staff  # a real admin, refused only because of the lock


def test_the_platform_admin_sign_in_accepts_the_number_written_locally(client):
    services.create_platform_admin(
        full_name="Platform Owner", phone="0788555555", password=PASSWORD
    )

    client.post(reverse("admin:login"), {"username": "0788 555 555", "password": PASSWORD})

    assert "_auth_user_id" in client.session


def test_unlock_command_removes_the_lock_and_is_recorded(user):
    wrong_attempts(5)
    out = io.StringIO()

    call_command("unlock_sign_in", "--phone", PHONE, stdout=out)

    assert "Unlocked" in out.getvalue()
    assert services.authenticate_by_phone(None, phone=PHONE, password=PASSWORD) == user
    assert AuditEntry.objects.filter(action="account.unlocked").count() == 1


def test_unlock_command_when_nothing_is_locked(db):
    out = io.StringIO()

    call_command("unlock_sign_in", "--phone", PHONE, stdout=out)

    assert "was not locked" in out.getvalue()
    assert not AuditEntry.objects.filter(action="account.unlocked").exists()
