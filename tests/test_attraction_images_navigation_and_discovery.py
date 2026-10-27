"""Regression tests for the October repair ticket.

Covers the six reported problem areas without network access:

* verified attraction images (normalisation, title/coordinate checks, caching)
* provider payload validation and the numeric-name ``.lower()`` crash
* partial provider success and honest status reporting
* discovery breadth, interest labels and plural interest aliases
* supported ``google-genai`` SDK calls and streaming
* navigation decoupling (backend progress must not switch tabs)
* strict ₹100,000 / 10-adult budget consistency and session isolation
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def test_image_url_normalisation_rejects_unsafe_values():
    from services.attraction_images import normalize_image_url

    assert normalize_image_url(" https://upload.wikimedia.org/a.jpg ") == (
        "https://upload.wikimedia.org/a.jpg"
    )
    assert normalize_image_url("//upload.wikimedia.org/a.jpg") == (
        "https://upload.wikimedia.org/a.jpg"
    )
    assert normalize_image_url("http://upload.wikimedia.org/a.jpg") == (
        "https://upload.wikimedia.org/a.jpg"
    )
    for value in (
        None, "", 123, True, "data:image/svg+xml;base64,AAAA",
        "javascript:alert(1)", "/static/local.jpg", "https://",
        "https://example.com/pixel.gif",
    ):
        assert normalize_image_url(value) is None


def _wiki_response(*pages):
    response = MagicMock(status_code=200)
    response.headers = {}
    response.json.return_value = {"query": {"pages": list(pages)}}
    return response


def _json_response(payload, status_code=200):
    response = MagicMock(status_code=status_code)
    response.headers = {}
    response.json.return_value = payload
    return response


def test_resolver_keeps_provider_image_without_a_lookup(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver._session.get = MagicMock()

    poi = {"name": "Fort Kochi", "image_url": "http://cdn.example/fort.jpg"}
    assert resolver.resolve(poi) == "https://cdn.example/fort.jpg"
    resolver._session.get.assert_not_called()


def test_resolver_uses_only_a_verified_wikipedia_lead_image(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    monkeypatch.setenv("ATTRACTION_IMAGE_CACHE_TTL", "60")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver._session.get = MagicMock(return_value=_wiki_response({
        "pageid": 1,
        "title": "Kovalam",
        "thumbnail": {"source": "//upload.wikimedia.org/kovalam.jpg"},
        "coordinates": [{"lat": 8.3988, "lon": 76.9782}],
    }))

    url = resolver.resolve(
        {"name": "Kovalam", "latitude": 8.3988, "longitude": 76.9782},
        city="Kerala",
    )
    assert url == "https://upload.wikimedia.org/kovalam.jpg"

    # The positive result is cached; a second call must not hit the network.
    resolver._session.get.reset_mock()
    assert resolver.resolve(
        {"name": "Kovalam", "latitude": 8.3988, "longitude": 76.9782},
        city="Kerala",
    ) == url
    resolver._session.get.assert_not_called()


def test_resolver_rejects_unrelated_titles_and_far_coordinates(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver._session.get = MagicMock(return_value=_wiki_response({
        "pageid": 2,
        "title": "Sunset Boulevard",
        "thumbnail": {"source": "https://upload.wikimedia.org/sunset.jpg"},
        "coordinates": [{"lat": 34.09, "lon": -118.33}],
    }))
    assert resolver.resolve({"name": "Sunset Deck"}, city="Kochi") is None

    resolver._session.get = MagicMock(return_value=_wiki_response({
        "pageid": 3,
        "title": "Tea Museum",
        "thumbnail": {"source": "https://upload.wikimedia.org/tea.jpg"},
        "coordinates": [{"lat": 27.0, "lon": 88.0}],
    }))
    # Same title but ~2,000 km from the requested point: not this place.
    assert resolver.resolve(
        {"name": "Tea Museum", "latitude": 9.97, "longitude": 76.28}
    ) is None


def test_resolver_negative_result_is_cached(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    monkeypatch.setenv("ATTRACTION_IMAGE_NEGATIVE_TTL", "60")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver._session.get = MagicMock(return_value=_wiki_response())

    assert resolver.resolve({"name": "Unknown Place"}, city="Kochi") is None
    resolver._session.get.reset_mock()
    assert resolver.resolve({"name": "Unknown Place"}, city="Kochi") is None
    resolver._session.get.assert_not_called()


def test_resolver_stops_lookups_after_a_rate_limit(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    monkeypatch.setenv("ATTRACTION_IMAGE_RATE_LIMIT_COOLDOWN", "60")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    limited = MagicMock(status_code=429)
    limited.headers = {"Retry-After": "0"}
    resolver._session.get = MagicMock(return_value=limited)

    assert resolver.resolve({"name": "First Place"}, city="Kochi") is None
    assert resolver.last_status.get("state") == "rate_limited"

    resolver._session.get.reset_mock()
    assert resolver.resolve({"name": "Second Place"}, city="Kochi") is None
    resolver._session.get.assert_not_called()


def test_commons_file_confidence_rejects_generic_city_and_distant_photos():
    from services.attraction_images import _commons_file_confidence

    assert _commons_file_confidence(
        "Changampuzha Park", "File:Changampuzha park art 04.jpg", 0.02
    )
    assert _commons_file_confidence(
        "Chengalathukavu", "File:Chengalathukavu Devi Temple 02.jpg", 0.011
    )
    # "tree" is generic and "Munnar" is only the city, not this school.
    assert not _commons_file_confidence(
        "Honey Tree", "File:A Bee tree in Mattupetty - panoramio.jpg", 0.02
    )
    assert not _commons_file_confidence(
        "LFGHS Munnar", "File:Munnar 4 (95666411).jpeg", 0.02, city="Munnar"
    )
    # A matching name 500 m away is not evidence of *this* place.
    assert not _commons_file_confidence(
        "Changampuzha Park", "File:Changampuzha park art 04.jpg", 0.5
    )


def test_commons_geosearch_resolves_a_geotagged_named_photo(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    calls = []

    def _fake_get(url, params=None, **kwargs):  # noqa: ARG001
        params = params or {}
        calls.append(dict(params))
        if params.get("ggsnamespace") == 6:
            return _json_response({"query": {"pages": [{
                "title": "File:Chengalathukavu Devi Temple 02.jpg",
                "coordinates": [{"lat": 9.3167, "lon": 76.4333}],
            }]}})
        if params.get("prop") == "imageinfo":
            return _json_response({"query": {"pages": [{
                "title": "File:Chengalathukavu Devi Temple 02.jpg",
                "imageinfo": [{
                    "thumburl": "https://upload.wikimedia.org/chengalathukavu.jpg"
                }],
            }]}})
        raise AssertionError(f"unexpected request: {url} {params}")

    resolver._session.get = MagicMock(side_effect=_fake_get)
    poi = {"name": "Chengalathukavu", "latitude": 9.3167, "longitude": 76.4333}

    resolver._resolve_commons_geosearch([poi])

    assert poi["image_url"] == "https://upload.wikimedia.org/chengalathukavu.jpg"
    assert poi["image_source"] == "commons_geosearch"
    assert any(call.get("ggsnamespace") == 6 for call in calls)
    assert any(call.get("prop") == "imageinfo" for call in calls)
    # One GeoSearch request per cluster, not one per attraction.
    assert sum(1 for call in calls if call.get("ggsnamespace") == 6) == 1


def test_commons_geosearch_failure_is_not_cached_as_a_negative(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    limited = MagicMock(status_code=429)
    limited.headers = {"Retry-After": "0"}
    resolver._session.get = MagicMock(return_value=limited)

    poi = {"name": "Chengalathukavu", "latitude": 9.3167, "longitude": 76.4333}
    resolver._resolve_commons_geosearch([poi])
    first_calls = resolver._session.get.call_count
    resolver._resolve_commons_geosearch([poi])

    assert resolver._session.get.call_count > first_calls, (
        "A rate-limited GeoSearch must be retried, not cached as 'no photo'"
    )
    assert resolver._commons_geo_cache == {}


def test_disabled_image_lookup_preserves_provider_images_without_network(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "0")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver._session.get = MagicMock()
    items = [
        {"name": "Fort Kochi", "image_url": "https://cdn.example/fort.jpg"},
        {"name": "Unknown Temple", "latitude": 9.9, "longitude": 76.2},
        {
            "name": "Marine Drive",
            "metadata": {"osm_tags": {"image": "https://upload.wikimedia.org/marine.jpg"}},
        },
    ]

    result = resolver.enrich(items, city="Kochi")

    assert result[0]["image_url"] == "https://cdn.example/fort.jpg"
    assert "image_url" not in result[1]
    assert result[2]["image_url"] == "https://upload.wikimedia.org/marine.jpg"
    assert resolver.last_status["state"] == "disabled"
    assert resolver.last_status["resolved"] == 2
    resolver._session.get.assert_not_called()


def test_generic_names_cannot_borrow_a_nearby_article_or_photo():
    """A POI literally called "Temple"/"Church" cannot be verified by name."""
    from services.attraction_images import _title_matches

    assert _title_matches("Temple", "Munnar Sree Temple") is False
    assert _title_matches("CSI Church", "St Thomas CSI Church Munnar") is False
    assert _title_matches("Church", "Church") is True
    # Distinctive names still match their own article.
    assert _title_matches("Changampuzha Park", "Changampuzha Park") is True
    assert _title_matches("Chengalathukavu", "Chengalathukavu Devi Temple") is True
    # A short article title is fine when the extra word is generic only.
    assert _title_matches("Changampuzha Park", "Changampuzha") is True
    # A town article must not stand in for a place that merely names the town.
    assert _title_matches("LFGHS Munnar", "Munnar", city="Munnar") is False
    assert _title_matches("Fort Kochi Beach", "Kochi", city="Kochi") is False
    assert _title_matches("Munnar", "Munnar", city="Munnar") is True
    assert _title_matches("Munnar", "Munnar (town)", city="Munnar") is True
    # Two shared words, one of them distinctive, are enough.
    assert _title_matches(
        "Sree Malsyavathara Vishnu Temple",
        "Meenangadi Matsyavatara Maha Vishnu Temple",
        city="Wayanad",
    ) is True
    # Sharing only a type word is never enough.
    assert _title_matches("St Mary's Church", "Holy Cross Church") is False


def test_first_verified_image_wins_and_is_never_overwritten():
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    poi = {"name": "Chengalathukavu"}

    resolver._set_image(poi, "https://upload.wikimedia.org/exact.jpg", "commons")
    resolver._set_image(poi, "https://upload.wikimedia.org/guess.jpg", "geosearch")

    assert poi["image_url"] == "https://upload.wikimedia.org/exact.jpg"
    assert poi["image_source"] == "commons"
    assert resolver._source_counts == {"commons": 1}


def test_commons_categories_skip_places_already_resolved_by_a_file():
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver._session.get = MagicMock()
    resolved = {
        "name": "Chengalathukavu",
        "metadata": {"osm_tags": {"wikimedia_commons": "File:Chengalathukavu Devi Temple.jpg"}},
    }
    pending = {
        "name": "Pathaparambu",
        "metadata": {"osm_tags": {"wikimedia_commons": "Category:Pathaparambu"}},
    }
    files_calls = []
    category_calls = []
    resolver._resolve_commons_files = lambda files: (
        files_calls.append(files),
        resolver._set_image(resolved, "https://upload.wikimedia.org/chengalathukavu.jpg",
                            "commons"),
    )
    resolver._resolve_commons_categories = lambda cats: category_calls.append(cats)

    resolver._resolve_commons_values([resolved, pending])

    assert files_calls and len(files_calls[0]) == 1
    assert category_calls and list(category_calls[0]) == ["Category:Pathaparambu"]
    resolver._session.get.assert_not_called()


def test_commons_geosearch_has_a_hard_cluster_cap():
    """The last-resort stage must not issue one request per remote place."""
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver._session.get = MagicMock(
        return_value=_json_response({"query": {"pages": []}})
    )
    pois = [
        {"name": f"Remote Place {index}", "latitude": 8.0 + index, "longitude": 76.0}
        for index in range(30)
    ]

    resolver._resolve_commons_geosearch(pois)

    assert resolver._session.get.call_count == 8


def test_search_stage_respects_the_wall_clock_deadline(monkeypatch):
    """A slow Wikimedia day must not stall the plan behind image lookups."""
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1)
    resolver.deadline_seconds = 0.05
    for stage in (
        "_hydrate_osm_tags", "_resolve_wikipedia_titles", "_resolve_wikidata",
        "_resolve_commons_values", "_resolve_geosearch", "_resolve_commons_geosearch",
    ):
        setattr(resolver, stage, lambda pois: None)

    calls = []

    def _slow_search(name, city, lat=None, lng=None):  # noqa: ARG001
        calls.append(name)
        time.sleep(0.08)
        return None, True

    resolver._search_wikipedia = _slow_search
    items = [
        {"name": f"Cherai Beach {index}", "latitude": 9.9, "longitude": 76.2}
        for index in range(4)
    ]

    resolver.enrich(items, city="Kochi", max_lookups=10)

    assert calls == ["Cherai Beach 0"]
    assert resolver.last_status["stages"]["search"]["state"] == "skipped"
    assert resolver.last_status["stages"]["search"]["detail"] == "deadline"


def test_search_budget_prefers_distinctive_names():
    from services.attraction_images import AttractionImageResolver

    key = AttractionImageResolver._search_sort_key
    distinctive = key({"name": "Cherai Beach"})
    generic = key({"name": "Temple"})
    assert distinctive < generic


# ---------------------------------------------------------------------------
# Provider normalisation / dedup
# ---------------------------------------------------------------------------

def _bare_manager():
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    pm.geoapify_key = "test-key"
    pm.wikipedia_enabled = False
    pm._cache = {}
    pm._query_cache = {}
    pm._query_cache_lock = __import__("threading").Lock()
    pm._provider_health = {}
    pm._last_provider_status = {}
    return pm


def test_dedup_survives_integer_names_and_malformed_records():
    """The exact Kochi failure: an integer name reached ``.lower()``."""
    pm = _bare_manager()
    pois = [
        {"name": 4242, "latitude": 9.9312, "longitude": 76.2673,
         "source": "geoapify", "category": "tourism.attraction"},
        {"name": "Fort Kochi Beach", "latitude": 9.9658, "longitude": 76.2422,
         "source": "osm", "category": "beach", "image_url": None},
        {"name": "Fort Kochi Beach", "latitude": 9.9658, "longitude": 76.2422,
         "source": "wikipedia", "category": "nature",
         "image_url": "https://upload.wikimedia.org/fort-kochi.jpg",
         "metadata": {"wikidata_id": "Q123"}},
        None,
        "not-a-dict",
        {"name": None, "latitude": 9.0, "longitude": 76.0},
        {"name": "Broken", "latitude": "x", "longitude": "y"},
    ]

    result = pm._deduplicate_pois(pois)

    assert len(result) == 1, "valid records survive one malformed name/type"
    beach = result[0]
    assert beach["name"] == "Fort Kochi Beach"
    assert beach["image_url"] == "https://upload.wikimedia.org/fort-kochi.jpg"
    assert "wikipedia" in beach["sources"]


def test_geoapify_rejects_non_list_features_without_dropping_the_city():
    from services import poi_fallback

    pm = _bare_manager()
    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"features": "not-a-list"}

    with patch.object(poi_fallback.requests, "get", return_value=response):
        assert pm._fetch_geoapify_attractions(9.93, 76.26, 20000, None) == []

    response.json.return_value = {"features": [
        None,
        {"properties": {"name": 999, "lat": "9.9", "lon": "76.2",
                        "categories": ["tourism.attraction"]}},
        {"properties": {"name": "Marine Drive", "lat": "9.981", "lon": "76.276",
                        "categories": ["tourism.attraction"]}},
    ]}
    with patch.object(poi_fallback.requests, "get", return_value=response):
        results = pm._fetch_geoapify_attractions(9.93, 76.26, 20000, None)

    assert [p["name"] for p in results] == ["Marine Drive"]


def test_osm_fetch_coerces_numeric_names_and_string_coordinates():
    pm = _bare_manager()
    pm._execute_overpass_query = MagicMock(return_value=[
        None,
        {"tags": None},
        {"tags": {"name": 7, "tourism": "attraction"}, "lat": "9.97", "lon": "76.28"},
        {"tags": {"name": "Marine Drive", "tourism": "attraction"},
         "lat": "9.981", "lon": "76.276"},
    ])

    results = pm._fetch_osm_attractions(9.93, 76.26, 20000, 12345)

    assert [p["name"] for p in results] == ["Marine Drive"]
    assert results[0]["latitude"] == pytest.approx(9.981)
    assert results[0]["longitude"] == pytest.approx(76.276)


def test_get_category_filters_accepts_non_string_hobbies():
    pm = _bare_manager()
    filters = pm._get_category_filters(12345, 20000, 9.93, 76.26)
    assert "tourism" in filters


def test_partial_provider_failure_keeps_geoapify_results_and_reports_overpass():
    pm = _bare_manager()
    good = {
        "id": "geo-1", "name": "Marine Drive", "latitude": 9.981,
        "longitude": 76.276, "source": "geoapify",
        "category": "tourism.attraction",
    }
    pm._fetch_geoapify_attractions = MagicMock(return_value=[good])
    pm._fetch_osm_attractions = MagicMock(side_effect=RuntimeError("all mirrors down"))

    results = pm.get_attractions(9.93, 76.26, radius=20000, city="Kochi")

    assert [p["name"] for p in results] == ["Marine Drive"]
    status = pm.get_provider_status()
    assert status["geoapify"]["state"] == "ok"
    assert status["overpass"]["state"] == "error"


def test_overpass_circuit_breaker_stops_a_dead_mirror_cascade():
    """A down Overpass must fail fast for later steps, not repeat timeouts."""
    from services import poi_fallback

    pm = _bare_manager()
    pm.overpass_endpoints = ["https://one.invalid/api", "https://two.invalid/api"]
    pm.overpass_cooldowns = {}
    pm._overpass_breaker_until = 0.0
    pm._overpass_consecutive_failures = 0

    calls = []

    def _timeout(*args, **kwargs):  # noqa: ARG001
        calls.append(1)
        raise poi_fallback.requests.exceptions.Timeout("down")

    with patch.object(poi_fallback.requests, "post", side_effect=_timeout), \
         patch.object(poi_fallback.time, "sleep", lambda s: None):
        first = pm._execute_overpass_query("[out:json];out;")
        second = pm._execute_overpass_query("[out:json];out;")
        calls_after_two = len(calls)
        third = pm._execute_overpass_query("[out:json];out;")

    assert first == [] and second == [] and third == []
    assert calls_after_two >= 1
    assert len(calls) == calls_after_two, (
        "The breaker must prevent any further HTTP calls while it is open"
    )
    assert pm._overpass_breaker_until > 0


def test_state_search_attributes_each_poi_to_its_origin_city():
    """`state_city` must stay the city (not the state), so the UI and the
    balanced cap can represent every searched destination."""
    from agents.information_agent import InformationAgent

    ia = InformationAgent.__new__(InformationAgent)
    ia.llm = None
    ia.city2geocode = lambda *args, **kwargs: {"lat": 9.9, "lng": 76.2}
    ia.enrich_attractions = lambda pois, city: pois
    ia.last_discovery_status = {}

    cities = ["Kochi", "Munnar"]
    by_city = {
        "Kochi": [
            {"id": f"kochi-{i}", "name": f"Kochi Place {i}", "latitude": 9.9, "longitude": 76.2,
             "source": "geoapify", "category": "tourism.attraction"}
            for i in range(10)
        ],
        "Munnar": [
            {"id": f"munnar-{i}", "name": f"Munnar Place {i}", "latitude": 10.0, "longitude": 77.0,
             "source": "geoapify", "category": "tourism.attraction"}
            for i in range(10)
        ],
    }
    ia.poi_manager = MagicMock()
    ia.poi_manager.get_attractions.side_effect = lambda lat, lng, radius, hobbies, poi_type, city: [
        dict(p) for p in by_city[city]
    ]

    results = ia.get_attractions_for_state(
        "Kerala", cities, {"hobbies": "beaches", "days": 5}, total_number=12,
    )

    assert {p["state_city"] for p in results} == {"Kochi", "Munnar"}
    assert {p["state"] for p in results} == {"Kerala"}
    assert all(p.get("city") == p["state_city"] for p in results)

    status = ia.last_discovery_status
    assert status["scope"] == "Kerala"
    assert status["cities_attempted"] == 2
    assert status["successful_cities"] == 2
    assert status["failed_cities"] == 0
    assert status["total"] == len(results)
    assert status["cities_with_results"] == {"Kochi": 10, "Munnar": 10}
    assert status["raw"] == 20
    assert status["capped"] == 20


def test_balanced_cap_keeps_every_city_represented():
    from agents.information_agent import InformationAgent

    pois = []
    for city in ("Kochi", "Munnar", "Alleppey", "Wayanad"):
        for i in range(20):
            pois.append({
                "id": f"{city}-{i}", "name": f"{city} Place {i}",
                "state_city": city, "source": "geoapify",
            })

    capped = InformationAgent._balanced_cap(pois, 40)

    assert len(capped) == 40
    counts = {}
    for poi in capped:
        counts[poi["state_city"]] = counts.get(poi["state_city"], 0) + 1
    assert counts == {"Kochi": 10, "Munnar": 10, "Alleppey": 10, "Wayanad": 10}


# ---------------------------------------------------------------------------
# Discovery / ranking / interests
# ---------------------------------------------------------------------------

def test_interest_aliases_understand_plurals_and_hill_stations():
    from services.interest_match import normalize_interests

    groups = normalize_interests("beaches, hill stations, viewpoints")
    assert {"nature", "mountains", "viewpoints"}.issubset(set(groups))


def test_rerank_keeps_all_results_and_labels_discovery():
    from agents.information_agent import InformationAgent

    ia = InformationAgent.__new__(InformationAgent)
    ia.llm = None
    ia.llm_rerank_cache = {}

    prefs = {"hobbies": "beaches, hill stations, viewpoints"}
    spots = [
        {"id": "beach", "name": "Kovalam Beach", "category": "beach",
         "description": "A popular beach with a lighthouse.",
         "latitude": 8.39, "longitude": 76.97, "source": "osm"},
        {"id": "hill", "name": "Munnar Hill Station", "category": "hill_station",
         "description": "Tea gardens and viewpoints in the Western Ghats.",
         "latitude": 10.08, "longitude": 77.05, "source": "osm"},
        {"id": "view", "name": "Kallimali View Point", "category": "viewpoint",
         "description": "A scenic viewpoint at sunset.",
         "latitude": 10.15, "longitude": 77.10, "source": "osm"},
    ]
    spots += [
        {"id": f"notable-{i}", "name": f"Notable Museum {i}", "category": "museum",
         "description": "A museum with a verifiable Wikipedia article.",
         "latitude": 9.5 + i * 0.01, "longitude": 76.3, "source": "wikipedia",
         "wikipedia_url": f"https://en.wikipedia.org/wiki/Museum_{i}"}
        for i in range(60)
    ]

    ranked = ia._rerank_attractions_with_llm(spots, prefs)

    assert len(ranked) == 63, "the old [:50] cap silently dropped verified places"
    labels = [a["recommendation_type"] for a in ranked]
    assert "interest_based" in labels
    assert "discovery" in labels
    assert labels.count("interest_based") == 3


def test_workflow_discovery_target_scales_with_duration_and_has_no_top_50_cut():
    source = (ROOT / "workflows" / "travel_graph.py").read_text(encoding="utf-8")
    info_source = (ROOT / "agents" / "information_agent.py").read_text(encoding="utf-8")
    recommend_source = (ROOT / "agents" / "recommend_agent.py").read_text(encoding="utf-8")

    assert "discovery_target = max(60, min(180, _days_int * 12))" in source
    assert "final_list[:50]" not in info_source
    assert "final_attractions[:50]" not in recommend_source


def test_explore_cards_with_a_verified_photo_render_first():
    source = (ROOT / "frontend" / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "function imageFirstAttractions(list)" in source
    assert "const ordered = imageFirstAttractions(attrs);" in source
    assert "const visible = ordered.slice(0, Math.min(visibleCount, ordered.length));" in source
    # Every card gets a visual: verified photos first, then a generated travel
    # card carrying the attraction's own name (never an empty grey box).
    assert "function travelCardImage(name, place)" in source
    assert "return travelCardImage(" in source
    assert "No verified photo available" not in source


# ---------------------------------------------------------------------------
# Supported Google GenAI SDK
# ---------------------------------------------------------------------------

def test_ask_openai_uses_supported_google_genai_client(monkeypatch):
    import utils

    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text="OK")
    monkeypatch.setattr(utils, "_get_genai_client", lambda: client)

    result = utils.ask_openai("hello", model="gemini-flash-lite-latest",
                              temperature=0.2, max_tokens=123)

    assert result == {"answer": "OK"}
    call = client.models.generate_content.call_args
    assert call.kwargs["model"] == "gemini-flash-lite-latest"
    assert call.kwargs["contents"] == "hello"
    assert call.kwargs["config"].temperature == 0.2
    assert call.kwargs["config"].max_output_tokens == 123


def test_no_source_imports_the_retired_generativeai_package():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", ".venv313", "node_modules", "__pycache__"}
               for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^\s*(?:from|import)\s+google\.generativeai\b", text, re.M):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_chat_agent_streams_chunks_from_its_model():
    from agents.chat_agent import ChatAgent
    from langchain_core.messages import AIMessage

    chunks = [AIMessage(content="Hello "), AIMessage(content="there")]

    class FakeModel:
        def stream(self, messages):  # noqa: ARG002
            return iter(chunks)

    agent = ChatAgent.__new__(ChatAgent)
    agent.conversation_history = []
    agent.model = FakeModel()

    stream = agent.interact_with_user("hi")
    assert list(stream) == chunks


# ---------------------------------------------------------------------------
# Navigation contract
# ---------------------------------------------------------------------------

def test_backend_progress_never_switches_frontend_tabs():
    source = (ROOT / "frontend" / "static" / "js" / "main.js").read_text(encoding="utf-8")

    status_block = source[source.index("if (data.type === 'status')"):
                         source.index("if (data.type === 'error')")]
    assert "switchTab(" not in status_block, (
        "SSE progress must not navigate; only explicit user actions may switch tabs"
    )

    complete_block = source[source.index("function handleComplete(data)"):
                           source.index("// === Render Logic ===")]
    assert "switchTab(" not in complete_block, (
        "handleComplete must render data, not force a tab change"
    )

    boot = source[source.index("restoreFromServer().then"):
                  source.index("} else {\n        showLanding();")]
    assert "switchTab('itinerary')" not in boot
    assert "explorex_tab" in source, "the user's chosen tab should be the only restored one"


def test_frontend_renders_lazy_images_with_a_placeholder_fallback():
    source = (ROOT / "frontend" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    assert 'loading="lazy"' in source
    assert "PLACEHOLDER_IMG" in source
    assert "images.unsplash.com" not in source
    assert "attractions-load-more" in source


# ---------------------------------------------------------------------------
# Pexels attraction fallback
# ---------------------------------------------------------------------------

def _pexels_photo(photo_id, alt, slug):
    return {
        "url": f"https://www.pexels.com/photo/{slug}-{photo_id}/",
        "alt": alt,
        "src": {
            "large": f"https://images.pexels.com/photos/{photo_id}/pexels-photo-{photo_id}.jpeg?w=940",
            "medium": f"https://images.pexels.com/photos/{photo_id}/pexels-photo-{photo_id}.jpeg?w=350",
        },
    }


def _pexels_resolver(monkeypatch, key="test-key"):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    monkeypatch.setenv("ATTRACTION_IMAGE_PEXELS", "1")
    from services.attraction_images import AttractionImageResolver

    return AttractionImageResolver(min_interval=0.0, timeout=0.1, pexels_key=key)


def test_pexels_accepts_a_relevant_attraction_photo(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(101, "Beautiful view of the Taj Mahal in Agra, India", "taj-mahal-agra"),
    ]}))
    poi = {"name": "Taj Mahal", "city": "Agra", "state": "Uttar Pradesh", "country": "India"}

    resolver._resolve_pexels([poi], "Agra")

    assert poi["image_url"].startswith("https://images.pexels.com/photos/101/")
    assert poi["image_source"] == "pexels"
    assert resolver._source_counts["pexels"] == 1


def test_pexels_rejects_a_same_named_place_in_another_country(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(
            202,
            "St Mary's Cathedral in Limerick, Ireland, with Gothic architecture",
            "st-marys-cathedral-limerick-ireland",
        ),
    ]}))
    poi = {"name": "St Marys Church", "city": "Kochi", "state": "Kerala", "country": "India"}

    resolver._resolve_pexels([poi], "Kochi")

    assert not poi.get("image_url"), "a church in Ireland must not illustrate a Kochi church"


def test_pexels_rejects_a_generic_photo_with_no_attraction_name(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(303, "A colorful temple pavilion beside a reflecting pool", "temple-pavilion"),
    ]}))
    poi = {"name": "Meenakshi Temple", "city": "Madurai", "state": "Tamil Nadu", "country": "India"}

    resolver._resolve_pexels([poi], "Madurai")

    assert not poi.get("image_url")


def test_pexels_without_a_key_is_disabled_without_network(monkeypatch):
    resolver = _pexels_resolver(monkeypatch, key="")
    resolver._session.get = MagicMock()
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    resolver._resolve_pexels([poi], "Agra")

    resolver._session.get.assert_not_called()
    assert resolver._stage_status["pexels"]["state"] == "disabled"


def test_pexels_auth_failure_disables_the_provider(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({}, status_code=401))
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    resolver._resolve_pexels([poi], "Agra")
    assert resolver._pexels_disabled_reason == "http_401"

    resolver._session.get.reset_mock()
    resolver._resolve_pexels([poi], "Agra")
    resolver._session.get.assert_not_called()


def test_pexels_rate_limit_is_not_cached_as_a_permanent_miss(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    rate_limited = _json_response({}, status_code=429)
    success = _json_response({"photos": [
        _pexels_photo(404, "The Taj Mahal in Agra, India", "taj-mahal-agra"),
    ]})
    resolver._session.get = MagicMock(side_effect=[rate_limited, success])
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    resolver._resolve_pexels([poi], "Agra")
    assert not poi.get("image_url")
    assert resolver._stage_status["pexels"]["state"] == "rate_limited"

    # The 429 was not cached as "no image": a later request can succeed.
    resolver._pexels_cooldown_until = 0.0
    resolver._resolve_pexels([poi], "Agra")
    assert poi["image_url"].startswith("https://images.pexels.com/photos/404/")


def test_pexels_invalid_json_is_reported_without_an_image(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    response = MagicMock(status_code=200)
    response.headers = {}
    response.json.side_effect = ValueError("not json")
    resolver._session.get = MagicMock(return_value=response)
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    resolver._resolve_pexels([poi], "Agra")

    assert not poi.get("image_url")
    assert resolver._stage_status["pexels"]["state"] == "error"


def test_pexels_empty_result_leaves_the_placeholder_and_is_cached(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": []}))
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    resolver._resolve_pexels([poi], "Agra")
    assert not poi.get("image_url")
    assert resolver._stage_status["pexels"]["state"] == "empty"

    resolver._session.get.reset_mock()
    resolver._resolve_pexels([poi], "Agra")
    resolver._session.get.assert_not_called()


def test_pexels_timeout_is_not_cached_as_a_negative(monkeypatch):
    import requests

    resolver = _pexels_resolver(monkeypatch)
    timeout = requests.exceptions.Timeout("slow")
    success = _json_response({"photos": [
        _pexels_photo(808, "The Taj Mahal in Agra, India", "taj-mahal-agra"),
    ]})
    resolver._session.get = MagicMock(side_effect=[timeout, success])
    poi = {"name": "Taj Mahal"}

    resolver._resolve_pexels([poi], "")
    assert not poi.get("image_url")
    assert resolver._stage_status["pexels"]["state"] == "error"

    resolver._resolve_pexels([poi], "")
    assert poi["image_url"].startswith("https://images.pexels.com/photos/808/")


def test_pexels_rejects_an_invalid_image_url(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    photo = _pexels_photo(909, "The Taj Mahal in Agra, India", "taj-mahal-agra")
    photo["src"] = {"large": "data:image/svg+xml;base64,AAAA", "medium": "/local.jpg"}
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [photo]}))
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    resolver._resolve_pexels([poi], "Agra")

    assert not poi.get("image_url")


def test_pexels_success_is_cached_for_repeated_requests(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(505, "The Taj Mahal in Agra, India", "taj-mahal-agra"),
    ]}))
    first = {"name": "Taj Mahal", "city": "Agra", "country": "India"}
    resolver._resolve_pexels([first], "Agra")
    assert first["image_url"]

    resolver._session.get.reset_mock()
    second = {"name": "Taj Mahal", "city": "Agra", "country": "India"}
    resolver._resolve_pexels([second], "Agra")
    assert second["image_url"] == first["image_url"]
    resolver._session.get.assert_not_called()


def test_pexels_does_not_reuse_one_photo_for_two_attractions(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(606, "The Taj Mahal in Agra, India", "taj-mahal-agra"),
    ]}))
    first = {"name": "Taj Mahal", "city": "Agra", "country": "India"}
    second = {"name": "Taj Mahal Agra", "city": "Agra", "country": "India"}

    resolver._resolve_pexels([first, second], "Agra")

    assert first["image_url"]
    assert not second.get("image_url"), "one stock photo must not represent two attractions"


def test_enrich_guarantees_an_image_when_every_lookup_misses(monkeypatch):
    """Every displayed attraction must end with a renderable image."""
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    monkeypatch.setenv("ATTRACTION_IMAGE_PEXELS", "0")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1, pexels_key="")
    for stage in (
        "_hydrate_osm_tags", "_resolve_wikipedia_titles", "_resolve_wikidata",
        "_resolve_commons_values", "_resolve_geosearch", "_resolve_commons_geosearch",
        "_resolve_commons_nearby", "_resolve_commons_text_search",
    ):
        monkeypatch.setattr(resolver, stage, lambda pois, *args, **kwargs: None)
    monkeypatch.setattr(resolver, "_destination_image", lambda city, country: None)
    pois = [
        {"name": "St Marys Church", "city": "Kochi", "country": "India"},
        {"name": "Ambalappuzha Sree Krishna Temple", "city": "Alappuzha",
         "country": "India"},
    ]

    resolver.enrich(pois, city="Kochi", max_lookups=0)

    for poi in pois:
        assert poi["image_url"].startswith("data:image/svg+xml;base64,")
        assert poi["image_source"] == "generated"
        assert poi["image_is_representative"] is True
    assert pois[0]["image_url"] != pois[1]["image_url"]
    assert resolver.last_status["missing"] == 0
    assert resolver.last_status["state"] == "ok"


def test_representative_pexels_uses_a_destination_photo(monkeypatch):
    """A category + city photo is accepted only by the representative stage."""
    resolver = _pexels_resolver(monkeypatch)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(1212, "A white church in Kerala, India", "church-kerala"),
    ]}))
    monkeypatch.setattr(resolver, "_resolve_commons_text_search", lambda *args, **kwargs: None)
    monkeypatch.setattr(resolver, "_resolve_commons_nearby", lambda *args, **kwargs: None)
    monkeypatch.setattr(resolver, "_destination_image", lambda *args, **kwargs: None)
    poi = {"name": "St Marys Church", "city": "Kochi", "country": "India"}

    resolver._resolve_representative([poi], "Kochi")

    assert poi["image_url"].startswith("https://images.pexels.com/photos/1212/")
    assert poi["image_source"] == "pexels_representative"
    assert poi["image_is_representative"] is True
    query = resolver._session.get.call_args.kwargs["params"]["query"]
    assert "church" in query.lower() and "kochi" in query.lower()


def test_generated_travel_card_is_unique_and_escapes_markup(monkeypatch):
    import base64

    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1, pexels_key="")
    first = {"name": "St <script> Church", "city": "Kochi", "country": "India"}
    second = {"name": "St Marys Church", "city": "Kochi", "country": "India"}

    resolver._resolve_representative([first, second], "Kochi", allow_network=False)

    assert first["image_url"].startswith("data:image/svg+xml;base64,")
    assert second["image_url"].startswith("data:image/svg+xml;base64,")
    assert first["image_url"] != second["image_url"]
    svg = base64.b64decode(first["image_url"].split(",", 1)[1]).decode("utf-8")
    assert "&lt;script&gt;" in svg
    assert "<script>" not in svg


def test_representative_fallback_never_overwrites_a_verified_photo(monkeypatch):
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1, pexels_key="")
    poi = {
        "name": "Kovalam Beach",
        "image_url": "https://upload.wikimedia.org/kovalam.jpg",
        "image_source": "geosearch",
    }

    resolver._resolve_representative([poi], "Kerala", allow_network=False)

    assert poi["image_url"] == "https://upload.wikimedia.org/kovalam.jpg"
    assert poi["image_source"] == "geosearch"
    assert "image_is_representative" not in poi


def test_resolve_with_guarantee_always_returns_a_visual(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    monkeypatch.setenv("ATTRACTION_IMAGE_PEXELS", "0")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1, pexels_key="")
    for stage in (
        "_hydrate_osm_tags", "_resolve_wikipedia_titles", "_resolve_wikidata",
        "_resolve_commons_values", "_resolve_geosearch", "_resolve_commons_geosearch",
        "_resolve_commons_nearby", "_resolve_commons_text_search",
    ):
        monkeypatch.setattr(resolver, stage, lambda pois, *args, **kwargs: None)
    monkeypatch.setattr(resolver, "_destination_image", lambda city, country: None)
    resolver._search_wikipedia = MagicMock(return_value=(None, True))

    poi = {"name": "St Marys Church", "city": "Kochi", "country": "India"}
    url = resolver.resolve(poi, city="Kochi", guarantee=True)

    assert url and url.startswith("data:image/svg+xml;base64,")
    assert poi["image_source"] == "generated"
    assert poi["image_is_representative"] is True

    # The low-level default still lets callers ask for "verified only".
    other = {"name": "Unknown Place", "city": "Kochi", "country": "India"}
    assert resolver.resolve(other, city="Kochi") is None


def test_frontend_accepts_generated_cards_and_backend_data_uris():
    source = (ROOT / "frontend" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    assert "function travelCardImage(name, place)" in source
    assert "data:image/svg+xml" in source
    assert "Representative image" in source


def test_commons_text_search_prefers_the_requested_city(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    from services.attraction_images import AttractionImageResolver

    resolver = AttractionImageResolver(min_interval=0.0, timeout=0.1, pexels_key="")
    resolver._session.get = MagicMock(return_value=_json_response({"query": {"pages": [
        {
            "title": "File:Church in another town.jpg",
            "imageinfo": [{"thumburl": "https://upload.wikimedia.org/other.jpg"}],
        },
        {
            "title": "File:Kottayam Cheriapally church.jpg",
            "imageinfo": [{"thumburl": "https://upload.wikimedia.org/kottayam.jpg"}],
        },
    ]}}))
    poi = {"name": "St. Thomas Church", "city": "Kottayam", "country": "India"}

    resolver._resolve_commons_text_search([poi], "Kottayam")

    assert poi["image_url"] == "https://upload.wikimedia.org/kottayam.jpg"
    assert poi["image_source"] == "commons_search"
    assert poi["image_is_representative"] is True


def test_enrich_uses_pexels_after_the_wikimedia_stages(monkeypatch):
    monkeypatch.setenv("ATTRACTION_IMAGE_LOOKUP", "1")
    resolver = _pexels_resolver(monkeypatch)
    for stage in (
        "_hydrate_osm_tags", "_resolve_wikipedia_titles", "_resolve_wikidata",
        "_resolve_commons_values", "_resolve_geosearch", "_resolve_commons_geosearch",
    ):
        monkeypatch.setattr(resolver, stage, lambda pois, *args, **kwargs: None)
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(707, "The Taj Mahal in Agra, India", "taj-mahal-agra"),
    ]}))
    pois = [{"name": "Taj Mahal", "city": "Agra", "state": "Uttar Pradesh", "country": "India"}]

    resolver.enrich(pois, city="Agra", max_lookups=0)

    assert pois[0]["image_url"].startswith("https://images.pexels.com/photos/707/")
    assert resolver.last_status["sources"]["pexels"] == 1


def test_resolve_prefers_a_verified_wikipedia_image_over_pexels(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    for stage in (
        "_hydrate_osm_tags", "_resolve_wikipedia_titles", "_resolve_wikidata",
        "_resolve_commons_values", "_resolve_geosearch", "_resolve_commons_geosearch",
    ):
        monkeypatch.setattr(resolver, stage, lambda pois, *args, **kwargs: None)
    resolver._search_wikipedia = MagicMock(
        return_value=("https://upload.wikimedia.org/taj.jpg", True)
    )
    resolver._session.get = MagicMock()
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    assert resolver.resolve(poi, city="Agra") == "https://upload.wikimedia.org/taj.jpg"
    resolver._session.get.assert_not_called()


def test_resolve_uses_pexels_when_the_wikipedia_search_is_rate_limited(monkeypatch):
    resolver = _pexels_resolver(monkeypatch)
    for stage in (
        "_hydrate_osm_tags", "_resolve_wikipedia_titles", "_resolve_wikidata",
        "_resolve_commons_values", "_resolve_geosearch", "_resolve_commons_geosearch",
    ):
        monkeypatch.setattr(resolver, stage, lambda pois, *args, **kwargs: None)
    resolver._search_cooldown_until = time.time() + 60
    resolver._session.get = MagicMock(return_value=_json_response({"photos": [
        _pexels_photo(111, "The Taj Mahal in Agra, India", "taj-mahal-agra"),
    ]}))
    poi = {"name": "Taj Mahal", "city": "Agra", "country": "India"}

    url = resolver.resolve(poi, city="Agra")

    assert url and url.startswith("https://images.pexels.com/photos/111/")
    assert poi["image_source"] == "pexels"
    assert resolver.last_status["detail"] == "pexels"


def test_explore_interest_card_is_gone_but_shared_state_remains():
    html = (ROOT / "frontend" / "templates" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "What are you interested in?" not in html
    assert 'id="interest-chips"' not in html
    assert 'id="interest-weight"' not in html
    assert 'id="weight-val"' not in html
    assert "Top Destinations For You" in html
    assert 'id="btn-generate-route"' in html

    # The picker DOM is gone; the shared model and its consumers are not.
    assert "applyInterestsToDom" not in js
    assert "readInterestsFromDom" not in js
    assert "state.interests = ['Food', 'Photography']" in js
    assert "getSelectedInterestsString" in js
    assert "if (tabId === 'explore')" in js
    assert "fetchDestinations();" in js


# ---------------------------------------------------------------------------
# Sessions, endpoint and the Kerala strict-budget contract
# ---------------------------------------------------------------------------

def test_attraction_image_endpoint_returns_verified_url(monkeypatch):
    import main
    from services import attraction_images

    class FakeResolver:
        last_status = {"state": "ok", "resolved": 1}

        def resolve(self, poi, city=None, guarantee=False):  # noqa: ARG002
            return "https://upload.wikimedia.org/kovalam.jpg"

    monkeypatch.setattr(attraction_images, "get_image_resolver", lambda: FakeResolver())
    main.app.config.update({"TESTING": True})
    client = main.app.test_client()

    response = client.get("/api/attraction-image?name=Kovalam&city=Kerala")
    assert response.status_code == 200
    body = response.get_json()
    assert body["image_url"] == "https://upload.wikimedia.org/kovalam.jpg"

    assert client.get("/api/attraction-image").status_code == 400


def test_sessions_keep_independent_trip_states():
    import main

    main.app.config.update({"TESTING": True})
    workflow_a = MagicMock()
    workflow_a.get_current_state.return_value = {
        "user_info": {"city": "Kochi", "days": 5}, "itinerary": [], "attractions": [],
    }
    workflow_b = MagicMock()
    workflow_b.get_current_state.return_value = {
        "user_info": {"city": "Tokyo", "days": 3}, "itinerary": [], "attractions": [],
    }
    main.workflows["session-a"] = workflow_a
    main.workflows["session-b"] = workflow_b
    try:
        client = main.app.test_client()
        a = client.get("/api/state?session_id=session-a").get_json()
        b = client.get("/api/state?session_id=session-b").get_json()
        assert a["trip"]["destination"] == "Kochi"
        assert b["trip"]["destination"] == "Tokyo"
        assert a["trip"]["days"] == 5 and b["trip"]["days"] == 3
    finally:
        main.workflows.pop("session-a", None)
        main.workflows.pop("session-b", None)


def test_strict_kerala_budget_keeps_the_users_cap_and_declares_shortfall():
    from agents.budget_agent import BudgetAgent

    prefs = {
        "city": "Kerala", "state": "Kerala", "country": "India",
        "is_international": False,
        "people": 10, "kids": 0, "days": 5,
        "budget": "medium", "budget_amount": 100000,
        "budget_strictness": "strict",
        "start_date": "2026-11-15",
    }
    spots = [
        {"name": "Fort Kochi Beach", "category": "beach", "price_level": 0,
         "location": {"lat": 9.9658, "lng": 76.2422}},
        {"name": "Mattupetty Dam", "category": "nature", "price_level": 1,
         "location": {"lat": 10.1000, "lng": 77.1300}},
    ]

    budget = BudgetAgent().estimate_budget(spots, prefs, False, None, None, None)

    assert budget["budget_limit"] == 100000
    assert budget["budget_type"] == "strict"
    assert budget["estimated_total"] > 0
    if budget["within_budget"] is False:
        assert budget["budget_infeasible"] is True
        assert "minimum" in (budget["budget_warning"] or "").lower()
    else:
        assert budget["within_budget"] is True
    # The user's cap is never rewritten by the planner.
    assert prefs["budget_amount"] == 100000
