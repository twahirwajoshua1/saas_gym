"""
Tenant isolation, lock 2: the database enforces the gym boundary itself.

Lock 1 (apps/gyms/tenancy.py) makes our code ask for the right gym's rows.
Lock 2 makes PostgreSQL refuse to hand out other gyms' rows even if our code
forgets to ask. It works like this:

  1. Every web request switches the database connection to a restricted role
     (`gymos_app`). That role is subject to row-level security.
  2. We tell the database who is asking (`app.current_person_id`) and, once the
     gym has been verified, which gym they are acting for (`app.current_gym_id`).
  3. Each gym-owned table has a policy: a row is visible only if it belongs to
     the current gym. With no gym set, nothing is visible.
  4. When the request ends, the connection switches back to the normal user.

Code that runs outside a web request (migrations, management commands, tests
that set data up) and the platform admin pages use the normal database user,
which is not restricted. That is deliberate: it is how the platform owner works
across gyms. Anything new that uses it must be reviewed with that in mind.
"""

import logging

from django.db import connection

logger = logging.getLogger(__name__)

# The restricted role. It is created by migration core.0001.
APP_ROLE = "gymos_app"


def _set_setting(cursor, name: str, value) -> None:
    cursor.execute("SELECT set_config(%s, %s, false)", [name, "" if value is None else str(value)])


def enter_app_role(*, person_id) -> None:
    """Switch this connection to the restricted role for the rest of the request."""
    with connection.cursor() as cursor:
        cursor.execute(f"SET ROLE {connection.ops.quote_name(APP_ROLE)}")
        _set_setting(cursor, "app.current_person_id", person_id)
        _set_setting(cursor, "app.current_gym_id", None)


def set_current_gym(gym_id) -> None:
    """Tell the database which gym this request is acting for (already verified)."""
    with connection.cursor() as cursor:
        _set_setting(cursor, "app.current_gym_id", gym_id)


def leave_app_role() -> None:
    """Switch back to the normal database user and forget who was asking."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            _set_setting(cursor, "app.current_person_id", None)
            _set_setting(cursor, "app.current_gym_id", None)
    except Exception:
        # If the connection is unusable the request is already failing; closing
        # it makes sure the restricted settings cannot leak into another request.
        logger.exception("Could not reset the database role; closing the connection.")
        connection.close()
