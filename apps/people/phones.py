"""
Phone numbers are how we recognise a person, so every number is stored in one
standard form: a plus sign, the country code, then the number.

    "0788 123 456"   -> "+250788123456"
    "250788123456"   -> "+250788123456"
    "+250788123456"  -> "+250788123456"

Numbers written without a country code are assumed to be Rwandan.
"""

import re

RWANDA_COUNTRY_CODE = "250"
RWANDA_MOBILE_PATTERN = re.compile(r"2507\d{8}")


class InvalidPhoneNumber(ValueError):
    """The text given is not a phone number we can accept."""


def normalize_phone(raw: str) -> str:
    """Return the phone number in standard form, or raise InvalidPhoneNumber."""
    cleaned = re.sub(r"[\s\-().]", "", raw or "")

    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        digits = cleaned[1:]
    elif cleaned.startswith(RWANDA_COUNTRY_CODE) and len(cleaned) == 12:
        digits = cleaned
    elif cleaned.startswith("0") and len(cleaned) == 10:
        digits = RWANDA_COUNTRY_CODE + cleaned[1:]
    elif cleaned.startswith("7") and len(cleaned) == 9:
        digits = RWANDA_COUNTRY_CODE + cleaned
    else:
        raise InvalidPhoneNumber(f"'{raw}' is not a valid phone number.")

    if not digits.isdigit() or not 8 <= len(digits) <= 15:
        raise InvalidPhoneNumber(f"'{raw}' is not a valid phone number.")

    is_rwandan = digits.startswith(RWANDA_COUNTRY_CODE)
    if is_rwandan and not RWANDA_MOBILE_PATTERN.fullmatch(digits):
        raise InvalidPhoneNumber(f"'{raw}' is not a valid Rwandan mobile number.")

    return "+" + digits
