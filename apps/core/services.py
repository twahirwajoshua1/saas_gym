"""
Business logic for the core module.

Rule for the whole project: screens (views) never contain business rules.
They call functions in a `services.py` file like this one and display the result.
"""

from django.db import connection


def database_is_reachable() -> bool:
    """Return True if PostgreSQL answers a trivial question, otherwise False."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return False
    return True
