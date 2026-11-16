from django.apps import AppConfig


class PlatformAdminConfig(AppConfig):
    """Screens for the people who run the platform itself (not gym staff)."""

    name = "apps.platformadmin"
    verbose_name = "Platform admin"
