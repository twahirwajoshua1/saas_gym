"""
Forms for the desk. They check shape only; the rules live in the services.
"""

import uuid

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.billing.models import PaymentMethod
from apps.massage.models import MassageType
from apps.members.models import Service


class ServiceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, service):
        return f"{service.name} · RWF {service.price_rwf:,}"


class MassageChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, massage_type):
        return f"{massage_type.name} · {massage_type.minutes} min · RWF {massage_type.price_rwf:,}"


class DoorSaleForm(forms.Form):
    """
    Somebody at the desk who is not a member, paying for one visit.

    A name and a phone number are all that is asked for. The phone number is
    how the gym knows them again, and how the receipt reaches them.
    """

    full_name = forms.CharField(label=_("Name"), max_length=150)
    phone = forms.CharField(
        label=_("Phone number"),
        max_length=20,
        help_text=_("For example 0788 123 456. The receipt goes to this number."),
        widget=forms.TextInput(attrs={"inputmode": "tel"}),
    )
    service = ServiceChoiceField(
        label=_("What are they having?"),
        queryset=Service.objects.none(),
        empty_label=None,
        required=False,
        widget=forms.RadioSelect,
    )
    massage_type = MassageChoiceField(
        label=_("Or a massage"),
        queryset=MassageType.objects.none(),
        required=False,
        widget=forms.RadioSelect,
    )
    method = forms.ChoiceField(label=_("Paid by"), choices=PaymentMethod.choices)
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    field_order = ["full_name", "phone", "service", "massage_type", "method"]

    def __init__(self, *args, services=None, massage_types=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = (
            services if services is not None else Service.objects.none()
        )
        self.fields["massage_type"].queryset = (
            massage_types if massage_types is not None else MassageType.objects.none()
        )
        if not self.fields["massage_type"].queryset.exists():
            del self.fields["massage_type"]

    def clean(self):
        cleaned = super().clean()
        service = cleaned.get("service")
        massage_type = cleaned.get("massage_type")
        if service is None and massage_type is None:
            raise forms.ValidationError(_("Choose what they are paying for."))
        if service is not None and massage_type is not None:
            raise forms.ValidationError(_("Choose one thing at a time."))
        return cleaned
