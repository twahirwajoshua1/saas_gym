"""
Slows down password guessing.

After MAX_FAILED_ATTEMPTS wrong passwords for the same phone number within
ATTEMPT_WINDOW, sign-in for that number is locked for LOCK_DURATION. During a
lock even the correct password is refused. A successful sign-in clears the count.

The count is kept per phone number, not per device, so it also works when many
people share one internet connection (as in a gym).
"""

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import LoginThrottle

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)


def locked_until(phone: str) -> datetime | None:
    """When the lock on this number ends, or None if it is not locked."""
    row = LoginThrottle.objects.filter(phone=phone).first()
    if row and row.locked_until and row.locked_until > timezone.now():
        return row.locked_until
    return None


@transaction.atomic
def record_failure(phone: str) -> None:
    """Count one wrong attempt, and lock the number if that makes too many."""
    now = timezone.now()
    row, _ = LoginThrottle.objects.select_for_update().get_or_create(
        phone=phone, defaults={"window_started_at": now}
    )
    if now - row.window_started_at > ATTEMPT_WINDOW:
        # The earlier wrong attempts are old enough to forget.
        row.failures = 0
        row.window_started_at = now

    row.failures += 1
    if row.failures >= MAX_FAILED_ATTEMPTS:
        row.locked_until = now + LOCK_DURATION
        row.failures = 0
        logger.warning("Sign-in locked for the number ending %s.", phone[-3:])
    row.save()


def clear(phone: str) -> bool:
    """Forget the wrong attempts and any lock. Returns True if there was something to forget."""
    deleted, _ = LoginThrottle.objects.filter(phone=phone).delete()
    return deleted > 0
