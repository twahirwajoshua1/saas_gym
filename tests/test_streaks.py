"""Tests for weekly streaks (apps/core/streaks.py): plain date arithmetic."""

from datetime import date, timedelta

from apps.core import streaks

MONDAY = date(2026, 11, 16)  # a Monday


def days(*offsets):
    return [MONDAY + timedelta(days=o) for o in offsets]


def test_no_visits_means_no_streak():
    s = streaks.compute([], MONDAY + timedelta(days=2))
    assert s.weeks == 0 and s.level == "" and s.visits_needed_this_week == 2


def test_weeks_that_reach_the_goal_add_up():
    visits = days(-21, -19, -14, -12, -7, -5)  # three full weeks before this one
    s = streaks.compute(visits, MONDAY + timedelta(days=1))
    assert s.weeks == 3
    assert s.recent[-1] == "now"


def test_this_week_counts_once_the_goal_is_reached():
    visits = days(-7, -5, 0, 1)
    assert streaks.compute(visits, MONDAY + timedelta(days=2)).weeks == 2


def test_two_visits_on_the_same_day_count_once():
    s = streaks.compute([MONDAY, MONDAY], MONDAY + timedelta(days=3))
    assert s.this_week == 1 and s.weeks == 0


def test_one_missed_week_a_month_is_forgiven_but_not_two():
    # Weeks starting 26 Oct, 2 Nov (missed), 9 Nov; today in the week of 16 Nov.
    visits = days(-21, -20, -7, -6)
    s = streaks.compute(visits, MONDAY + timedelta(days=1))
    assert s.weeks == 2 and "rest" in s.recent
    # Missing two weeks of November (2 and 9 Nov) breaks it.
    visits = days(-21, -20)
    s = streaks.compute(visits, MONDAY + timedelta(days=1))
    assert s.weeks == 0


def test_levels():
    assert streaks.level_for(3) == ""
    assert streaks.level_for(4) == "Bronze"
    assert streaks.level_for(12) == "Silver"
    assert streaks.level_for(30) == "Gold"
    assert streaks.next_level(6) == ("Silver", 6)
    assert streaks.next_level(26) is None


def test_a_bigger_goal_is_harder():
    visits = days(-7, -5)
    assert streaks.compute(visits, MONDAY, goal=2).weeks == 1
    assert streaks.compute(visits, MONDAY, goal=3).weeks == 0
