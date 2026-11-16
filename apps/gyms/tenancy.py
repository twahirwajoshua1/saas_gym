"""
Tenant isolation, lock 1: deciding which gym a request is acting for.

Every gym screen is wrapped in `@gym_required`. It answers two questions in
one place, on every single request:

    1. Which gym is this person acting for right now?
    2. Do they still have a role there, and is that role allowed on this screen?

If the answers are satisfactory the screen receives `request.gym` and
`request.assignment`. Screens never work this out for themselves.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from apps.core import tenant_db
from apps.gyms import services

SESSION_KEY = "current_gym"


def select_gym(request, assignment) -> None:
    """Remember which gym this person chose to work in."""
    request.session[SESSION_KEY] = str(assignment.gym.public_id)


def forget_gym(request) -> None:
    request.session.pop(SESSION_KEY, None)


def current_assignment(request):
    """
    The signed-in person's active role at the gym they chose, or None.

    The choice kept in the session is only a hint. It is checked against the
    database every time, so removing someone's role takes effect immediately,
    and a tampered session cannot open another gym.
    """
    gym_public_id = request.session.get(SESSION_KEY)
    if not gym_public_id:
        return None
    return services.find_active_assignment(person=request.user.person, gym_public_id=gym_public_id)


def gym_required(*allowed_roles):
    """
    Wrap a screen so that it only runs for staff of the current gym.

        @gym_required()                          any role at the gym
        @gym_required(Role.OWNER, Role.BRANCH_MANAGER)   only these roles
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            assignment = current_assignment(request)
            if assignment is None:
                forget_gym(request)
                return redirect("gyms:enter")
            if allowed_roles and assignment.role not in allowed_roles:
                raise PermissionDenied("Your role does not allow this.")
            request.assignment = assignment
            request.gym = assignment.gym
            # Lock 2: tell the database which gym this request acts for. From
            # here on it hides every other gym's rows, whatever our code asks for.
            tenant_db.set_current_gym(assignment.gym_id)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
