"""Tests for turning phone numbers into one standard form."""

import pytest

from apps.people.phones import InvalidPhoneNumber, normalize_phone


@pytest.mark.parametrize(
    "written",
    [
        "0788123456",
        "0788 123 456",
        "788123456",
        "250788123456",
        "+250788123456",
        "+250 788-123-456",
    ],
)
def test_rwandan_numbers_written_differently_become_the_same(written):
    assert normalize_phone(written) == "+250788123456"


def test_a_foreign_number_with_country_code_is_accepted():
    assert normalize_phone("+254 712 345678") == "+254712345678"


@pytest.mark.parametrize("written", ["", "hello", "12345", "0788", "+250188123456", "07881234567"])
def test_invalid_numbers_are_rejected(written):
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone(written)
