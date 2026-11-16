"""
Working out the last day of a membership.

The last day is included: a 1-day pass starting on the 16th also ends on the
16th, and one month from 16 November ends on 15 December.
"""

import calendar
from datetime import date, timedelta

from apps.members.models import DurationUnit


def add_months(day: date, months: int) -> date:
    """The same day of the month, `months` later. 31 January + 1 month is the end of February."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day_of_month))


def end_date_for(start: date, count: int, unit: str) -> date:
    """The last valid day of a membership that starts on `start`."""
    if unit == DurationUnit.DAY:
        return start + timedelta(days=count - 1)
    if unit == DurationUnit.WEEK:
        return start + timedelta(days=7 * count - 1)

    months = count if unit == DurationUnit.MONTH else 12 * count
    anniversary = add_months(start, months)
    if anniversary.day < start.day:
        # The target month is shorter (31 Jan -> 28 Feb): its last day is the end.
        return anniversary
    return anniversary - timedelta(days=1)
