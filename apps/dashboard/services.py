"""
Putting the owner's figures together.

Each number is worked out by the module that owns the data (members, billing,
attendance). This file only decides which figures to show and for which dates.
Figures for an application the gym has not switched on are left out (None).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from apps.attendance import services as attendance
from apps.billing import services as billing
from apps.gyms.models import Gym
from apps.members import services as members
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE

SOON_DAYS = 7
LATER_DAYS = 30
LAPSED_DAYS = 30
NOT_VISITING_DAYS = 14


def month_start(day: date) -> date:
    return day.replace(day=1)


def previous_month(day: date) -> tuple[date, date]:
    """The first and last day of the month before `day`'s month."""
    last = month_start(day) - timedelta(days=1)
    return month_start(last), last


def with_shares(rows: list[dict]) -> list[dict]:
    """Add `share` (0 to 100) to each row, relative to the largest, for drawing bars."""
    largest = max((row["amount"] for row in rows), default=0)
    for row in rows:
        row["share"] = round(row["amount"] * 100 / largest) if largest else 0
    return rows


@dataclass
class MoneyFigures:
    collected_today: int
    collected_this_month: int
    collected_last_month: int
    owed_by_members: int
    by_method: list[dict]
    by_package: list[dict]


@dataclass
class VisitFigures:
    today: int
    this_month: int
    not_visiting: int


@dataclass
class Overview:
    today: date
    month_start: date
    active_members: int
    new_members_this_month: int
    ending_soon: int
    lapsed: int
    ending_soon_list: list = field(default_factory=list)
    money: MoneyFigures | None = None
    visits: VisitFigures | None = None


def overview(gym: Gym, today: date) -> Overview:
    start = month_start(today)
    soon = members.ending_soon(gym, today, SOON_DAYS)
    result = Overview(
        today=today,
        month_start=start,
        active_members=members.members_with_valid_membership(gym, today).count(),
        new_members_this_month=members.new_member_count(gym, start, today),
        ending_soon=soon.count(),
        lapsed=members.lapsed(gym, today, LAPSED_DAYS).count(),
        ending_soon_list=list(soon[:8]),
    )

    if modules.is_enabled(gym, PAYMENTS_FINANCE):
        last_start, last_end = previous_month(today)
        result.money = MoneyFigures(
            collected_today=billing.collected_between(gym, today, today),
            collected_this_month=billing.collected_between(gym, start, today),
            collected_last_month=billing.collected_between(gym, last_start, last_end),
            owed_by_members=billing.total_outstanding(gym),
            by_method=with_shares(billing.collected_by_method(gym, start, today)),
            by_package=with_shares(billing.sales_by_package(gym, start, today)),
        )

    if modules.is_enabled(gym, ATTENDANCE_ACCESS):
        result.visits = VisitFigures(
            today=attendance.check_in_count(gym, today, today),
            this_month=attendance.check_in_count(gym, start, today),
            not_visiting=attendance.not_visiting(gym, today, NOT_VISITING_DAYS).count(),
        )
    return result


class RenewalView:
    SOON = "soon"
    LATER = "later"
    LAPSED = "lapsed"


def renewals(gym: Gym, today: date, view: str) -> list:
    """
    The memberships for one tab of the Renewals page, each with `owes_rwf`
    added when the gym uses Payments and Finance.
    """
    if view == RenewalView.LAPSED:
        memberships = members.lapsed(gym, today, LAPSED_DAYS)
    else:
        days = LATER_DAYS if view == RenewalView.LATER else SOON_DAYS
        memberships = members.ending_soon(gym, today, days)
    memberships = list(memberships[:200])

    balances = {}
    if modules.is_enabled(gym, PAYMENTS_FINANCE):
        balances = billing.balances_for(gym, {m.member_id for m in memberships})
    for membership in memberships:
        membership.owes_rwf = balances.get(membership.member_id, 0)
        membership.days_from_today = (membership.end_date - today).days
    return memberships
