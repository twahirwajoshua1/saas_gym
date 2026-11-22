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


@register.filter
def thousands(amount):
    """A whole number with thousands separators and no currency: 30000 -> "30,000"."""
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return ""


@register.filter
def initials(name):
    """The first letters of the first two words of a name: "Eric Mugisha" -> "EM"."""
    words = [w for w in str(name or "").split() if w]
    return "".join(w[0].upper() for w in words[:2]) or "?"


@register.filter
def dictitem(mapping, key):
    """Look up a key in a dictionary from a template: {{ cells|dictitem:name }}."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def divide(amount, by):
    """Whole-number division for display (for example a price from a total and a count)."""
    try:
        return int(amount) // int(by) if int(by) else 0
    except (TypeError, ValueError):
        return 0
