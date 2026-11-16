from django import forms


class PasswordChangeForm(forms.Form):
    current_password = forms.CharField(
        label="Current password",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    new_password = forms.CharField(
        label="New password",
        help_text="At least 8 characters. Not only numbers, and not a common password.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password_again = forms.CharField(
        label="New password again",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        data = super().clean()
        if data.get("new_password") and data.get("new_password") != data.get("new_password_again"):
            self.add_error("new_password_again", "The two new passwords are not the same.")
        return data


class LoginForm(forms.Form):
    phone = forms.CharField(
        label="Phone number",
        max_length=20,
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
