"""Forms for the massage screens. Shape only; the rules live in services.py."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.amounts import parse_rwf


class MassageTypeForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=80, help_text=_("For example: Deep tissue"))
    minutes = forms.IntegerField(
        label=_("How long (minutes)"),
        min_value=5,
        max_value=240,
        initial=60,
        help_text=_("The length decides which times can be offered."),
    )
    price_rwf = forms.CharField(
        label=_("Price at the door (RWF)"),
        max_length=15,
        widget=forms.TextInput(attrs={"inputmode": "numeric"}),
        help_text=_(
            "What somebody pays who has no membership. Companies agree their "
            "own price in their contract."
        ),
    )
    what_to_expect = forms.CharField(
        label=_("What to expect"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_price_rwf(self):
        amount = parse_rwf(self.cleaned_data["price_rwf"])
        if amount is None:
            raise forms.ValidationError(_("Enter the price as a whole number of francs."))
        return amount


class MassageRuleForm(forms.Form):
    cut_off_minutes = forms.IntegerField(
        label=_("Called off free until (minutes before)"),
        min_value=0,
        max_value=1440,
        initial=120,
        help_text=_("After this the slot is still charged, because the masseuse held the time."),
    )
    gap_minutes = forms.IntegerField(
        label=_("Break between massages (minutes)"), min_value=0, max_value=120, initial=10
    )
    reminder_minutes = forms.IntegerField(
        label=_("Remind the desk this many minutes before"),
        min_value=0,
        max_value=120,
        initial=10,
    )


class ReasonForm(forms.Form):
    reason = forms.CharField(label=_("Why?"), max_length=200)


class NoteForm(forms.Form):
    note = forms.CharField(
        label=_("My note"),
        max_length=300,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Only you can see this. Leave it empty and nothing is shown."),
    )
