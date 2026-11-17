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


@register.filter
def reduction(price, normal_price):
    """Percent `price` is below `normal_price`, as a whole number: 4000|reduction:5000 gives 20."""
    try:
        price, normal_price = int(price), int(normal_price)
    except (TypeError, ValueError):
        return ""
    if normal_price <= 0:
        return ""
    return round((normal_price - price) * 100 / normal_price)
