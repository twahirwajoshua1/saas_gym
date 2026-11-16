"""Screens for the core module. They only call services and show the result."""

from django.shortcuts import render

from apps.core import services


def health(request):
    """A simple page that shows whether the application and database are working."""
    database_ok = services.database_is_reachable()
    status_code = 200 if database_ok else 503
    return render(request, "core/health.html", {"database_ok": database_ok}, status=status_code)
