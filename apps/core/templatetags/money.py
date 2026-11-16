"""Template helpers for showing money."""

from django import template

register = template.Library()


@register.filter
def rwf(amount):
    """Show a whole number of Rwandan francs, for example 30000 -> "RWF 30,000"."""
    try:
        return f"RWF {int(amount):,}"
    except (TypeError, ValueError):
        return ""
