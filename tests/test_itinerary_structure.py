"""
Phase 10 (C) deterministic itinerary-structure test.
====================================================
`RouteAgent.format_daily_plan_to_itinerary` is the function that turns the
abstract per-day plan into the dated itinerary the user finally sees. The most
important correctness properties are:

  * one entry per planned day, in ascending day order;
  * each day gets a real calendar date derived from the start date;
  * dates are unique and consecutive (no repeated or out-of-order days).

We drive the function with a hand-built, location-resolved single-spot-per-day
plan so it performs **no network / no LLM / no geocoding** work: every spot is
tagged `_location_resolved=True` with an explicit `location`, and each day holds
exactly one spot so the TSP optimiser is bypassed (len <= 1). The RouteAgent is
created via `__new__` to skip the heavyweight InformationAgent constructor.
"""
from datetime import datetime, timedelta

import pytest

from agents.route_agent import RouteAgent


def _bare_route_agent():
    """Create a RouteAgent without running __init__ (no InformationAgent)."""
    ra = RouteAgent.__new__(RouteAgent)
    ra.api_key = None
    ra.distances_cache = {}
    ra.info_agent = None  # forces restaurant-lookup branches to be skipped
    return ra


def _spot(name, lat, lng):
    return {
        "id": name.replace(" ", "_").lower(),
        "name": name,
        "description": "Test attraction",
        "estimated_duration": 2,
        "location": {"lat": lat, "lng": lng},
        "category": "tourist_attraction",
        "_location_resolved": True,
    }


@pytest.fixture()
def plan_and_map():
    daily_plan = {
        "Day 1": ["Fort Kochi Beach"],
        "Day 2": ["Mattancherry Palace"],
        "Day 3": ["Alleppey Backwaters"],
        "Day 4": ["Munnar Tea Plantations"],
        "Day 5": ["Thekkumala Periyar"],
    }
    spot_map = {
        "Fort Kochi Beach": _spot("Fort Kochi Beach", 9.93, 76.26),
        "Mattancherry Palace": _spot("Mattancherry Palace", 9.96, 76.26),
        "Alleppey Backwaters": _spot("Alleppey Backwaters", 9.50, 76.34),
        "Munnar Tea Plantations": _spot("Munnar Tea Plantations", 10.09, 77.06),
        "Thekkumala Periyar": _spot("Thekkumala Periyar", 9.56, 76.96),
    }
    return daily_plan, spot_map


def test_itinerary_has_one_entry_per_day_in_order(plan_and_map):
    ra = _bare_route_agent()
    daily_plan, spot_map = plan_and_map
    itin = ra.format_daily_plan_to_itinerary(daily_plan, spot_map, "2026-11-05")

    assert isinstance(itin, list)
    assert len(itin) == 5, f"Expected 5 days, got {len(itin)}"
    assert [d["day"] for d in itin] == [1, 2, 3, 4, 5], "Days must be 1..5 in order"


def test_itinerary_dates_are_unique_and_consecutive(plan_and_map):
    ra = _bare_route_agent()
    daily_plan, spot_map = plan_and_map
    start = "2026-11-05"
    itin = ra.format_daily_plan_to_itinerary(daily_plan, spot_map, start)

    dates = [d["date"] for d in itin]
    # Unique dates
    assert len(set(dates)) == len(dates), f"Duplicate dates detected: {dates}"
    # Not flagged as a "suggested" (fallback) date because a valid start was given
    assert all(d.get("is_suggested_date") is False for d in itin), (
        "A valid start_date_str must not produce suggested/fallback dates"
    )
    # Consecutive: day N date == start + (N-1) days
    base = datetime.strptime(start, "%Y-%m-%d")
    expected = [(base + timedelta(days=i)).strftime("%d %b %Y") for i in range(5)]
    assert dates == expected, f"Expected {expected}, got {dates}"


def test_each_day_carries_its_activity(plan_and_map):
    ra = _bare_route_agent()
    daily_plan, spot_map = plan_and_map
    itin = ra.format_daily_plan_to_itinerary(daily_plan, spot_map, "2026-11-05")

    for day in itin:
        assert day["activities"], f"Day {day['day']} has no activities"
        # The single planned spot must be present somewhere in the day
        names = {a.get("name") for a in day["activities"]}
        planned = daily_plan[f"Day {day['day']}"]
        assert any(p in names for p in planned), (
            f"Day {day['day']} missing planned spot {planned}; got {names}"
        )


def test_invalid_start_date_is_flagged_not_crashed():
    ra = _bare_route_agent()
    daily_plan = {"Day 1": ["A"], "Day 2": ["B"]}
    spot_map = {"A": _spot("A", 1.0, 1.0), "B": _spot("B", 2.0, 2.0)}
    itin = ra.format_daily_plan_to_itinerary(daily_plan, spot_map, "flexible")
    assert len(itin) == 2
    assert all(d.get("is_suggested_date") is True for d in itin), (
        "An unparseable start date must produce transparent 'suggested' dates, not a crash"
    )
