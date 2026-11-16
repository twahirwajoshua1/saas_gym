from django import forms


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
