"""
Forms for members and packages.

A form only checks the shape of what was typed (is this a number? a date?).
The business rules (is the name already used? is this phone already a member?)
live in services.py.
"""

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.amounts import parse_rwf
from apps.gyms.models import Branch
from apps.members.models import DurationUnit, Gender


class PackageForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=80, help_text=_("For example: Monthly"))
    price_rwf = forms.CharField(
        label=_("Price (RWF)"),
        max_length=15,
        help_text=_("Whole francs, for example 30000"),
        widget=forms.TextInput(attrs={"inputmode": "numeric"}),
    )
    duration_count = forms.IntegerField(label=_("Lasts for"), min_value=1, max_value=999)
    duration_unit = forms.ChoiceField(
        label=_("Unit"),
        choices=[
            (DurationUnit.DAY, _("day(s)")),
            (DurationUnit.WEEK, _("week(s)")),
            (DurationUnit.MONTH, _("month(s)")),
            (DurationUnit.YEAR, _("year(s)")),
        ],
        initial=DurationUnit.MONTH,
    )

    def clean_price_rwf(self):
        # People type prices as "30,000", "30 000" or "30000". All mean the same.
        amount = parse_rwf(self.cleaned_data["price_rwf"])
        if amount is None:
            raise forms.ValidationError(_("Enter the price as a whole number of francs."))
        return amount


class MemberForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    phone = forms.CharField(
        label=_("Phone number"),
        max_length=20,
        help_text=_("For example 0788 123 456"),
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "off"}),
    )
    branch = forms.ModelChoiceField(
        label=_("Signed up at"),
        help_text=_("Only where they registered. They may train at any branch."),
        queryset=Branch.objects.none(),
        to_field_name="public_id",
    )
    gender = forms.ChoiceField(
        label=_("Gender"), required=False, choices=[("", "—"), *Gender.choices]
    )
    date_of_birth = forms.DateField(
        label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    emergency_contact_name = forms.CharField(
        label=_("Emergency contact name"), max_length=150, required=False
    )
    emergency_contact_phone = forms.CharField(
        label=_("Emergency contact phone"),
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "tel"}),
    )
    notes = forms.CharField(
        label=_("Notes"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, gym, editing=False, at_branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        branches = Branch.objects.for_gym(gym).filter(is_active=True)
        self.fields["branch"].queryset = branches
        if at_branch is not None and not self.is_bound:
            # The desk's own branch, so staff do not answer a question they
            # already know the answer to. It is still theirs to change.
            self.fields["branch"].initial = at_branch.public_id
        self.single_branch = branches.count() == 1
        if self.single_branch:
            # Nothing to choose, so the question is not asked.
            del self.fields["branch"]
        if editing:
            # The phone number identifies the person and is not changed here.
            del self.fields["phone"]


class ExtendForm(forms.Form):
    days = forms.IntegerField(label=_("Add days"), min_value=1, max_value=365)
    reason = forms.CharField(
        label=_("Reason"),
        max_length=300,
        help_text=_("For example: the gym was closed for repairs."),
    )


class FreezeForm(forms.Form):
    start = forms.DateField(label=_("Freeze from"), widget=forms.DateInput(attrs={"type": "date"}))
    days = forms.IntegerField(label=_("For how many days"), min_value=1, max_value=90)
    reason = forms.CharField(
        label=_("Reason"), max_length=300, help_text=_("For example: travelling, or an injury.")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start"].initial = timezone.localdate()


class ServiceForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=80, help_text=_("For example: Sauna"))
    price_rwf = forms.CharField(
        label=_("Price for one session (RWF)"),
        max_length=15,
        help_text=_("What a member pays for one use. Company contracts set their own prices."),
        widget=forms.TextInput(attrs={"inputmode": "numeric"}),
    )

    def clean_price_rwf(self):
        amount = parse_rwf(self.cleaned_data["price_rwf"])
        if amount is None:
            raise forms.ValidationError(_("Enter the price as a whole number of francs."))
        return amount
