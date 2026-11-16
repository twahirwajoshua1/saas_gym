from django.apps import AppConfig


class MemberAppConfig(AppConfig):
    """
    What members themselves see: their card, opened from a private link, and
    the same information as JSON (the first API endpoint).
    """

    name = "apps.memberapp"
    verbose_name = "Member app"
