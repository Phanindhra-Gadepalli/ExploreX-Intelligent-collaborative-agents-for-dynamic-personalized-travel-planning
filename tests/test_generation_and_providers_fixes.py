"""
Regression tests for the "itinerary / transit not generating + Geoapify/Overpass
errors" repair pass.

Everything external is mocked, so these run offline and deterministically:

* Geoapify attraction query must use ONLY valid Geoapify category keys (the old
  `heritage.*` / `tourism.museum` / `amenity.place_of_worship` keys made the
  entire Places request fail with HTTP 400, so no POIs ever came back).
* Overpass must default to a mirror list that includes the more reliable
  kumi.systems endpoint and honour the OVERPASS_TIMEOUT env var.
* The budget level must reflect per-person-per-day spend, so a ₹150,000 pot for
  10 people over 10 days is treated as a *budget* trip (not "high"), which is
  what was inflating cost estimates and triggering false infeasibility.
* A strict budget that cannot be met must NEVER abort plan generation. The
  communication step must keep flowing toward the route step, and the route step
  must return a COMPLETE itinerary plus transit options alongside the warning
  (previously both returned an empty itinerary and dropped transit_options,
  producing the "0-Day Itinerary" and empty Transport tab).
"""
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Geoapify categories
# ---------------------------------------------------------------------------

_INVALID_GEOAPIFY_KEYS = [
    "heritage.", "tourism.museum", "tourism.viewpoint", "tourism.gallery",
    "tourism.artwork", "tourism.zoo_aquarium", "tourism.wildlife_park",
    "leisure.garden", "leisure.nature_reserve", "leisure.water_park",
    "natural.landform", "natural.beach", "natural.spring",
    "amenity.place_of_worship", "amenity.theatre", "amenity.marketplace",
    "shop.museum_store_art_gallery",
]


def test_geoapify_attraction_categories_are_all_valid_keys():
    """The categories sent to Geoapify must be drawn from its real taxonomy."""
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    pm.geoapify_key = "fake-key"
    pm.pexels_key = ""

    captured = {}

    class _Resp:
        def raise_for_status(self):  # noqa: D401
            return None

        def json(self):
            return {"features": []}

    def _fake_get(url, params=None, timeout=None):
        captured["params"] = params or {}
        return _Resp()

    with patch("services.poi_fallback.requests.get", side_effect=_fake_get):
        pm._fetch_geoapify_attractions(lat=27.04, lng=88.26, radius=20000, hobbies=None)

    categories = captured["params"]["categories"]
    for bad in _INVALID_GEOAPIFY_KEYS:
        assert bad not in categories, f"Invalid Geoapify category {bad!r} would trigger HTTP 400"

    # At least a few genuine Geoapify keys must be present.
    for good in ("tourism.attraction", "tourism.sights", "entertainment.museum", "natural.water"):
        assert good in categories, f"Expected valid Geoapify category {good!r} in {categories!r}"


# ---------------------------------------------------------------------------
# Overpass endpoints / timeout
# ---------------------------------------------------------------------------

def test_overpass_default_endpoints_include_kumi_mirror():
    from services.poi_fallback import POIManager

    with patch.dict("os.environ", {"OVERPASS_ENDPOINTS": ""}, clear=False):
        pm = POIManager()
    assert any("kumi.systems" in e for e in pm.overpass_endpoints), (
        "The kumi.systems Overpass mirror should be part of the default rotation"
    )


def test_overpass_query_honours_timeout_env_and_bounds_retries():
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    pm.overpass_endpoints = ["https://example.invalid/api/interpreter"]
    pm.overpass_cooldowns = {}

    seen = {}
    sleeps = []

    def _fake_post(url, data=None, headers=None, timeout=None):
        seen.setdefault("timeouts", []).append(timeout)
        seen["headers"] = headers
        raise requests_timeout_stub()

    with patch.dict("os.environ", {"OVERPASS_TIMEOUT": "33", "OVERPASS_CONNECT_TIMEOUT": "4"}, clear=False), \
         patch("services.poi_fallback.requests.post", side_effect=_fake_post), \
         patch("services.poi_fallback.time.sleep", lambda s: sleeps.append(s)):
        result = pm._execute_overpass_query("[out:json];out;")

    assert result == [], "A fully failing Overpass cascade must degrade to an empty list"
    # Timeout is a (connect, read) pair: a slow DNS/TLS handshake must not be
    # given the full 33s read allowance, and the read value still follows env.
    assert seen["timeouts"], "Overpass must pass an explicit timeout"
    for t in seen["timeouts"]:
        assert isinstance(t, tuple) and len(t) == 2, f"Expected (connect, read) tuple, got {t!r}"
        assert t[0] == pytest.approx(4.0)
        assert t[1] == pytest.approx(33.0), "OVERPASS_TIMEOUT must drive the read timeout"
    # Retries stay bounded (no retry storm against a mirror that is not answering)
    assert len(seen["timeouts"]) <= 3, f"Expected at most 3 attempts, got {len(seen['timeouts'])}"
    # A real User-Agent is required by Overpass' fair-use policy
    assert "User-Agent" in seen["headers"]
    # Backoff sleeps must carry jitter, otherwise concurrent clients retry in lockstep
    assert sleeps and any(s != int(s) for s in sleeps), f"Expected jittered backoff, got {sleeps}"


def requests_timeout_stub():
    import requests
    return requests.exceptions.Timeout("boom")


# ---------------------------------------------------------------------------
# Budget level classification
# ---------------------------------------------------------------------------

def test_budget_level_uses_per_person_per_day():
    from agents.budget_agent import BudgetAgent

    ba = BudgetAgent()

    # ₹150,000 for 10 people / 10 days = ₹1,500 pp/day -> a genuine budget trip.
    level, amount, _ = ba._resolve_budget_level(
        {"budget_amount": 150000, "people": 10, "days": 10, "budget": "high"}
    )
    assert level == "low", "1500/person/day must classify as low, not high"
    assert amount == 150000

    # ₹150,000 for 1 person / 3 days = ₹50,000 pp/day -> luxury.
    level2, _, _ = ba._resolve_budget_level(
        {"budget_amount": 150000, "people": 1, "days": 3}
    )
    assert level2 == "high"

    # A mid-range group trip lands on "medium" (₹80,000 / 4 / 5 = ₹4,000 pp/day).
    level3, _, _ = ba._resolve_budget_level(
        {"budget_amount": 80000, "people": 4, "days": 5}
    )
    assert level3 == "medium"


def test_budget_level_absolute_fallback_without_dimensions():
    from agents.budget_agent import BudgetAgent

    ba = BudgetAgent()
    level, _, _ = ba._resolve_budget_level({"budget_amount": 25000})
    assert level == "low"
    level_hi, _, _ = ba._resolve_budget_level({"budget_amount": 500000})
    assert level_hi == "high"


# ---------------------------------------------------------------------------
# Itinerary / transit must never be aborted by a strict-budget shortfall
# ---------------------------------------------------------------------------

def _bare_workflow():
    """A TravelGraph instance with stubbed agents and a controllable state."""
    from workflows.travel_graph import TravelGraph

    wf = TravelGraph.__new__(TravelGraph)
    wf.state = {
        "user_info": {
            "origin_city": "Hyderabad", "city": "Darjeeling", "days": 10,
            "people": 10, "budget": "high", "budget_amount": 150000,
            "budget_strictness": "strict", "start_date": "2026-10-31",
            "is_international": False,
        },
        "attractions": [
            {"name": "Tiger Hill", "location": {"lat": 27.0, "lng": 88.2}},
            {"name": "Batasia Loop", "location": {"lat": 27.01, "lng": 88.26}},
        ],
        "selected_attractions": [{"name": "Tiger Hill"}],
        "daily_plan": {"day1": ["Tiger Hill", "Batasia Loop"]},
        "should_rent_car": False,
        "transit_options": {
            "intercity_transport": [{"mode": "train", "estimated_cost": 1200}],
            "recommended_mode": "train",
        },
        "budget": {
            "budget_type": "strict",
            "budget_infeasible": True,
            "budget_warning": "Over the strict budget.",
            "estimated_total": 200000,
            "budget_limit": 150000,
        },
        "email_sent": True,  # skip the auto-send path
    }
    wf.transit_agent = MagicMock()
    wf.transit_agent.get_transit_options.return_value = {
        "intercity_transport": [{"mode": "train", "estimated_cost": 1200}],
        "recommended_mode": "train",
    }
    wf.info_agent = MagicMock()
    wf.budget_agent = MagicMock()
    wf.budget_agent.estimate_budget.return_value = {
        "budget_type": "strict",
        "budget_infeasible": True,
        "budget_warning": "Over the strict budget.",
        "estimated_total": 200000,
        "budget_limit": 150000,
    }
    wf.comm_agent = MagicMock()
    wf.comm_agent.generate_travel_tips_and_summary.return_value = "tips"
    wf.comm_agent.generate_booking_confirmation.return_value = "confirmed"
    wf.route_agent = MagicMock()
    wf.route_agent.format_daily_plan_to_itinerary.return_value = [
        {"day": 1, "date": "31 Oct 2026",
         "activities": [{"name": "Tiger Hill"}, {"name": "Batasia Loop"}]}
    ]
    wf.route_agent._sort_spots_by_time = lambda spots: spots
    return wf


def test_communication_does_not_abort_on_strict_infeasible_budget():
    wf = _bare_workflow()
    result = wf._process_communication(user_input="continue")

    # It must keep flowing (pause at 'communication' so the route step runs),
    # NOT jump straight to 'complete' with an empty itinerary.
    assert result.get("next_step") == "communication", (
        "A strict-budget shortfall must not short-circuit the workflow to 'complete'"
    )
    assert result.get("itinerary", "MISSING") != [], (
        "communication must never return an empty itinerary that nukes the plan"
    )


def test_route_returns_full_itinerary_and_transit_even_when_over_budget():
    wf = _bare_workflow()
    result = wf._process_route()

    assert result.get("next_step") == "complete"
    assert result.get("itinerary"), "route must deliver a non-empty itinerary"
    assert len(result["itinerary"]) >= 1
    assert result["itinerary"][0]["activities"], "the day must keep its activities"
    assert result.get("transit_options"), "route must return transit options"
    # The over-budget condition is surfaced as an advisory flag, not a hard stop.
    assert result.get("budget_infeasible") is True
    assert result.get("budget_warning")
