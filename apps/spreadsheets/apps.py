from django.apps import AppConfig


class SpreadsheetsConfig(AppConfig):
    """Importing members from Excel or CSV, and exporting data to Excel."""

    name = "apps.spreadsheets"
    verbose_name = "Spreadsheets"
