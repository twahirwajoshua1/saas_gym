"""Tells every page template which applications the current gym has switched on."""

from apps.modules import services


def enabled_modules(request):
    gym = getattr(request, "gym", None)
    if gym is None:
        return {}
    return {"modules_on": services.enabled_codes(gym)}
