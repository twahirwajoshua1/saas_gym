from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

MAX_EXPORT_DAYS = 366


class UploadForm(forms.Form):
    file = forms.FileField(
        label=_("Spreadsheet"),
        help_text=_("Excel (.xlsx) or CSV, up to 5 MB and 2,000 members."),
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv"}),
    )


class DateRangeForm(forms.Form):
    start = forms.DateField(label=_("From"), widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label=_("To"), widget=forms.DateInput(attrs={"type": "date"}))

    @classmethod
    def this_month(cls, **kwargs):
        today = timezone.localdate()
        return cls(initial={"start": today.replace(day=1), "end": today}, **kwargs)

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if start and end:
            if start > end:
                raise forms.ValidationError(_("The start date is after the end date."))
            if end - start > timedelta(days=MAX_EXPORT_DAYS):
                raise forms.ValidationError(_("Choose a period of one year or less."))
        return data
