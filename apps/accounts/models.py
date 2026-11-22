from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from apps.people.models import Person
from apps.people.phones import normalize_phone


class UserManager(BaseUserManager):
    """Creates user accounts. Django calls this when it needs to make a user."""

    def create_user(self, phone, password=None, *, person, **extra_fields):
        user = self.model(phone=normalize_phone(phone), person=person, **extra_fields)
        # set_password stores a scrambled (hashed) version, never the password itself.
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """
    A login account. It belongs to exactly one Person.

    Password handling, sessions and permissions come from Django. The only
    change is that people sign in with a phone number instead of a username.
    """

    person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="user_account")

    # The phone number used to sign in. It is always the same as the person's
    # phone number; the services in this module keep the two in step.
    phone = models.CharField(max_length=16, unique=True)

    is_active = models.BooleanField(default=True)

    # Staff of the platform itself (us), allowed into the platform admin.
    # This has nothing to do with being staff at a gym.
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    # Set when someone else chose the password (a new staff login or a reset).
    # The person must choose their own before doing anything else.
    must_change_password = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.phone


class LoginThrottle(models.Model):
    """
    Counts wrong sign-in attempts for one phone number (see throttle.py).

    A row exists for any number that had a wrong attempt, whether or not an
    account exists for it, so this table never reveals who has an account.
    """

    phone = models.CharField(max_length=16, unique=True)
    failures = models.PositiveSmallIntegerField(default=0)

    # Wrong attempts are counted within a window that starts here.
    window_started_at = models.DateTimeField()

    # While this is in the future, sign-in for the number is refused.
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.phone


class DemoLogin(models.Model):
    """
    A short demo name and password (for example "gymowner") that signs in as a
    sample account, or opens a sample member's card. Only used when
    settings.DEMO_LOGINS is on (a developer's own computer). The sample
    account's real password is not changed.
    """

    name = models.CharField(max_length=40, unique=True)  # stored in lower case
    password = models.CharField(max_length=128)  # hashed, like any password
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE)
    url = models.CharField(max_length=300, blank=True)  # for people without a login
    label = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return self.name
