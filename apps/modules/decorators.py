"""
Protects a screen so it only works for gyms that have switched an application on.

Use it under @gym_required, which is what sets `request.gym`:

    @gym_required()
    @module_required(PAYMENTS_FINANCE)
    def payments_home(request): ...
"""

from functools import wraps

from django.core.exceptions import PermissionDenied

from apps.modules import services


def module_required(code: str):
    module = services.get_module(code)

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not services.is_enabled(request.gym, code):
                raise PermissionDenied(f"{module.name} is not switched on for this gym.")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
