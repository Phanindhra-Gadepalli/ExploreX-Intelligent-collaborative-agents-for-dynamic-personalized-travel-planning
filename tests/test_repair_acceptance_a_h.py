"""Acceptance tests A-H for the October-2025 Japan-trip repair.

Every external dependency is mocked: no network, no LLM, no SMTP.  The suite
is organised exactly as the repair specification's verification matrix:

A  Destination scope       country / region / city / multi-city / unknown
B  Interest matching       "viewpoints and photography" is not a dead end
C  Retrieval parsing       fenced / prose-wrapped / concatenated / trailing comma
D  Strict budget           below-minimum budget is declared, never optimised away
E  Provider failure        partial success survives, total failure invents nothing
F  Itinerary integrity     every selection once, N days, duplicates removed
G  Health / resources      /health is secret-free, workflow store bounded, sockets closed
H  End-to-end Japan trip   Tokyo/Kyoto/Osaka, viewpoints, strict Rs 40,000, mocked providers
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# A - destination scope
# ---------------------------------------------------------------------------

def test_a_country_request_is_not_collapsed_to_one_locality():
    from services.destination_resolver import (
        disclosure_text,
        resolve_destination,
        scope_city_names,
        validate_point_for_scope,
    )

    resolved = resolve_destination("Japan")

    assert resolved.kind == "country"
    assert resolved.scope_status == "proposed"
    assert resolved.is_international is True

    cities = scope_city_names(resolved)
    assert {"Tokyo", "Kyoto", "Osaka"}.issubset(set(cities)), (
        f"Japan proposal must include the requested cities, got {cities!r}"
    )
    assert len(cities) >= 5
    assert all(c.get("lat") is not None and c.get("lng") is not None
               for c in resolved.cities)

    disclosure = disclosure_text(resolved)
    assert "whole country" in disclosure.lower()
    assert "editable" in disclosure.lower()

    # A POI in Tokyo is in scope; a POI in Tohoku or Paris is not.
    assert validate_point_for_scope(35.6586, 139.7454, resolved)  # Tokyo Tower
    assert not validate_point_for_scope(39.0, 141.0, resolved)    # Tohoku
    assert not validate_point_for_scope(48.8566, 2.3522, resolved)  # Paris


def test_a_city_region_and_multi_city_are_preserved():
    from services.destination_resolver import resolve_destination, scope_city_names

    tokyo = resolve_destination("Tokyo")
    assert tokyo.kind == "city"
    assert scope_city_names(tokyo) == ["Tokyo"]
    assert tokyo.scope_status == "resolved"

    region = resolve_destination("Hokkaido")
    assert region.kind == "region"
    assert "Sapporo" in scope_city_names(region)

    multi = resolve_destination("Tokyo, Kyoto, Osaka")
    assert multi.kind == "multi_city"
    assert scope_city_names(multi) == ["Tokyo", "Kyoto", "Osaka"]


def test_a_unknown_country_requires_clarification_not_a_centroid():
    from services.destination_resolver import (
        disclosure_text,
        resolve_destination,
    )

    fake_geo = SimpleNamespace(
        lat=1.0, lng=2.0, feature_type="country", place_type="country",
        country="Atlantis", country_code="zz", city="", state="",
        provider="fake_geocoder", display_name="Atlantis",
    )
    geocoder = MagicMock()
    geocoder.geocode.return_value = fake_geo

    resolved = resolve_destination("Atlantis", geocoder=geocoder)
    assert resolved.kind == "country"
    assert resolved.cities == []
    assert resolved.needs_clarification() is True
    assert "which region or cities" in disclosure_text(resolved).lower()


# ---------------------------------------------------------------------------
# B - interest matching
# ---------------------------------------------------------------------------

def _interest_prefs():
    return {"hobbies": "viewpoints and photography"}


def test_b_viewpoints_and_photography_become_canonical_groups():
    from services.interest_match import match_interests, normalize_interests

    groups = normalize_interests("Viewpoints and photography")
    assert "viewpoints" in groups
    assert "photography" in groups

    # A Japanese-named observation deck is matched through structured OSM tags,
    # not through an English-only keyword list.
    japanese_deck = {"name": "展望台", "osm_tags": {"tourism": "viewpoint"}}
    matched, reasons = match_interests(japanese_deck, _interest_prefs())
    assert "viewpoints" in matched
    assert "photography" in matched
    assert reasons, "the UI must be able to explain why a place matched"


def test_b_popularity_cannot_rescue_an_interest_mismatch():
    from agents.information_agent import InformationAgent
    from services.interest_match import interest_score

    prefs = _interest_prefs()
    deck = {
        "name": "Tokyo Metropolitan Government Building Observation Deck",
        "category": "viewpoint",
        "description": "An observation deck with panoramic city views.",
        "rating": 4.5, "review_count": 12000,
        "location": {"lat": 35.6896, "lng": 139.6917},
        "source": "geoapify",
    }
    mall = {
        "name": "Shibuya Shopping Mall",
        "category": "shopping",
        "description": "A large shopping mall.",
        "rating": 4.9, "review_count": 500000,
        "location": {"lat": 35.6580, "lng": 139.7016},
        "source": "geoapify",
    }

    deck_points, _, _ = interest_score(deck, prefs)
    mall_points, _, _ = interest_score(mall, prefs)
    assert deck_points > 0
    assert mall_points == 0, "no interest evidence must mean zero interest points"

    ia = InformationAgent.__new__(InformationAgent)
    deck_score, deck_groups = ia._calculate_score(dict(deck), prefs)
    mall_score, mall_groups = ia._calculate_score(dict(mall), prefs)
    assert deck_groups and not mall_groups
    assert deck_score > mall_score

    # No stated interests uses the neutral discovery baseline, not a fake match.
    neutral, _, _ = interest_score(deck, {})
    assert neutral == 20.0


# ---------------------------------------------------------------------------
# C - retrieval / model output parsing
# ---------------------------------------------------------------------------

def test_c_fenced_prose_wrapped_json_with_trailing_comma_is_parsed():
    from services.structured_parse import records_from_text

    raw = (
        "Here are the attractions I found:\n"
        "```json\n"
        '{"places": [{"name": "Senso-ji", "category": "temple",'
        ' "rating": 4.6,},]}\n'
        "```\n"
        "Let me know if you need more."
    )
    records = records_from_text(raw)
    assert len(records) == 1
    assert records[0]["name"] == "Senso-ji"
    assert records[0]["rating"] == 4.6


def test_c_concatenated_values_and_python_literals_are_recovered():
    from services.structured_parse import records_from_text

    concatenated = '{"name": "A"} and then {"name": "B"}'
    assert [r["name"] for r in records_from_text(concatenated)] == ["A", "B"]

    literal = "prefix [{'name': 'C', 'category': 'park'}] suffix"
    records = records_from_text(literal)
    assert records[0]["name"] == "C"

    nested = '{"data": {"places": [{"name": "D"}]}}'
    assert records_from_text(nested)[0]["name"] == "D"


def test_c_extracted_pois_keep_unknown_fields_unknown():
    from agents.recommend_agent import RecommendAgent
    from services.structured_parse import clamp_rating

    response = SimpleNamespace(content=(
        "```json\n"
        '{"pois": ['
        '{"name": "Senso-ji", "category": "temple", "description": "A temple.",'
        ' "latitude": null, "longitude": null, "rating": null,'
        ' "user_ratings_total": null, "price_level": null},'
        '{"name": "senso-ji", "category": "temple"},'
        '{"name": "N/A", "category": "temple"}'
        "]}\n"
        "```"
    ))
    model = MagicMock()
    model.invoke.return_value = response

    agent = RecommendAgent.__new__(RecommendAgent)
    agent.model_name = "gemini-flash-lite-latest"

    with patch("agents.recommend_agent.ChatGoogleGenerativeAI", return_value=model):
        pois = agent.extract_pois_from_text(
            "Senso-ji is a famous temple in Tokyo." * 2, "Tokyo", 35.68, 139.65
        )

    assert len(pois) == 1, "case-insensitive duplicates and 'N/A' names must be dropped"
    poi = pois[0]
    assert poi["name"] == "Senso-ji"
    assert poi["location"] is None
    assert poi["rating"] is None
    assert poi["review_count"] is None
    assert clamp_rating("not-a-rating") is None


# ---------------------------------------------------------------------------
# D - strict budget
# ---------------------------------------------------------------------------

def _japan_budget_prefs(budget_amount=40000):
    return {
        "city": "Japan",
        "country": "Japan",
        "is_international": True,
        "people": 2,
        "days": 5,
        "budget": "low",
        "budget_amount": budget_amount,
        "budget_strictness": "strict",
        "destination_scope": {
            "kind": "country", "country": "Japan",
            "is_international": True,
            "cities": [],
        },
    }


def _flight_transit():
    return {
        "intercity_transport": [
            {"mode": "flight", "estimated_cost": 45000,
             "cost_basis": "per_person", "currency": "INR"},
        ],
        "recommended_mode": "flight",
        "local_transport": [],
    }


def test_d_strict_budget_below_minimum_is_declared_not_hidden():
    from agents.budget_agent import BudgetAgent

    prefs = _japan_budget_prefs(40000)
    spots = [
        {"name": "Senso-ji", "category": "temple", "price_level": 0,
         "location": {"lat": 35.7148, "lng": 139.7967}},
        {"name": "Tokyo Skytree", "category": "viewpoint", "price_level": 3,
         "location": {"lat": 35.7101, "lng": 139.8107}},
    ]

    budget = BudgetAgent().estimate_budget(
        spots, prefs, False, None, None, _flight_transit()
    )

    assert budget["budget_limit"] == 40000
    assert budget["budget_type"] == "strict"
    assert budget["budget_infeasible"] is True
    assert budget["within_budget"] is False
    assert budget["min_viable_cost"] > 40000
    assert budget["estimated_total"] > 40000
    assert "minimum" in (budget["budget_warning"] or "").lower()
    assert "shortfall" in (budget["budget_warning"] or "").lower()
    # The traveller's cap is an input, not something the planner may rewrite.
    assert prefs["budget_amount"] == 40000


def test_d_budget_without_any_limit_reports_none_not_an_invented_pass():
    from agents.budget_agent import BudgetAgent

    prefs = {
        "city": "Tokyo", "country": "Japan", "is_international": True,
        "people": 1, "days": 3, "budget": "medium",
    }
    budget = BudgetAgent().estimate_budget([], prefs, False, None, None, None)
    assert budget["budget_limit"] is None
    assert budget["within_budget"] is None


def test_d_explicit_transport_choice_beats_a_cheaper_mode():
    from agents.budget_agent import BudgetAgent

    options = {
        "intercity_transport": [
            {"mode": "flight", "estimated_cost": 50000,
             "cost_basis": "per_person", "currency": "INR"},
            {"mode": "train", "estimated_cost": 8000,
             "cost_basis": "per_person", "currency": "INR"},
        ],
        "recommended_mode": "train",
    }
    cost, mode = BudgetAgent()._calculate_intercity_transport_cost(
        options, "low", 2, 40000, 0.0,
        user_prefs={"transport_preference": "flight"},
    )
    assert mode == "flight"
    assert cost == 200000.0, "per-person fare x 2 travellers x 2 legs"


# ---------------------------------------------------------------------------
# E - provider failure
# ---------------------------------------------------------------------------

def _bare_information_agent():
    from agents.information_agent import InformationAgent

    ia = InformationAgent.__new__(InformationAgent)
    ia.poi_manager = MagicMock()
    ia.geocoder = MagicMock()
    ia.llm = None
    ia.last_discovery_status = {}
    ia.retrieval_agent = MagicMock()
    ia.retrieval_agent.retrieve_context.return_value = (
        "No additional background knowledge found."
    )
    return ia


def test_e_total_provider_and_llm_failure_returns_empty_not_fabricated():
    ia = _bare_information_agent()
    ia.poi_manager.get_attractions.return_value = []
    ia.llm = MagicMock()
    ia.llm.invoke.side_effect = RuntimeError("429 quota exhausted")

    result = ia.get_attractions(
        lat=35.68, lng=139.65,
        user_prefs={"city": "Tokyo", "hobbies": "viewpoints", "duration_days": 3},
        rerank_with_llm=False,
    )

    assert result == []
    assert ia.last_discovery_status["empty"] is True
    assert ia.last_discovery_status["generated_count"] == 0
    names = [str(p.get("name", "")).lower() for p in result]
    assert "city center" not in names
    assert "historical district" not in names


def test_e_partial_provider_success_survives_without_invented_fields():
    ia = _bare_information_agent()
    ia.poi_manager.get_attractions.return_value = [
        {"id": "p1", "name": "Senso-ji", "category": "temple",
         "latitude": 35.7148, "longitude": 139.7967,
         "source": "geoapify", "tags": {"historic": "temple"}},
    ]

    result = ia.get_attractions(
        lat=35.68, lng=139.65,
        user_prefs={"city": "Tokyo", "hobbies": "temples", "duration_days": 3},
        rerank_with_llm=False,
    )

    assert len(result) == 1
    poi = result[0]
    assert poi["name"] == "Senso-ji"
    assert poi["rating"] is None, "a missing rating must not become 4.0"
    assert poi["review_count"] is None
    assert poi["image_url"] is None
    assert poi["location"] == {"lat": 35.7148, "lng": 139.7967}
    assert poi["tags"] == {"historic": "temple"}
    assert ia.last_discovery_status["provider_count"] == 1
    assert ia.last_discovery_status["empty"] is False


def test_e_generated_pois_are_geometry_checked_before_routing():
    ia = _bare_information_agent()

    validated, dropped = ia._validate_generated_pois(
        [{"name": "Numata Park", "latitude": 36.65, "longitude": 139.06}],
        "Tokyo", 35.6762, 139.6503, country_code="jp",
    )
    assert validated == []
    assert dropped[0]["reason"] == "outside_city"

    ia.geocoder.geocode.return_value = SimpleNamespace(
        lat=35.7148, lng=139.7967, country_code="jp",
        provider="nominatim", display_name="Senso-ji, Tokyo, Japan",
    )
    validated, dropped = ia._validate_generated_pois(
        [{"name": "Senso-ji"}], "Tokyo", 35.6762, 139.6503, country_code="jp"
    )
    assert not dropped
    assert validated[0]["coords_verified"] is True
    assert validated[0]["verified"] is False

    ia.geocoder.geocode.return_value = SimpleNamespace(
        lat=48.8566, lng=2.3522, country_code="fr",
        provider="nominatim", display_name="Paris, France",
    )
    validated, dropped = ia._validate_generated_pois(
        [{"name": "Eiffel Tower"}], "Tokyo", 35.6762, 139.6503, country_code="jp"
    )
    assert validated == []
    assert dropped[0]["reason"].startswith("country_mismatch")


# ---------------------------------------------------------------------------
# F - itinerary integrity
# ---------------------------------------------------------------------------

def _selected_spots():
    return [
        {"id": "a1", "name": "Senso-ji", "location": {"lat": 35.7148, "lng": 139.7967}},
        {"id": "a2", "name": "Tokyo Skytree", "location": {"lat": 35.7101, "lng": 139.8107}},
        {"id": "a3", "name": "Fushimi Inari Taisha", "location": {"lat": 34.9671, "lng": 135.7727}},
        {"id": "a4", "name": "Kiyomizu-dera", "location": {"lat": 34.9949, "lng": 135.7850}},
    ]


def test_f_strategy_finalisation_keeps_every_selection_exactly_once():
    from agents.strategy_agent import StrategyAgent

    agent = StrategyAgent.__new__(StrategyAgent)
    selected = _selected_spots()
    raw_plan = {
        "day1": ["Senso-ji", "Senso-ji", "Relaxation", "Hallucinated Palace"],
        "day2": ["Tokyo Skytree", "Senso-ji"],
        "day9": ["Fushimi Inari Taisha"],  # out of range: must be re-inserted
    }

    plan, details = agent._finalize_daily_plan(raw_plan, selected, total_days=2)

    assert set(plan) == {"day1", "day2"}, "every day 1..N must exist"
    names = [name for day in plan.values() for name in day]
    attraction_names = [n for n in names if n in {s["name"] for s in selected}]
    assert sorted(attraction_names) == sorted(s["name"] for s in selected)
    assert len(attraction_names) == len(set(attraction_names))
    assert "Hallucinated Palace" not in names
    assert names.count("Relaxation") == 1, "free-time labels must not repeat daily"
    assert all(d["name"] != "Relaxation" for d in details)
    assert {d["name"] for d in details} == {s["name"] for s in selected}


def _bare_route_agent():
    from agents.route_agent import RouteAgent

    agent = RouteAgent.__new__(RouteAgent)
    agent.api_key = None
    agent.distances_cache = {}
    agent._restaurant_cache = {}
    agent._last_routing = {"source": "none", "optimized": False, "status": "not_attempted"}
    agent.info_agent = None
    return agent


def test_f_route_deduplicates_days_and_reports_dropped_names():
    agent = _bare_route_agent()
    spots = {
        "Senso-ji": {"id": "a1", "name": "Senso-ji", "category": "temple",
                     "estimated_duration": 1, "_location_resolved": True,
                     "location": {"lat": 35.7148, "lng": 139.7967}},
        "Tokyo Skytree": {"id": "a2", "name": "Tokyo Skytree", "category": "viewpoint",
                          "estimated_duration": 1, "_location_resolved": True,
                          "location": {"lat": 35.7101, "lng": 139.8107}},
    }
    daily_plan = {
        "day1": ["Senso-ji", "Tokyo Skytree"],
        "day2": ["Senso-ji", "Tokyo Skytree", "Hallucinated Palace"],
    }
    unscheduled = []

    itinerary = agent.format_daily_plan_to_itinerary(
        daily_plan, spots, "2026-10-25", unscheduled_out=unscheduled
    )

    assert len(itinerary) == 2
    attraction_names = [
        act["name"] for day in itinerary for act in day["activities"]
        if act.get("entry_type") == "attraction"
    ]
    assert sorted(attraction_names) == ["Senso-ji", "Tokyo Skytree"]
    assert len(attraction_names) == len(set(attraction_names))
    reasons = {u["reason"] for u in unscheduled}
    assert "not_found_in_data_providers" in reasons
    # A duplicate is a planner repeat, not a missing place: it is disclosed on
    # the day, but not in the global "selected but unscheduled" list.
    assert all(u["name"] != "Senso-ji" for u in unscheduled)
    day_reasons = {
        u["reason"] for day in itinerary for u in (day.get("unscheduled") or [])
    }
    assert "duplicate_across_days" in day_reasons
    for day in itinerary:
        assert day["routing_source"] is not None
        assert day["routing_status"] is not None


# ---------------------------------------------------------------------------
# G - health / resource cleanup
# ---------------------------------------------------------------------------

def test_g_health_endpoint_is_ready_and_secret_free(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "super-secret-test-key-value")
    import main

    main.app.config.update(TESTING=True)
    client = main.app.test_client()
    response = client.get("/health")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["checks"]["llm_configured"] is True
    assert "workflow_count" in body
    assert "super-secret-test-key-value" not in response.get_data(as_text=True)


def test_g_workflow_store_is_bounded():
    import main

    main.workflows.clear()
    for index in range(6):
        main.workflows[f"session-{index}"] = object()

    evicted = main.prune_workflows(max_items=3)

    assert evicted == 3
    assert list(main.workflows) == ["session-3", "session-4", "session-5"]
    main.workflows.clear()


def test_g_car_rental_socket_is_closed_on_403(monkeypatch):
    from services.car_rental_api import CarRentalService

    CarRentalService._DISABLED_UNTIL.clear()
    closed = []

    class FakeResponse:
        status = 403

        def read(self):
            return b""

    class FakeConnection:
        def __init__(self, host, timeout=None):
            self.host = host

        def request(self, *args, **kwargs):
            return None

        def getresponse(self):
            return FakeResponse()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        "services.car_rental_api.http.client.HTTPSConnection", FakeConnection
    )
    service = CarRentalService("x" * 40)
    result = service.find_available_cars(
        35.0, 139.0, "2026-10-25", "10:00:00",
        35.0, 139.0, "2026-10-27", "10:00:00",
        "INR",
    )

    assert result is None
    assert closed == [True], "the SSL socket must be released on a non-200 response"
    assert CarRentalService.disabled_until() is not None
    CarRentalService._DISABLED_UNTIL.clear()


# ---------------------------------------------------------------------------
# H - mocked end-to-end Japan trip
# ---------------------------------------------------------------------------

_TOKYO_POIS = [
    {"id": "tokyo-1", "name": "Tokyo Skytree", "category": "viewpoint",
     "description": "Observation tower with panoramic city views.",
     "latitude": 35.7101, "longitude": 139.8107, "rating": 4.5,
     "review_count": 50000, "source": "geoapify",
     "tags": {"tourism": "viewpoint"}},
    {"id": "tokyo-2", "name": "Senso-ji", "category": "temple",
     "description": "Historic Buddhist temple in Asakusa.",
     "latitude": 35.7148, "longitude": 139.7967, "rating": 4.6,
     "review_count": 70000, "source": "geoapify",
     "tags": {"historic": "temple"}},
]

_KYOTO_POIS = [
    {"id": "kyoto-1", "name": "Fushimi Inari Taisha", "category": "shrine",
     "description": "Shinto shrine famous for its torii gates.",
     "latitude": 34.9671, "longitude": 135.7727, "rating": 4.7,
     "review_count": 90000, "source": "overpass",
     "tags": {"historic": "shrine"}},
    {"id": "kyoto-2", "name": "Kiyomizu-dera", "category": "temple",
     "description": "Historic temple with a hillside wooden stage.",
     "latitude": 34.9949, "longitude": 135.7850, "rating": 4.6,
     "review_count": 60000, "source": "overpass",
     "tags": {"historic": "temple"}},
]

_OSAKA_POIS = [
    {"id": "osaka-1", "name": "Dotonbori", "category": "food",
     "description": "Riverside street known for food and neon signs.",
     "latitude": 34.6687, "longitude": 135.5013, "rating": 4.5,
     "review_count": 80000, "source": "geoapify",
     "tags": {"tourism": "attraction"}},
    {"id": "osaka-2", "name": "Osaka Castle", "category": "castle",
     "description": "Historic castle surrounded by parkland.",
     "latitude": 34.6873, "longitude": 135.5259, "rating": 4.4,
     "review_count": 45000, "source": "geoapify",
     "tags": {"historic": "castle"}},
]

# The old failure: the country was geocoded near Numata, so a 20 km search
# returned unrelated Gunma POIs instead of Tokyo/Kyoto/Osaka.
_NUMATA_POI = {
    "id": "numata-1", "name": "Numata Park", "category": "park",
    "description": "A local park in Gunma.",
    "latitude": 36.65, "longitude": 139.06, "source": "overpass",
}


def _japan_information_agent():
    from agents.information_agent import InformationAgent

    info = InformationAgent.__new__(InformationAgent)
    info.poi_manager = MagicMock()
    info.geocoder = MagicMock()
    info.llm = None
    info.last_discovery_status = {}
    info.state = {}
    info.resolved_destination = None
    info.retrieval_agent = MagicMock()
    info.retrieval_agent.retrieve_context.return_value = (
        "No additional background knowledge found."
    )

    def _provider(lat, lng, radius, hobbies, poi_type, city=None):
        # Only Tokyo, Kyoto and Osaka have live data; the other proposed cities
        # fail (provider outage) and must simply contribute nothing.
        if abs(lat - 35.6762) < 0.1:
            return [dict(p) for p in _TOKYO_POIS + [_NUMATA_POI]]
        if abs(lat - 35.0116) < 0.1:
            return [dict(p) for p in _KYOTO_POIS]
        if abs(lat - 34.6937) < 0.1:
            return [dict(p) for p in _OSAKA_POIS]
        return []

    info.poi_manager.get_attractions.side_effect = _provider
    info.poi_manager.get_restaurants.return_value = []
    info.poi_manager.get_provider_status.return_value = {
        "geoapify": {"state": "ok", "detail": "live"},
        "overpass": {"state": "error", "detail": "timeout"},
    }
    info.get_accommodations = MagicMock(return_value=[])
    info.get_weather = MagicMock(return_value={"summary": "Mild autumn weather."})
    return info


def _japan_workflow():
    from agents.budget_agent import BudgetAgent
    from workflows.travel_graph import TravelGraph

    wf = TravelGraph.__new__(TravelGraph)
    wf.session_states = {}
    wf.state = {
        "user_info": {
            "name": "Asha",
            "origin_city": "Hyderabad",
            "city": "Japan",
            "city_validated": True,
            "is_international": True,
            "days": 5,
            "people": 2,
            "kids": 0,
            "budget": "low",
            "budget_amount": 40000,
            "budget_strictness": "strict",
            "start_date": "2026-11-01",
            "hobbies": "viewpoints and photography",
        },
        "attractions": [],
        "selected_attractions": [],
        "planned_attractions": [],
        "unscheduled_attractions": [],
        "accommodations": [],
        "restaurants": [],
        "weather_summary": None,
        "weather_forecast": None,
        "information_processed": False,
        "should_rent_car": False,
        "rental_decision_source": "default",
        "selected_accommodation": None,
        "transit_options": None,
        "daily_plan": {},
        "itinerary": [],
        "budget": {},
        "budget_normalized": {},
        "provider_status": None,
        "discovery_status": None,
        "email_sent": True,  # never touch SMTP
        "ai_recommendation_generated": True,
        "current_step": "information",
    }

    wf.info_agent = _japan_information_agent()
    wf.recommend_agent = MagicMock()
    wf.recommend_agent.generate_map_data.side_effect = (
        lambda attractions: [
            {"id": a.get("id"), "name": a.get("name"),
             "lat": (a.get("location") or {}).get("lat"),
             "lng": (a.get("location") or {}).get("lng")}
            for a in attractions if a.get("location")
        ]
    )
    wf.budget_agent = BudgetAgent()
    wf.transit_agent = MagicMock()
    wf.transit_agent.get_transit_options.return_value = _flight_transit()
    wf.comm_agent = MagicMock()
    wf.comm_agent.generate_travel_tips_and_summary.return_value = "tips"
    wf.comm_agent.generate_booking_confirmation.return_value = "confirmed"
    wf.strategy_agent = MagicMock()
    wf.chat_agent = MagicMock()
    wf.retrieval_agent = MagicMock()
    return wf


def test_h_mocked_japan_trip_end_to_end():
    wf = _japan_workflow()

    # 1. Discovery: a country request becomes a disclosed multi-city scope.
    info_result = wf._process_information()
    assert info_result["next_step"] == "retrieval"
    scope = info_result["destination_scope"]
    assert scope is not None and scope["kind"] == "country"
    city_names = [c["name"] for c in scope["cities"]]
    assert {"Tokyo", "Kyoto", "Osaka"}.issubset(set(city_names))

    attractions = info_result["attractions"]
    shown_cities = {a.get("city") for a in attractions}
    assert {"Tokyo", "Kyoto", "Osaka"}.issubset(shown_cities)
    assert {a["name"] for a in attractions} == {
        "Tokyo Skytree", "Senso-ji", "Fushimi Inari Taisha",
        "Kiyomizu-dera", "Dotonbori", "Osaka Castle",
    }
    assert "Numata Park" not in {a["name"] for a in attractions}, (
        "the old Japan-centroid POI must not survive multi-city scope validation"
    )
    # No invented rating/image where the provider gave none.
    for attraction in attractions:
        assert attraction.get("rating") is not None
        assert attraction.get("image_url") is None

    # Partial provider success is exposed, not hidden.
    assert info_result["provider_status"]["overpass"]["state"] == "error"
    assert info_result["provider_status"]["geoapify"]["state"] == "ok"
    message = "".join(
        getattr(chunk, "content", "") for chunk in info_result["stream"]
    )
    assert "editable" in message.lower() or "whole country" in message.lower()

    # 2. Strict Rs 40,000 budget: the itinerary is still produced, but the
    #    shortfall is declared with the realistic minimum.
    wf.state["selected_attractions"] = [dict(a) for a in attractions]
    budget_result = wf._process_communication(user_input="continue")
    budget = wf.state["budget"]

    assert budget_result["next_step"] == "communication"
    assert budget["budget_limit"] == 40000
    assert budget["budget_type"] == "strict"
    assert budget["budget_infeasible"] is True
    assert budget["within_budget"] is False
    assert budget["min_viable_cost"] > 40000
    assert "minimum" in budget["budget_warning"].lower()
    assert wf.state["user_info"]["budget_amount"] == 40000
    assert budget_result["stream"] is not None

    # 3. Itinerary integrity: a duplicate/hallucinated planner output cannot
    #    duplicate a visit or invent a place, and every selected spot is kept.
    from agents.route_agent import RouteAgent

    route_agent = RouteAgent.__new__(RouteAgent)
    route_agent.api_key = None
    route_agent.distances_cache = {}
    route_agent._restaurant_cache = {}
    route_agent._last_routing = {"source": "none", "optimized": False,
                                 "status": "not_attempted"}
    route_agent.info_agent = None
    wf.route_agent = route_agent

    wf.state["daily_plan"] = {
        "day1": ["Tokyo Skytree", "Senso-ji", "Tokyo Skytree", "Hallucinated Palace"],
        "day2": ["Fushimi Inari Taisha", "Kiyomizu-dera"],
        "day3": ["Dotonbori", "Osaka Castle", "Senso-ji"],
    }
    route_result = wf._process_route()

    assert route_result["next_step"] == "complete"
    itinerary = route_result["itinerary"]
    assert len(itinerary) == 3, "all three days must reach the client"

    scheduled = [
        act["name"] for day in itinerary for act in day["activities"]
        if act.get("entry_type") == "attraction"
    ]
    assert sorted(scheduled) == sorted(
        {"Tokyo Skytree", "Senso-ji", "Fushimi Inari Taisha",
         "Kiyomizu-dera", "Dotonbori", "Osaka Castle"}
    )
    assert len(scheduled) == len(set(scheduled)), "no place may be visited twice"
    assert "Hallucinated Palace" not in scheduled
    assert all(
        item["name"] not in scheduled
        for item in route_result["unscheduled_attractions"]
    ), "a repeat must not be reported as a place missing from the itinerary"
    assert route_result["budget_infeasible"] is True
    assert route_result["budget_warning"]
    assert route_result["transit_options"]["intercity_transport"]
    assert route_result["provider_status"]["overpass"]["state"] == "error"

    # The budget contract exposed to the UI is internally consistent.
    normalized = route_result["budget_normalized"]
    assert normalized["budget_limit"] == 40000
    assert normalized["within_budget"] is False
    assert normalized["budget_infeasible"] is True
    assert normalized["min_viable_cost"] > 40000


def test_h2_final_client_payload_and_email_use_the_same_itinerary(monkeypatch):
    """The last mile: /api/state and /api/email must serve the final plan.

    This extends the mocked Japan trip above through the two contracts the SPA
    actually consumes, so a Python/runtime upgrade cannot silently change the
    itinerary shape, drop map coordinates, or email a different budget.
    """
    import main as main_module

    wf = _japan_workflow()
    info_result = wf._process_information()
    wf.state["selected_attractions"] = [dict(a) for a in info_result["attractions"]]
    wf.state["email_sent"] = False
    wf.state.pop("email_sent_to", None)

    # 1. Map markers are built from the same attraction records.
    map_data = wf.recommend_agent.generate_map_data(wf.state["attractions"])
    assert len(map_data) == 6
    assert all(m.get("lat") is not None and m.get("lng") is not None
               for m in map_data), "every map marker needs real coordinates"

    wf._process_communication(user_input="continue")
    # The strategy agent is mocked in this fixture (no live LLM); supply the
    # planner output it would have produced, including a repeat and a
    # hallucinated name that the route stage must normalise away.
    wf.state["daily_plan"] = {
        "day1": ["Tokyo Skytree", "Senso-ji", "Tokyo Skytree", "Hallucinated Palace"],
        "day2": ["Fushimi Inari Taisha", "Kiyomizu-dera"],
        "day3": ["Dotonbori", "Osaka Castle", "Senso-ji"],
    }

    from agents.route_agent import RouteAgent

    route_agent = RouteAgent.__new__(RouteAgent)
    route_agent.api_key = None
    route_agent.distances_cache = {}
    route_agent._restaurant_cache = {}
    route_agent._last_routing = {"source": "none", "optimized": False,
                                 "status": "not_attempted"}
    route_agent.info_agent = None
    wf.route_agent = route_agent

    route_result = wf._process_route()
    assert route_result["next_step"] == "complete"

    expected = {
        "Tokyo Skytree", "Senso-ji", "Fushimi Inari Taisha",
        "Kiyomizu-dera", "Dotonbori", "Osaka Castle",
    }

    # 2. The client state endpoint returns the final itinerary + budget.
    main_module.workflows["py313-e2e"] = wf
    client = main_module.app.test_client()
    with client.session_transaction() as s:
        s["session_id"] = "py313-e2e"
        s["user_email"] = "asha@example.com"

    api = client.get("/api/state").get_json()
    assert api["status"] == "success"
    trip = api["trip"]
    assert trip["destination"] == "Japan"
    assert trip["destination_scope"]["kind"] == "country"
    assert trip["provider_status"]["overpass"]["state"] == "error"

    days = trip["itinerary"]["days"]
    assert [d["day"] for d in days] == [1, 2, 3]
    scheduled = [
        spot["name"] for d in days for spot in d["activities"]
        if spot.get("entry_type") == "attraction"
    ]
    assert sorted(scheduled) == sorted(expected)
    assert all(spot.get("day") == d["day"]
               for d in days for spot in d["activities"]
               if spot.get("entry_type") == "attraction")

    assert trip["budget"]["budget_limit"] == 40000
    normalized = trip["budget_normalized"]
    assert normalized["budget_limit"] == 40000
    assert normalized["budget_infeasible"] is True
    # The page and the email must quote the same total.
    assert round(float(normalized["total"]), 2) == round(
        float(trip["budget"]["estimated_total"]), 2
    )

    # 3. The email payload is built from server-side state, not the request body.
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "explorex@example.com")
    captured = {}

    def fake_send(email, user_name, city, itinerary, budget, confirmation,
                  notes=None, trip_context=None):
        captured.update({
            "email": email, "city": city, "itinerary": itinerary,
            "budget": budget, "trip_context": trip_context,
        })
        return True

    monkeypatch.setattr(main_module, "send_trip_email", fake_send)
    response = client.post("/api/email", json={
        "email": "asha@example.com",
        # A tampered body must not replace the server's itinerary.
        "itinerary": [{"day": 1, "activities": [{"name": "Injected Place"}]}],
    })
    assert response.status_code == 200
    assert response.get_json()["success"] is True

    assert captured["email"] == "asha@example.com"
    assert captured["city"] == "Japan"
    assert captured["itinerary"] == wf.state["itinerary"]
    assert captured["budget"]["budget_limit"] == 40000
    assert captured["trip_context"]["people"] == 2
    assert captured["trip_context"]["origin"] == "Hyderabad"
    emailed = [
        act["name"] for day in captured["itinerary"]
        for act in day.get("activities", [])
        if act.get("entry_type") == "attraction"
    ]
    assert sorted(emailed) == sorted(expected)
    assert "Injected Place" not in emailed
    assert wf.state["email_sent"] is True

    # Keep the module-level store clean for other tests.
    main_module.workflows.pop("py313-e2e", None)
