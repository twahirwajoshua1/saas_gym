from django.apps import AppConfig


class PeopleConfig(AppConfig):
    """One record per human being, shared by every other module."""

    name = "apps.people"
    verbose_name = "People"
