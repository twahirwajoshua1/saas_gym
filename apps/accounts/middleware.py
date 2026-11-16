from django.shortcuts import redirect
from django.urls import reverse


class PasswordChangeRequiredMiddleware:
    """
    Someone signed in with a password chosen by somebody else (a new staff login
    or a reset) is sent to "Choose your password" until they have done so.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.must_change_password:
            allowed = {reverse("accounts:password_change"), reverse("accounts:logout")}
            if request.path not in allowed and not request.path.startswith("/static/"):
                return redirect("accounts:password_change")
        return self.get_response(request)
