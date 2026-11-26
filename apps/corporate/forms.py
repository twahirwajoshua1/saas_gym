"""
Forms for the corporate screens. They check the shape of what was typed; the
rules live in services.py.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.corporate.models import Company
from apps.members.models import Service


class ReportFilterForm(forms.Form):
    """
    What the monthly report covers. Whole months only, because a company pays
    for whole months and a report has to match the bill.
    """

    company = forms.ModelChoiceField(
        label=_("Company"),
        queryset=Company.objects.none(),
        to_field_name="public_id",
        required=False,
        empty_label=_("All companies"),
    )
    service = forms.ModelChoiceField(
        label=_("Activity"),
        queryset=Service.objects.none(),
        to_field_name="public_id",
        required=False,
        empty_label=_("All activities"),
    )
    months = forms.TypedChoiceField(
        label=_("Period"),
        coerce=int,
        choices=[
            (1, _("1 month")),
            (2, _("2 months")),
            (3, _("3 months (a quarter)")),
            (4, _("4 months")),
            (5, _("5 months")),
            (6, _("6 months (half a year)")),
            (12, _("12 months (a year)")),
        ],
        initial=1,
    )
    last_month = forms.ChoiceField(label=_("Last month"))

    def __init__(self, *args, gym=None, companies=None, months=None, **kwargs):
        super().__init__(*args, **kwargs)
        if gym is not None:
            self.fields["service"].queryset = Service.objects.for_gym(gym)
        if companies is not None:
            self.fields["company"].queryset = companies
        self.fields["last_month"].choices = months or []
        if months:
            self.fields["last_month"].initial = months[0][0]
