from django import forms
from django.utils.translation import gettext_lazy as _

from apps.gyms.models import Branch, Role


class StaffForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    phone = forms.CharField(
        label=_("Phone number"),
        max_length=20,
        help_text=_("They will sign in with this number."),
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "off"}),
    )
    role = forms.ChoiceField(label=_("Role"), choices=Role.choices, initial=Role.RECEPTIONIST)
    branch = forms.ModelChoiceField(
        label=_("Works at"),
        queryset=Branch.objects.none(),
        to_field_name="public_id",
        required=False,
        empty_label=_("All branches"),
        help_text=_("Owners always cover all branches."),
    )

    def __init__(self, *args, gym, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        branches = Branch.objects.for_gym(gym).filter(is_active=True)
        self.fields["branch"].queryset = branches
        if branches.count() < 2:
            del self.fields["branch"]
        if editing:
            del self.fields["full_name"]
            del self.fields["phone"]
