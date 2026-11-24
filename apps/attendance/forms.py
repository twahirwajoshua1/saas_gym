"""Forms for the desk. They collect; the services decide."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.gyms.models import Branch
from apps.members.models import Member


class GuestForm(forms.Form):
    """A friend a member brought in."""

    member = forms.ModelChoiceField(
        label=_("Which member brought them"),
        queryset=Member.objects.none(),
        to_field_name="public_id",
    )
    guest_name = forms.CharField(label=_("Friend's name"), max_length=150)
    guest_phone = forms.CharField(
        label=_("Friend's phone"),
        max_length=20,
        required=False,
        help_text=_("Only if they are happy to give it. It lets the gym call them back."),
        widget=forms.TextInput(attrs={"inputmode": "tel"}),
    )
    branch = forms.ModelChoiceField(
        label=_("Branch"),
        queryset=Branch.objects.none(),
        to_field_name="public_id",
        required=False,
    )

    def __init__(self, *args, gym, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = Member.objects.for_gym(gym).select_related("person")
        self.fields["branch"].queryset = Branch.objects.for_gym(gym).filter(is_active=True)
