"""Login screens. They collect input, call a service and show the result."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts import services
from apps.accounts.forms import LoginForm, PasswordChangeForm


def login_view(request):
    """Show the sign-in form and sign the person in when it is submitted."""
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        demo = services.demo_login(form.cleaned_data["phone"], form.cleaned_data["password"])
        if demo is not None:
            if demo.user is None:
                return redirect(demo.url)  # a member: their card page, no login
            login(request, demo.user, backend=settings.AUTHENTICATION_BACKENDS[0])
            return redirect(settings.LOGIN_REDIRECT_URL)
        try:
            user = services.authenticate_by_phone(
                request,
                phone=form.cleaned_data["phone"],
                password=form.cleaned_data["password"],
            )
        except services.TooManyAttempts as locked:
            form.add_error(
                None,
                "Too many wrong attempts. Sign-in for this number is locked for about "
                f"{locked.minutes_left} more minute(s).",
            )
        else:
            if user is not None:
                login(request, user)
                return redirect(settings.LOGIN_REDIRECT_URL)
            # One message for every failure, so nobody can use this page to
            # find out which phone numbers have accounts.
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


@login_required
def password_change(request):
    """Choose a new password. Required after a temporary password was given."""
    form = PasswordChangeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.change_own_password(
                user=request.user,
                current_password=form.cleaned_data["current_password"],
                new_password=form.cleaned_data["new_password"],
            )
        except services.WrongPassword:
            form.add_error("current_password", "This is not your current password.")
        except ValidationError as error:
            form.add_error("new_password", error)
        else:
            update_session_auth_hash(request, request.user)  # stay signed in
            messages.success(request, "Your password was changed.")
            return redirect(settings.LOGIN_REDIRECT_URL)
    return render(
        request,
        "accounts/password_change.html",
        {"form": form, "required": request.user.must_change_password},
    )
