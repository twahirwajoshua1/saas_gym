"""
How a phone number and password are checked.

Django asks an "authentication backend" whether a sign-in is correct. This one
is Django's own (which does the password checking), with two additions:

  * the phone number may be written any way ("0788 123 456", "+250788123456");
  * wrong attempts are counted and the number is locked after too many.

Every sign-in goes through here: our own sign-in page and the platform admin's.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from apps.accounts import throttle
from apps.people.phones import InvalidPhoneNumber, normalize_phone


class ThrottledPhoneBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None

        try:
            phone = normalize_phone(username)
        except InvalidPhoneNumber:
            return None

        if throttle.locked_until(phone):
            # Refuse without even looking at the password.
            raise PermissionDenied("Sign-in for this number is temporarily locked.")

        user = super().authenticate(request, username=phone, password=password)
        if user is None:
            throttle.record_failure(phone)
        else:
            throttle.clear(phone)
        return user
