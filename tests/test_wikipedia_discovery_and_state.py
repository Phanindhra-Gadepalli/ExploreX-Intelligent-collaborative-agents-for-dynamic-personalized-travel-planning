"""Regression tests for the Wikipedia notability layer and state contracts.

These tests are fully offline: MediaWiki is mocked, no provider credentials or
network access are required.
"""
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Wikipedia GeoSearch provider
# ---------------------------------------------------------------------------

_WIKI_PAGES = {
    "query": {
        "pages": [
            {
                "pageid": 101,
                "title": "Sensō-ji",
                "fullurl": "https://en.wikipedia.org/wiki/Sens%C5%8D-ji",
                "coordinates": [{"lat": 35.7148, "lon": 139.7967}],
                "extract": (
                    "Sensō-ji is an ancient Buddhist temple located in Asakusa, "
                    "Tokyo. It is Tokyo's oldest temple and one of its most "
                    "significant."
                ),
                "thumbnail": {"source": "https://upload.example/sensoji.jpg"},
                "pageprops": {"wikibase_item": "Q194883"},
                "categories": [
                    {"title": "Category:Buddhist temples in Tokyo"},
                    {"title": "Category:Tourist attractions in Tokyo"},
                ],
            },
            {
                "pageid": 102,
                "title": "Ueno Station",
                "coordinates": [{"lat": 35.7138, "lon": 139.7772}],
                "extract": "Ueno Station is a major railway station in Tokyo.",
                "pageprops": {},
                "categories": [],
            },
            {
                "pageid": 103,
                "title": "University of Tokyo",
                "coordinates": [{"lat": 35.7126, "lon": 139.7620}],
                "extract": "The University of Tokyo is a public research university.",
                "pageprops": {},
                "categories": [],
            },
            {
                "pageid": 104,
                "title": "Asakusa",
                "coordinates": [{"lat": 35.7148, "lon": 139.7967}],
                "extract": "Asakusa is a district in Taito, Tokyo.",
                "pageprops": {"disambiguation": ""},
                "categories": [{"title": "Category:Disambiguation pages"}],
            },
            {
                "pageid": 105,
                "title": "Article without coordinates",
                "extract": "This page has no coordinates.",
                "pageprops": {},
                "categories": [],
            },
            {
                "pageid": 106,
                "title": "Siege of Sanjō Palace",
                "coordinates": [{"lat": 35.679, "lon": 139.764}],
                "extract": "The Siege of Sanjō Palace was a battle in Kyoto.",
                "pageprops": {},
                "categories": [{"title": "Category:Tourist attractions in Kyoto"}],
            },
        ]
    }
}


def _wiki_manager():
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    pm.wikipedia_enabled = True
    pm.wikipedia_language = "en"
    return pm


def test_wikipedia_geosearch_returns_verifiable_notable_places():
    pm = _wiki_manager()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = _WIKI_PAGES

    with patch("services.poi_fallback.requests.get", return_value=response) as get:
        results = pm._fetch_wikipedia_attractions(35.6762, 139.6503, 20000)

    assert get.called
    params = get.call_args.kwargs["params"]
    assert params["generator"] == "geosearch"
    assert params["ggsradius"] == 10000, "GeoSearch supports at most a 10 km radius"
    assert get.call_args.kwargs["headers"]["User-Agent"].startswith("ExploreX")

    assert [p["name"] for p in results] == ["Sensō-ji"]
    poi = results[0]
    assert poi["source"] == "wikipedia"
    assert poi["id"] == "wiki_101"
    assert poi["latitude"] == pytest.approx(35.7148)
    assert poi["longitude"] == pytest.approx(139.7967)
    assert poi["wikipedia_url"].startswith("https://en.wikipedia.org/wiki/")
    assert poi["image_url"] == "https://upload.example/sensoji.jpg"
    assert poi["description"].startswith("Sensō-ji is an ancient Buddhist temple")
    assert poi["metadata"]["wikidata_id"] == "Q194883"
    assert poi["metadata"]["wikipedia_pageid"] == 101
    # Categories are preserved as structured evidence for interest matching.
    assert any("Buddhist temples" in value for value in poi["types"])
    assert poi["category"] in ("temples", "tourist_attraction")


def test_wikipedia_search_strips_a_country_suffix_from_the_destination():
    """A city stored as "Tokyo, Japan" must search Wikipedia for "Tokyo"."""
    pm = _wiki_manager()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = _WIKI_PAGES

    with patch("services.poi_fallback.requests.get", return_value=response) as get, \
         patch("services.poi_fallback.time.sleep", return_value=None):
        pm._fetch_wikipedia_attractions(
            35.6762, 139.6503, 20000, city="Tokyo, Japan"
        )

    calls = [call.kwargs["params"] for call in get.call_args_list]
    searches = [p.get("gsrsearch") for p in calls if p.get("generator") == "search"]
    categories = [p.get("gcmtitle") for p in calls if p.get("generator") == "categorymembers"]
    assert searches == ["tourist attractions in Tokyo"]
    assert categories == ["Category:Tourist attractions in Tokyo"]


def test_wikipedia_geosearch_filters_administrative_and_disambiguation_pages():
    pm = _wiki_manager()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = _WIKI_PAGES

    with patch("services.poi_fallback.requests.get", return_value=response):
        results = pm._fetch_wikipedia_attractions(35.6762, 139.6503, 20000)

    names = {p["name"] for p in results}
    assert names == {"Sensō-ji"}, (
        "stations, universities, disambiguation pages and coord-less pages "
        f"must never become attractions; got {names}"
    )


# ---------------------------------------------------------------------------
# Deduplication across providers
# ---------------------------------------------------------------------------

def test_dedup_merges_normalised_names_and_keeps_the_wikipedia_record():
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    candidates = [
        {
            "id": "geo-sensoji", "name": "Senso-ji", "source": "geoapify",
            "latitude": 35.7148, "longitude": 139.7967,
            "category": "religion.place_of_worship",
            "address": "2-3-1 Asakusa, Taito, Tokyo",
        },
        {
            "id": "osm-sensoji", "name": "Sensō-ji", "source": "osm",
            "latitude": 35.71485, "longitude": 139.79675,
            "description": "Buddhist temple in Asakusa.",
        },
        {
            "id": "wiki_101", "name": "Sensō-ji", "source": "wikipedia",
            "latitude": 35.7148, "longitude": 139.7967,
            "wikipedia_url": "https://en.wikipedia.org/wiki/Sens%C5%8D-ji",
            "metadata": {"wikidata_id": "Q194883"},
        },
    ]

    out = pm._deduplicate_pois(candidates)

    assert len(out) == 1
    poi = out[0]
    assert poi["source"] == "wikipedia", "the notable record must win"
    assert poi["wikipedia_url"]
    # Facts supplied by the other providers are not lost.
    assert poi["address"] == "2-3-1 Asakusa, Taito, Tokyo"
    assert poi["description"] == "Buddhist temple in Asakusa."
    assert set(poi["sources"]) == {"geoapify", "osm", "wikipedia"}


def test_dedup_keeps_genuinely_distinct_attractions():
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    out = pm._deduplicate_pois([
        {"id": "a", "name": "Sensō-ji", "latitude": 35.7148, "longitude": 139.7967, "source": "wikipedia"},
        {"id": "b", "name": "Tokyo Skytree", "latitude": 35.7101, "longitude": 139.8107, "source": "wikipedia"},
        {"id": "c", "name": "Asakusa Shrine", "latitude": 35.7149, "longitude": 139.7971, "source": "osm"},
    ])

    assert {p["name"] for p in out} == {"Sensō-ji", "Tokyo Skytree", "Asakusa Shrine"}


# ---------------------------------------------------------------------------
# Ranking: notability must beat incidental OSM noise
# ---------------------------------------------------------------------------

def test_wikipedia_landmark_outranks_an_obscure_osm_node():
    from agents.information_agent import InformationAgent

    agent = InformationAgent.__new__(InformationAgent)
    prefs = {"hobbies": "photography and viewpoints"}

    notable = {
        "id": "wiki_1", "name": "Tokyo Metropolitan Government Building",
        "category": "architecture", "source": "wikipedia",
        "description": (
            "The Tokyo Metropolitan Government Building has two observation "
            "decks with panoramic views over the city."
        ),
        "types": ["Observation towers in Tokyo"],
        "wikipedia_url": "https://en.wikipedia.org/wiki/Tokyo_Metropolitan_Government_Building",
        "metadata": {"wikidata_id": "Q1142025"},
        "location": {"lat": 35.6895, "lng": 139.6917},
        "image_url": "https://upload.example/tmg.jpg",
    }
    obscure = {
        "id": "geo_1", "name": "小平中央公園", "category": "leisure",
        "source": "geoapify", "location": {"lat": 35.72, "lng": 139.47},
    }

    notable_score, notable_matches = agent._calculate_score(notable, prefs)
    obscure_score, obscure_matches = agent._calculate_score(obscure, prefs)

    assert "viewpoints" in notable_matches
    assert notable_score > obscure_score
    assert notable_score > 0 and obscure_score >= 0


# ---------------------------------------------------------------------------
# Workflow state contract
# ---------------------------------------------------------------------------

def _recommend_workflow():
    from workflows.travel_graph import TravelGraph

    wf = TravelGraph.__new__(TravelGraph)
    wf.session_states = {}
    wf.state = {
        "user_info": {
            "city": "Tokyo", "days": 2, "people": 2, "hobbies": "photography",
            "budget": "medium",
        },
        "attractions": [
            {
                "id": "a1", "name": "Sensō-ji", "category": "temples",
                "recommendation_type": "interest_based",
                "location": {"lat": 35.7148, "lng": 139.7967},
            },
            {
                "id": "a2", "name": "Tokyo Skytree", "category": "viewpoints",
                "recommendation_type": "interest_based",
                "location": {"lat": 35.7101, "lng": 139.8107},
            },
            {
                "id": "a3", "name": "Meiji Jingu", "category": "temples",
                "recommendation_type": "interest_based",
                "location": {"lat": 35.6764, "lng": 139.6993},
            },
            {
                "id": "a4", "name": "Ueno Park", "category": "nature",
                "recommendation_type": "outside_interests",
                "location": {"lat": 35.7156, "lng": 139.7745},
            },
        ],
        "selected_attractions": [],
        "accommodations": [],
        "restaurants": [],
    }
    wf.recommend_agent = MagicMock()
    wf.recommend_agent.generate_map_data.return_value = []
    return wf


def test_process_recommend_always_returns_state():
    wf = _recommend_workflow()

    # 1. Initial recommendation (no selection, no confirmation phrase).
    result = wf._process_recommend()
    assert result["next_step"] == "recommend"
    assert result["state"]["attractions"], "state is required for /api/state"

    # 2. Insufficient selection without an explicit override.
    result = wf._process_recommend(
        selected_attraction_ids=["a1"], user_input=""
    )
    assert result["next_step"] == "recommend"
    assert result["validation_warning"] is True
    assert result["state"]["selected_attractions"][0]["id"] == "a1"

    # 3. Explicit override proceeds (the user accepted fewer places).
    result = wf._process_recommend(
        selected_attraction_ids=["a1"], user_input="", force_continue="true"
    )
    assert result["next_step"] == "strategy"
    assert result["state"]["selected_attractions"][0]["id"] == "a1"

    # 4. Explicit "assistant chooses" confirmation.
    result = wf._process_recommend(user_input="go ahead")
    assert result["next_step"] == "strategy"
    assert len(result["selected_attractions"]) == 4
    assert result["state"] is not None


def test_api_state_accepts_an_explicit_session_id():
    import main

    main.app.config.update({"TESTING": True})
    wf = MagicMock()
    wf.get_current_state.return_value = {
        "user_info": {"city": "Tokyo", "days": 5},
        "itinerary": [{"day": 1, "activities": []}],
    }
    main.workflows["state-query-sess"] = wf
    try:
        # No session cookie at all: the query parameter alone must resolve it.
        client = main.app.test_client()
        response = client.get("/api/state?session_id=state-query-sess")

        assert response.status_code == 200
        body = response.get_json()
        assert body["session_id"] == "state-query-sess"
        assert body["trip"]["destination"] == "Tokyo"
        assert body["trip"]["days"] == 5
    finally:
        main.workflows.pop("state-query-sess", None)
