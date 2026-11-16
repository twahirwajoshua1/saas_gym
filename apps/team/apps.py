from django.apps import AppConfig


class TeamConfig(AppConfig):
    """The gym's staff: adding people with a role and a login, and managing them."""

    name = "apps.team"
    verbose_name = "Team"
