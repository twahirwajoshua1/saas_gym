"""Template helper for showing phone numbers the way people in Rwanda write them."""

import re

from django import template

register = template.Library()


@register.filter
def phone(number):
    """
    "+250788123456" -> "0788 123 456". Numbers from other countries are left as stored.

    This only changes how the number looks on screen. It is always stored and
    compared in the standard form.
    """
    number = number or ""
    if re.fullmatch(r"\+2507\d{8}", number):
        local = "0" + number[4:]
        return f"{local[:4]} {local[4:7]} {local[7:]}"
    return number
