"""Forms for classes and trainers. They collect; the services decide."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.classes.models import ClassType, TrainerProfile
from apps.gyms.models import Branch

WEEKDAYS = [
    (0, _("Monday")),
    (1, _("Tuesday")),
    (2, _("Wednesday")),
    (3, _("Thursday")),
    (4, _("Friday")),
    (5, _("Saturday")),
    (6, _("Sunday")),
]


class ClassTypeForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=80)
    what_to_expect = forms.CharField(
        label=_("What to expect"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("What happens in the hour, what to bring, who it suits."),
    )
    minutes = forms.IntegerField(label=_("How long (minutes)"), min_value=5, max_value=300)
    places = forms.IntegerField(label=_("Places"), min_value=1, max_value=500)


class ScheduleForm(forms.Form):
    class_type = forms.ModelChoiceField(
        label=_("Class"), queryset=ClassType.objects.none(), to_field_name="public_id"
    )
    branch = forms.ModelChoiceField(
        label=_("Branch"), queryset=Branch.objects.none(), to_field_name="public_id"
    )
    trainer = forms.ModelChoiceField(
        label=_("Trainer"),
        queryset=TrainerProfile.objects.none(),
        to_field_name="public_id",
        required=False,
        help_text=_("Leave empty until you know who is taking it."),
    )
    first_day = forms.DateField(
        label=_("First day"), widget=forms.DateInput(attrs={"type": "date"})
    )
    at = forms.TimeField(label=_("Starts at"), widget=forms.TimeInput(attrs={"type": "time"}))
    repeat_weeks = forms.IntegerField(
        label=_("Repeat for how many weeks"),
        min_value=1,
        max_value=12,
        initial=4,
        help_text=_("1 means this day only."),
    )

    def __init__(self, *args, gym, at_branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.classes import services

        self.fields["class_type"].queryset = services.list_class_types(gym)
        branches = Branch.objects.for_gym(gym).filter(is_active=True)
        self.fields["branch"].queryset = branches
        self.fields["trainer"].queryset = services.list_trainers(gym)
        if at_branch is not None and not self.is_bound:
            self.fields["branch"].initial = at_branch.public_id


class TrainerForm(forms.Form):
    headline = forms.CharField(
        label=_("One line about them"),
        max_length=120,
        required=False,
        help_text=_("For example: Strength and conditioning."),
    )
    about = forms.CharField(
        label=_("About"), required=False, widget=forms.Textarea(attrs={"rows": 4})
    )
    takes_personal_sessions = forms.BooleanField(
        label=_("Members may book one-to-one sessions"), required=False, initial=True
    )
    is_listed = forms.BooleanField(
        label=_("Show this trainer to members"), required=False, initial=True
    )


class HoursForm(forms.Form):
    """One window of free hours. Several are sent at once from the page."""

    weekday = forms.TypedChoiceField(label=_("Day"), choices=WEEKDAYS, coerce=int)
    branch = forms.ModelChoiceField(
        label=_("Branch"), queryset=Branch.objects.none(), to_field_name="public_id"
    )
    starts_at = forms.TimeField(label=_("From"), widget=forms.TimeInput(attrs={"type": "time"}))
    ends_at = forms.TimeField(label=_("To"), widget=forms.TimeInput(attrs={"type": "time"}))

    def __init__(self, *args, gym, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = Branch.objects.for_gym(gym).filter(is_active=True)


class ReviewForm(forms.Form):
    stars = forms.TypedChoiceField(
        label=_("Your rating"),
        choices=[(n, f"{'★' * n}{'☆' * (5 - n)}") for n in range(5, 0, -1)],
        coerce=int,
        widget=forms.RadioSelect,
    )
    words = forms.CharField(
        label=_("Anything to add?"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Everyone at the gym can read this."),
    )
