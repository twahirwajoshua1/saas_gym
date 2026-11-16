"""Login screens. They collect input, call a service and show the result."""

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts import services
from apps.accounts.forms import LoginForm


def login_view(request):
    """Show the sign-in form and sign the person in when it is submitted."""
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = services.authenticate_by_phone(
            request,
            phone=form.cleaned_data["phone"],
            password=form.cleaned_data["password"],
        )
        if user is not None:
            login(request, user)
            return redirect(settings.LOGIN_REDIRECT_URL)
        # One message for every failure, so nobody can use this page to find
        # out which phone numbers have accounts.
        form.add_error(None, "The phone number or password is not correct.")

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@login_required
def home(request):
    """A simple page showing who is signed in."""
    return render(request, "accounts/home.html", {"person": request.user.person})
