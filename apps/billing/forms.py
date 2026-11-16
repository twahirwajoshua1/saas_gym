"""Forms for selling memberships and recording payments. They check shape only."""

import uuid

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.billing.models import PaymentMethod
from apps.core.amounts import parse_rwf
from apps.members.models import Package


class PackageChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, package):
        return f"{package.name} · RWF {package.price_rwf:,} · {package.duration_label}"


class PayNow:
    FULL = "full"
    PART = "part"
    LATER = "later"


class _PaymentFields(forms.Form):
    """The inputs every payment needs. Shared by the two forms below."""

    amount_rwf = forms.CharField(
        label=_("Amount (RWF)"),
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "numeric"}),
    )
    method = forms.ChoiceField(label=_("Paid by"), choices=PaymentMethod.choices)
    reference = forms.CharField(
        label=_("Transaction reference"),
        max_length=60,
        required=False,
        help_text=_("Needed for mobile money, bank and card payments."),
    )
    # A one-time code, so the same form arriving twice is recorded once.
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def clean_amount_rwf(self):
        typed = self.cleaned_data["amount_rwf"]
        if not typed.strip():
            return None
        amount = parse_rwf(typed)
        if amount is None:
            raise forms.ValidationError(_("Enter the amount as a whole number of francs."))
        return amount


class SellMembershipForm(_PaymentFields):
    package = PackageChoiceField(
        label=_("Package"),
        queryset=Package.objects.none(),
        to_field_name="public_id",
        widget=forms.RadioSelect,
        empty_label=None,
    )
    start_date = forms.DateField(
        label=_("Starts on"), widget=forms.DateInput(attrs={"type": "date"})
    )
    pay_now = forms.ChoiceField(
        label=_("Payment"),
        widget=forms.RadioSelect,
        initial=PayNow.FULL,
        choices=[
            (PayNow.FULL, _("Paid in full now")),
            (PayNow.PART, _("Part payment now")),
            (PayNow.LATER, _("Nothing paid yet")),
        ],
    )
    field_order = ["package", "start_date", "pay_now", "amount_rwf", "method", "reference"]

    def __init__(self, *args, gym, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["package"].queryset = Package.objects.for_gym(gym).filter(is_active=True)
        self.fields["amount_rwf"].help_text = _("Only for a part payment.")

    def clean(self):
        data = super().clean()
        if data.get("pay_now") == PayNow.PART and data.get("amount_rwf") is None:
            if "amount_rwf" not in self.errors:
                self.add_error("amount_rwf", _("Enter how much was paid."))
        return data


class PaymentForm(_PaymentFields):
    field_order = ["amount_rwf", "method", "reference"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount_rwf"].required = True


class ReversalForm(forms.Form):
    reason = forms.CharField(
        label=_("Why is this payment being reversed?"),
        max_length=300,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("For example: entered twice, wrong member, wrong amount."),
    )
