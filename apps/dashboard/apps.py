from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """
    The owner's figures and the renewals list.

    This module only reads. It collects figures from the other modules through
    their services and puts them together, so it sits above all of them.
    """

    name = "apps.dashboard"
    verbose_name = "Dashboard"
