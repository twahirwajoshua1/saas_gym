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
from apps.corporate import services as corporate
from apps.gyms.models import Gym
from apps.members import services as members
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, CORPORATE_WELLNESS, PAYMENTS_FINANCE

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
    ending_soon_value: int = 0
    money: MoneyFigures | None = None
    visits: VisitFigures | None = None
    clients_today: int = 0
    clients_this_month: int = 0
    tablet_today: int = 0
    today_cash: int = 0
    today_mobile: int = 0
    per_day: list = field(default_factory=list)
    companies: list = field(default_factory=list)
    companies_total: int = 0
    cash_by_person: list = field(default_factory=list)
    active_by_package: list = field(default_factory=list)
    insights: list = field(default_factory=list)


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
        ending_soon_value=sum(m.price_rwf for m in soon),
        active_by_package=members.active_by_package(gym, today),
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
        result.clients_today = result.visits.today
        result.clients_this_month = (
            attendance.check_ins_between(gym, start, today).values("member_id").distinct().count()
        )
        result.tablet_today = attendance.check_ins_on(gym, today).filter(method="tablet").count()

    if result.money is not None:
        split = billing.collected_today_by_kind(gym, today)
        result.today_cash, result.today_mobile = split["cash"], split["mobile"] + split["other"]
        result.per_day = billing.collected_per_day(gym, start, today)
        result.cash_by_person = billing.cash_by_person_today(gym)

    if modules.is_enabled(gym, CORPORATE_WELLNESS):
        result.companies = corporate.gym_company_month(gym, today)
        result.companies_total = sum(r["amount_rwf"] for r in result.companies)
        company_people = corporate.visits_for_gym(gym, start, today)
        result.clients_this_month += company_people.values("employee_id").distinct().count()
        today_company = corporate.visits_for_gym(gym, today, today)
        result.clients_today += today_company.count()
        result.tablet_today += today_company.filter(method="tablet").count()

    result.insights = insights(gym, today, result)
    return result


def insights(gym: Gym, today: date, figures: "Overview") -> list[dict]:
    """
    A few plain sentences from the gym's own data, each with a link to act on.
    Only shown when the data supports them.
    """
    found = []
    if figures.ending_soon:
        found.append(
            {
                "icon": "refresh",
                "title": f"{figures.ending_soon} memberships end within {SOON_DAYS} days",
                "text": f"RWF {figures.ending_soon_value:,} at stake. Call them before they lapse.",
                "link": "dashboard:renewals",
                "link_text": "See who",
            }
        )
    if modules.is_enabled(gym, ATTENDANCE_ACCESS):
        hours = attendance.busiest_hours(gym, today - timedelta(days=28))
        if sum(n for _, n in hours) >= 20:
            by_hour = dict(hours)
            start_hour = max(by_hour, key=lambda h: by_hour[h] + by_hour.get(h + 1, 0))
            midday = sum(by_hour.get(h, 0) for h in range(9, 16))
            peak = by_hour[start_hour] + by_hour.get(start_hour + 1, 0)
            quiet_text = (
                "Mid-day (09:00–16:00) is quiet: a daytime offer could fill it."
                if midday < peak
                else "Visits are spread through the day."
            )
            found.append(
                {
                    "icon": "clock",
                    "title": f"Busiest: {start_hour:02d}:00–{start_hour + 2:02d}:00",
                    "text": f"From the last 4 weeks of check-ins. {quiet_text}",
                    "link": "",
                    "link_text": "",
                }
            )
        at_risk = attendance.streak_at_risk(gym, today)
        if at_risk:
            found.append(
                {
                    "icon": "spark",
                    "title": f"{len(at_risk)} members may lose their streak",
                    "text": "They came twice last week but not yet this week. "
                    "A friendly message helps.",
                    "link": "",
                    "link_text": "",
                    "people": [m.full_name for m in at_risk[:6]],
                }
            )
    for row in figures.companies:
        state = row["last_bill"]
        if state is not None and state.overdue:
            days = (today - state.bill.due_on).days
            found.append(
                {
                    "icon": "building",
                    "title": f"{row['company'].name}'s bill is {days} days late",
                    "text": f"RWF {state.owed_rwf:,} for {state.bill.period_start:%B}. "
                    "Remind their logistics officer.",
                    "link": "corporate:bill_list",
                    "link_text": "Open bills",
                }
            )
    return found[:5]


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
