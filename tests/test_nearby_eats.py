"""Tests for the attraction modal's "Nearby Eats" feature.

Covers the verified-distance contract (haversine from the selected attraction's
coordinates to each eatery's own coordinates), provider failures/empty results,
the guest-accessible ``/api/nearby/<lat,lng>`` endpoint, and the static frontend
wiring that keeps the existing Wikipedia link intact.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _haversine_m(lat1, lng1, lat2, lng2):
    radius = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _manager():
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)  # avoid live env/provider construction
    pm._cache = {}
    pm.geoapify_key = ""
    pm._provider_health = {}
    pm._last_provider_status = {}
    return pm


def _osm_node(node_id, name, lat, lng, amenity="restaurant", **tags):
    payload = {"name": name, "amenity": amenity, **tags}
    return {
        "type": "node",
        "id": node_id,
        "lat": lat,
        "lon": lng,
        "tags": {k: v for k, v in payload.items() if v not in (None, "")},
    }


# ---------------------------------------------------------------------------
# Service: distances and normalisation
# ---------------------------------------------------------------------------

def test_distance_is_computed_from_both_real_coordinate_pairs():
    """Every displayed distance must match the two coordinate pairs exactly."""
    pm = _manager()
    attraction = (27.1751, 78.0421)  # Taj Mahal
    elements = [
        _osm_node(
            1, "Cafe Close", 27.1760, 78.0430, amenity="cafe",
            cuisine="coffee_shop", **{"addr:street": "Main Road", "addr:city": "Agra"},
        ),
        _osm_node(2, "Restaurant Far", 27.1700, 78.0400, cuisine="north_indian"),
        _osm_node(3, "Ice Cream Mid", 27.1770, 78.0460, amenity="ice_cream"),
    ]
    pm._execute_overpass_query = MagicMock(return_value=elements)

    result = pm.get_nearby_eateries(*attraction, radius=1000)

    assert result["status"] == "ok"
    assert result["distance_type"] == "straight_line"
    assert result["distance_method"] == "haversine"
    assert result["count"] == 3
    assert [r["distance_m"] for r in result["restaurants"]] == sorted(
        r["distance_m"] for r in result["restaurants"]
    )
    for item in result["restaurants"]:
        expected = int(round(_haversine_m(
            attraction[0], attraction[1], item["latitude"], item["longitude"]
        )))
        assert item["distance_m"] == expected
        assert item["distance_type"] == "straight_line"
        assert f"{item['latitude']:.6f}" in item["map_url"]

    cafe = next(r for r in result["restaurants"] if r["name"] == "Cafe Close")
    assert cafe["type"] == "Cafe"
    assert cafe["cuisine"] == "Coffee Shop"
    assert cafe["address"] == "Main Road, Agra"
    assert "openstreetmap.org" in cafe["map_url"]


def test_places_without_coordinates_or_names_are_never_given_a_distance():
    pm = _manager()
    pm._execute_overpass_query = MagicMock(return_value=[
        {"type": "way", "id": 9, "tags": {"name": "No coords", "amenity": "cafe"}},
        {"type": "node", "id": 10, "lat": 27.18, "lon": 78.05, "tags": {"amenity": "cafe"}},
        _osm_node(11, "Real Place", 27.18, 78.05, amenity="fast_food"),
        _osm_node(12, "Real Place", 27.18, 78.05, amenity="fast_food"),
    ])

    result = pm.get_nearby_eateries(27.1751, 78.0421)

    names = [r["name"] for r in result["restaurants"]]
    assert names == ["Real Place"], "unverifiable records and duplicates must be dropped"
    assert result["restaurants"][0]["distance_m"] == int(round(_haversine_m(
        27.1751, 78.0421, 27.18, 78.05
    )))


def test_bakery_and_street_vendor_are_treated_as_eateries():
    pm = _manager()
    pm._execute_overpass_query = MagicMock(return_value=[
        {"type": "node", "id": 1, "lat": 27.176, "lon": 78.043,
         "tags": {"name": "Corner Bakery", "shop": "bakery"}},
        _osm_node(2, "Street Chaat", 27.176, 78.043, amenity="street_vendor"),
    ])

    result = pm.get_nearby_eateries(27.1751, 78.0421)

    types = {r["name"]: r["type"] for r in result["restaurants"]}
    assert types == {"Corner Bakery": "Bakery", "Street Chaat": "Street food"}


def test_empty_and_failed_provider_results_are_reported_without_raising():
    pm = _manager()
    pm._execute_overpass_query = MagicMock(return_value=[])
    empty = pm.get_nearby_eateries(27.1751, 78.0421)
    assert empty["status"] == "empty"
    assert empty["restaurants"] == []
    assert empty["count"] == 0

    pm2 = _manager()
    pm2._execute_overpass_query = MagicMock(side_effect=RuntimeError("mirror down"))
    failed = pm2.get_nearby_eateries(27.1751, 78.0421)
    assert failed["status"] == "unavailable"
    assert failed["restaurants"] == []
    assert failed["count"] == 0


def test_geoapify_fallback_adds_only_coordinate_verified_results():
    pm = _manager()
    pm.geoapify_key = "configured"
    pm._execute_overpass_query = MagicMock(return_value=[])
    pm._fetch_geoapify_nearby = MagicMock(return_value=[{
        "name": "Fallback Cafe",
        "type": "Cafe",
        "category": "Food",
        "latitude": 27.1760,
        "longitude": 78.0430,
        "distance_m": int(round(_haversine_m(27.1751, 78.0421, 27.1760, 78.0430))),
        "distance_type": "straight_line",
        "map_url": "https://www.openstreetmap.org/?mlat=27.176000&mlon=78.043000",
        "source": "geoapify",
    }])

    result = pm.get_nearby_eateries(27.1751, 78.0421)

    assert result["status"] == "ok"
    assert result["source"] == "geoapify"
    assert result["restaurants"][0]["name"] == "Fallback Cafe"


def test_geoapify_accessibility_categories_are_not_reported_as_cuisine():
    """Only the catering branch describes food; "wheelchair" is not a cuisine."""
    pm = _manager()
    pm.geoapify_key = "configured"
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"features": [
        {"properties": {
            "name": "Accessible Cafe",
            "categories": ["catering", "catering.cafe", "wheelchair", "wheelchair.yes"],
            "lat": 27.1760, "lon": 78.0430,
        }},
        {"properties": {
            "name": "Italian Place",
            "categories": [
                "catering", "catering.restaurant",
                "catering.restaurant.italian", "catering.restaurant.pizza",
            ],
            "lat": 27.1770, "lon": 78.0440,
        }},
    ]}

    with patch("services.poi_fallback.requests.get", return_value=response):
        results = pm._fetch_geoapify_nearby(27.1751, 78.0421, 1000, 10)

    by_name = {r["name"]: r for r in results}
    assert by_name["Accessible Cafe"]["type"] == "Cafe"
    assert "cuisine" not in by_name["Accessible Cafe"]
    assert by_name["Italian Place"]["type"] == "Restaurant"
    assert by_name["Italian Place"]["cuisine"] == "Italian"
    for item in results:
        assert item["distance_m"] == int(round(_haversine_m(
            27.1751, 78.0421, item["latitude"], item["longitude"]
        )))


def test_invalid_coordinates_are_rejected_before_any_provider_call():
    pm = _manager()
    pm._execute_overpass_query = MagicMock()

    for lat, lng in ((None, None), ("x", 2), (91, 0), (0, 181)):
        result = pm.get_nearby_eateries(lat, lng)
        assert result["status"] == "invalid"
        assert result["restaurants"] == []

    pm._execute_overpass_query.assert_not_called()


def test_successful_results_are_cached_to_avoid_repeat_provider_calls():
    pm = _manager()
    pm._execute_overpass_query = MagicMock(return_value=[
        _osm_node(1, "Cached Cafe", 27.176, 78.043, amenity="cafe"),
    ])

    first = pm.get_nearby_eateries(27.1751, 78.0421)
    second = pm.get_nearby_eateries(27.1751, 78.0421)

    assert first["restaurants"] == second["restaurants"]
    assert pm._execute_overpass_query.call_count == 1


def test_adjacent_attractions_do_not_share_cached_distances():
    """A 4-decimal cache key would reuse distances for places ~10 m apart."""
    pm = _manager()
    pm._execute_overpass_query = MagicMock(return_value=[
        _osm_node(1, "Cafe", 27.176, 78.043, amenity="cafe"),
    ])

    first = pm.get_nearby_eateries(27.1751, 78.0421)
    second = pm.get_nearby_eateries(27.1752, 78.0421)

    assert pm._execute_overpass_query.call_count == 2
    assert first["restaurants"][0]["distance_m"] != second["restaurants"][0]["distance_m"]
    for result, attraction in ((first, (27.1751, 78.0421)), (second, (27.1752, 78.0421))):
        item = result["restaurants"][0]
        assert item["distance_m"] == int(round(_haversine_m(
            attraction[0], attraction[1], item["latitude"], item["longitude"]
        )))


# ---------------------------------------------------------------------------
# API: guest access and the selected attraction's coordinates
# ---------------------------------------------------------------------------

def test_nearby_endpoint_works_without_a_session_and_passes_selected_coordinates():
    import main

    fake_manager = MagicMock()
    fake_manager.get_nearby_eateries.return_value = {
        "status": "ok",
        "restaurants": [{
            "name": "Verified Cafe",
            "distance_m": 134,
            "distance_type": "straight_line",
            "latitude": 27.176,
            "longitude": 78.043,
        }],
        "count": 1,
        "radius_m": 1000,
        "distance_type": "straight_line",
        "source": "osm",
        "attraction": {"latitude": 27.1751, "longitude": 78.0421},
    }
    client = main.app.test_client()

    with patch("services.poi_fallback.get_shared_poi_manager", return_value=fake_manager):
        resp = client.get("/api/nearby/27.1751,78.0421?radius=1000")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["restaurants"][0]["name"] == "Verified Cafe"
    assert payload["distance_type"] == "straight_line"
    fake_manager.get_nearby_eateries.assert_called_once_with(27.1751, 78.0421, radius=1000)


def test_nearby_endpoint_rejects_malformed_or_impossible_coordinates():
    import main

    client = main.app.test_client()
    for path in ("/api/nearby/not-coords", "/api/nearby/91,0", "/api/nearby/0,181"):
        resp = client.get(path)
        assert resp.status_code == 400
        assert "error" in resp.get_json()


def test_nearby_endpoint_prefers_the_active_session_manager():
    import main

    fake_manager = MagicMock()
    fake_manager.get_nearby_eateries.return_value = {
        "status": "ok", "restaurants": [], "count": 0, "radius_m": 1500,
        "distance_type": "straight_line", "source": "osm",
        "attraction": {"latitude": 18.9219, "longitude": 72.8347},
    }
    workflow = MagicMock()
    workflow.info_agent.poi_manager = fake_manager
    client = main.app.test_client()

    with patch.dict(main.workflows, {"nearby-session": workflow}, clear=False):
        with client.session_transaction() as sess:
            sess["session_id"] = "nearby-session"
        resp = client.get("/api/nearby/18.9219,72.8347?radius=1500")

    assert resp.status_code == 200
    fake_manager.get_nearby_eateries.assert_called_once_with(18.9219, 72.8347, radius=1500)


def test_nearby_endpoint_keeps_the_standard_error_contract_on_unexpected_failure():
    import main

    fake_manager = MagicMock()
    fake_manager.get_nearby_eateries.side_effect = RuntimeError("boom")
    client = main.app.test_client()

    with patch("services.poi_fallback.get_shared_poi_manager", return_value=fake_manager):
        resp = client.get("/api/nearby/27.1751,78.0421")

    assert resp.status_code == 500
    payload = resp.get_json()
    assert payload["success"] is False
    assert payload["error"] == "Nearby places could not be loaded for this stop right now."


# ---------------------------------------------------------------------------
# Frontend contract (no browser automation in this environment)
# ---------------------------------------------------------------------------

def test_modal_keeps_wikipedia_and_adds_nearby_eats_beside_it():
    html = (ROOT / "frontend" / "templates" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert 'id="modal-links"' in html
    assert 'id="modal-nearby"' in html
    assert 'data-testid="nearby-eats-panel"' in html

    # Wikipedia rendering is unchanged; the new button is appended to the same
    # links container afterwards.
    assert "Wikipedia article" in js
    assert "wiki_url" in js
    assert "linksWrap.innerHTML = linkBits.join(' ')" in js
    assert "_appendNearbyEatsButton(linksWrap, a, lat, lng)" in js
    assert "Nearby Eats" in js

    # Distances must be labelled straight-line and recomputed from coordinates.
    assert "_haversineMeters" in js
    assert "not walking or road distance" in js
    assert "/api/nearby/" in js
    assert "nearby-eats-loading" in js
    assert "nearby-eats-results" in js
    assert "no usable coordinates" in js

    # A late response for a previously selected attraction must be discarded.
    assert "nearbyEatsToken" in js
    assert "AbortController" in js
