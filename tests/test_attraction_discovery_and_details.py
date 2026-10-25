"""
Requirement regression tests (Requirements 1, 2 — attraction discovery + details).

All external HTTP is mocked so the tests are deterministic and safe to run in
any environment; nothing hits Geoapify/Overpass/Wikipedia. The intent is to
lock in the *behavioural* changes requested by Requirements 1 and 2:

R1 — attraction discovery breadth
  * The Overpass category filter now includes viewpoints, nature, heritage,
    worship, museums, markets and landmarks by DEFAULT (not just "famous
    landmarks"), so a hill-station like Darjeeling yields genuine breadth.
  * `get_attractions()` calls BOTH Geoapify and Overpass, rather than
    skipping Overpass when Geoapify returns >=10.
  * Deduplication still merges same-name/same-coordinate pairs but must NOT
    merge two distinct nearby attractions with different names.

R2 — attraction details & Wikipedia link
  * `get_attraction_details` returns a `wiki_url` when Wikipedia actually has
    an article; when it returns 404 the caller must receive NO fabricated URL.
  * Disambiguation pages are skipped rather than trusted.
"""
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# R1 — Attraction discovery
# ---------------------------------------------------------------------------

def test_overpass_category_filters_include_broad_baseline_without_hobbies():
    """The default Overpass filter must cover viewpoints, nature, heritage,
    worship, museums and markets — not just 'tourism=attraction' landmarks."""
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)  # avoid __init__ env lookups
    pm.overpass_endpoints = []
    filters = pm._get_category_filters(hobbies=None, radius=20000, lat=27.04, lng=88.26)

    required_substrings = [
        'tourism"~"attraction|museum|viewpoint',
        '"historic"',
        'leisure"~"park|nature_reserve',
        'natural"~"waterfall|peak',
        'place_of_worship',
        'marketplace',
    ]
    for s in required_substrings:
        assert s in filters, f"Expected Overpass filter to include {s!r}; got: {filters}"


def test_overpass_category_filters_add_hobby_specific_viewpoints():
    """Photography/viewpoint hobby must add an explicit tourism=viewpoint query."""
    from services.poi_fallback import POIManager
    pm = POIManager.__new__(POIManager)
    filters = pm._get_category_filters(
        hobbies="photography viewpoints", radius=20000, lat=27.04, lng=88.26
    )
    assert 'tourism"="viewpoint' in filters, (
        "Photography/viewpoint hobby must add an explicit viewpoint query"
    )


def test_get_attractions_calls_both_providers_regardless_of_geoapify_count():
    """
    Previously Overpass was skipped whenever Geoapify returned >=10 POIs,
    which is exactly the failure mode that truncated Darjeeling discovery.
    The new implementation must always run both providers.
    """
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    pm.geoapify_key = "fake-key"
    pm._cache = {}
    pm.pexels_key = ""

    geo_fake = [{"id": f"g{i}", "name": f"Geo {i}", "latitude": 27.0 + i * 1e-4,
                 "longitude": 88.26, "source": "geoapify"} for i in range(20)]
    osm_fake = [{"id": f"o{i}", "name": f"Osm {i}", "latitude": 27.04 + i * 1e-4,
                 "longitude": 88.26, "source": "osm"} for i in range(25)]

    with patch.object(POIManager, "_fetch_geoapify_attractions", return_value=geo_fake) as geo_mock, \
         patch.object(POIManager, "_fetch_osm_attractions", return_value=osm_fake) as osm_mock:
        result = pm.get_attractions(lat=27.04, lng=88.26, radius=20000, hobbies=None)

    assert geo_mock.called, "Geoapify provider must always be attempted"
    assert osm_mock.called, "Overpass provider must always be attempted (no <10 short-circuit)"
    # 20 + 25 distinct names → 45 unique POIs after dedup.
    assert len(result) == 45, f"Expected 45 merged POIs, got {len(result)}"


def test_deduplication_merges_same_place_but_keeps_distinct_attractions():
    """Dedup must remove the same POI returned twice by two providers, but
    must NOT merge two different attractions that happen to be near each other."""
    from services.poi_fallback import POIManager
    pm = POIManager.__new__(POIManager)

    candidates = [
        # Two providers describing the SAME viewpoint (identical name+coords).
        {"id": "g1", "name": "Peace Pagoda", "latitude": 27.0410, "longitude": 88.2610, "source": "geoapify"},
        {"id": "o1", "name": "Peace Pagoda", "latitude": 27.0410, "longitude": 88.2610, "source": "osm"},
        # A DIFFERENT attraction ~150m away (must be preserved, not merged).
        {"id": "o2", "name": "Darjeeling Himalayan Railway Museum",
         "latitude": 27.0424, "longitude": 88.2613, "source": "osm"},
    ]
    out = pm._deduplicate_pois(candidates)
    names = {p["name"] for p in out}
    assert "Peace Pagoda" in names, "Peace Pagoda should survive dedup"
    assert "Darjeeling Himalayan Railway Museum" in names, (
        "Distinct nearby attractions must NOT be merged away"
    )
    assert len(out) == 2, f"Expected 2 unique attractions, got {len(out)}: {names}"


# ---------------------------------------------------------------------------
# R2 — Attraction details & Wikipedia link
# ---------------------------------------------------------------------------

_WIKI_OK = {
    "type": "standard",
    "title": "Peace Pagoda, Darjeeling",
    "description": "Buddhist peace pagoda in Darjeeling, India",
    "extract": "The Peace Pagoda in Darjeeling is one of several Peace Pagodas "
               "built by Nipponzan-Myōhōji in India. It sits on a hill overlooking the town.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Peace_Pagoda,_Darjeeling"}},
    "thumbnail": {"source": "https://upload.example/darjeeling.jpg"},
    "coordinates": {"lat": 27.0408, "lon": 88.2609},
}


def _fake_wiki_resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    if payload is not None:
        r.json.return_value = payload
    return r


def test_get_attraction_details_returns_verified_wiki_url(tmp_path, monkeypatch):
    """When Wikipedia has an article, the endpoint must return its canonical URL."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Reload the module so the module-level disk cache points at a fresh dir.
    import importlib
    import services.attraction_details as ad
    monkeypatch.setattr(ad, "_DISK_CACHE_DIR", str(tmp_path / "acache"))
    ad._DETAIL_CACHE.clear()

    with patch("services.attraction_details.requests.get",
               return_value=_fake_wiki_resp(200, _WIKI_OK)) as mock_get:
        result = ad.get_attraction_details(name="Peace Pagoda Darjeeling", lat=27.04, lng=88.26)

    assert mock_get.called, "Wikipedia REST API must be queried"
    assert result.get("wiki_url") == _WIKI_OK["content_urls"]["desktop"]["page"]
    assert "Peace Pagoda" in result.get("description", "")
    assert result.get("description_source") == "wikipedia"


def test_get_attraction_details_never_fabricates_url_when_article_missing(tmp_path, monkeypatch):
    """A 404 from Wikipedia must produce NO wiki_url key — the caller (and the
    frontend) then shows 'Wikipedia article unavailable' rather than a made-up link."""
    import services.attraction_details as ad
    monkeypatch.setattr(ad, "_DISK_CACHE_DIR", str(tmp_path / "acache"))
    ad._DETAIL_CACHE.clear()

    with patch("services.attraction_details.requests.get",
               return_value=_fake_wiki_resp(404)):
        result = ad.get_attraction_details(name="Random Corner That Does Not Exist xyz123")

    assert "wiki_url" not in result, (
        f"404 must not yield a fabricated wiki_url; got: {result.get('wiki_url')!r}"
    )
    assert result.get("description_source") != "wikipedia"


def test_get_attraction_details_skips_disambiguation_pages(tmp_path, monkeypatch):
    """Disambiguation pages are not the correct article for a specific POI."""
    import services.attraction_details as ad
    monkeypatch.setattr(ad, "_DISK_CACHE_DIR", str(tmp_path / "acache"))
    ad._DETAIL_CACHE.clear()

    disamb = dict(_WIKI_OK)
    disamb["type"] = "disambiguation"

    # First candidate (name as-is) returns a disambiguation page; second
    # (article-stripped) 404s → the function must not return any wiki_url.
    responses = [_fake_wiki_resp(200, disamb), _fake_wiki_resp(404)]

    def _side_effect(*args, **kwargs):
        return responses.pop(0) if responses else _fake_wiki_resp(404)

    with patch("services.attraction_details.requests.get", side_effect=_side_effect):
        result = ad.get_attraction_details(name="The Smith Monument")

    assert "wiki_url" not in result, (
        f"Disambiguation pages must not be trusted; got: {result.get('wiki_url')!r}"
    )
