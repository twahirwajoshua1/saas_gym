from django.apps import AppConfig


class AuditConfig(AppConfig):
    """The permanent record of who did what, and when."""

    name = "apps.audit"
    verbose_name = "Audit log"
