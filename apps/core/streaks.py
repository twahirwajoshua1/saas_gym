"""
Weekly streaks: how many weeks in a row someone reached their visit goal.

Weeks run Monday to Sunday. A week counts when the person came at least
`goal` times (on different days). Each calendar month gives one "rest week":
the first missed week in a month is forgiven (it neither adds to the streak
nor breaks it). The week in progress only adds once the goal is reached; until
then it does not break anything.

Levels: Bronze from 4 weeks, Silver from 12, Gold from 26.

This file only does arithmetic on dates, so it is easy to test and is shared
by members (gym visits) and company staff (company visits).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

DEFAULT_GOAL = 2
LEVELS = [(26, "Gold"), (12, "Silver"), (4, "Bronze")]
WEEKS_SHOWN = 7


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def level_for(weeks: int) -> str:
    for minimum, name in LEVELS:
        if weeks >= minimum:
            return name
    return ""


def next_level(weeks: int) -> tuple[str, int] | None:
    """The next level and how many more weeks it needs, or None at Gold."""
    for minimum, name in reversed(LEVELS):
        if weeks < minimum:
            return name, minimum - weeks
    return None


@dataclass
class Streak:
    weeks: int
    goal: int
    this_week: int  # visits so far in the week in progress
    rest_left_this_month: bool
    # The last few weeks, oldest first and the week in progress last.
    # Each is "done", "rest", "missed" or "now".
    recent: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        return level_for(self.weeks)

    @property
    def next_level(self):
        return next_level(self.weeks)

    @property
    def visits_needed_this_week(self) -> int:
        return max(0, self.goal - self.this_week)


def compute(visit_days, today: date, goal: int = DEFAULT_GOAL, max_weeks: int = 104) -> Streak:
    """Work out a streak from the days someone visited (any iterable of dates)."""
    goal = max(1, int(goal))
    per_week: dict[date, set[date]] = {}
    for day in visit_days:
        per_week.setdefault(week_start(day), set()).add(day)

    current = week_start(today)
    this_week = len(per_week.get(current, ()))
    weeks = 1 if this_week >= goal else 0
    recent = ["done" if this_week >= goal else "now"]

    rest_used: set[tuple[int, int]] = set()
    week = current - timedelta(days=7)
    for _ in range(max_weeks):
        count = len(per_week.get(week, ()))
        month = (week.year, week.month)
        if count >= goal:
            weeks += 1
            status = "done"
        elif month not in rest_used:
            rest_used.add(month)
            status = "rest"
        else:
            if len(recent) < WEEKS_SHOWN:
                recent.append("missed")
            break
        if len(recent) < WEEKS_SHOWN:
            recent.append(status)
        week -= timedelta(days=7)

    # A rest at the very start of the record is not a real rest: there was no
    # streak to protect. Drop trailing rests from the picture.
    while len(recent) > 1 and recent[-1] == "rest" and weeks == 0:
        recent.pop()
    month_now = (current.year, current.month)
    return Streak(
        weeks=weeks,
        goal=goal,
        this_week=this_week,
        rest_left_this_month=month_now not in rest_used,
        recent=list(reversed(recent)),
    )
