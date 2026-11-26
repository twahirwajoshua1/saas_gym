"""Forms for selling memberships and recording payments. They check shape only."""

import uuid

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.billing.models import PaymentMethod
from apps.core.amounts import parse_rwf
from apps.members.models import Package, Service


class PackageChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, package):
        included = ", ".join(service.name for service in package.included_services.all())
        included = f" · {included}" if included else ""
        return f"{package.name} · RWF {package.price_rwf:,} · {package.rule_label}{included}"


class PayNow:
    FULL = "full"
    PART = "part"


# Every sale is paid for, in full or in part. There is no "pay later" at the
# desk: a membership starts when the money is in.
PAY_CHOICES = [
    (PayNow.FULL, _("Paid in full now")),
    (PayNow.PART, _("Part payment now")),
]


class _PaymentFields(forms.Form):
    """The inputs every payment needs. Shared by the two forms below."""

    amount_rwf = forms.CharField(
        label=_("Amount (RWF)"),
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "numeric"}),
    )
    method = forms.ChoiceField(label=_("Paid by"), choices=PaymentMethod.choices)
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
        label=_("Starts on"),
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("For a membership. A book of visits starts today."),
    )
    pay_now = forms.ChoiceField(
        label=_("Payment"),
        widget=forms.RadioSelect,
        initial=PayNow.FULL,
        choices=PAY_CHOICES,
    )
    field_order = ["package", "start_date", "pay_now", "amount_rwf", "method"]

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
    field_order = ["amount_rwf", "method"]

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


class ServiceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, service):
        return f"{service.name} · RWF {service.price_rwf:,}"


class SellSessionsForm(_PaymentFields):
    service = ServiceChoiceField(
        label=_("Service"),
        queryset=Service.objects.none(),
        to_field_name="public_id",
        widget=forms.RadioSelect,
        empty_label=None,
    )
    quantity = forms.IntegerField(label=_("Sessions"), min_value=1, max_value=50, initial=1)
    pay_now = forms.ChoiceField(
        label=_("Payment"),
        widget=forms.RadioSelect,
        initial=PayNow.FULL,
        choices=PAY_CHOICES,
    )
    field_order = ["service", "quantity", "pay_now", "amount_rwf", "method"]

    def __init__(self, *args, gym, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.for_gym(gym).filter(is_active=True)
        self.fields["amount_rwf"].help_text = _("Only for a part payment.")


class SignUpPaymentForm(_PaymentFields):
    """Taking the money from somebody who signed up on their own phone."""

    pay_now = forms.ChoiceField(
        label=_("Payment"), widget=forms.RadioSelect, initial=PayNow.FULL, choices=PAY_CHOICES
    )
    field_order = ["pay_now", "amount_rwf", "method"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount_rwf"].help_text = _("Only for a part payment.")

    def clean(self):
        data = super().clean()
        if data.get("pay_now") == PayNow.PART and data.get("amount_rwf") is None:
            if "amount_rwf" not in self.errors:
                self.add_error("amount_rwf", _("Enter how much was paid."))
        return data
