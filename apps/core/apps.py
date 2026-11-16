from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Shared foundation used by every other module."""

    name = "apps.core"
    verbose_name = "Core"
