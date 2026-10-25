"""
test_geocode_resilience.py
==========================
Tests for the multi-provider geocoding resilience fix.

Verifies that Google Maps REQUEST_DENIED does NOT cause Indian cities such as
Varanasi to be misclassified as international destinations.

Run: pytest test_geocode_resilience.py -v

No network calls are made — Nominatim is disabled in every test, and Google Maps
is replaced by a MagicMock that raises REQUEST_DENIED.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# ── Setup ──────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Provide a fake key so InformationAgent.__init__ doesn't raise ValueError
os.environ.setdefault("MAPS_API_KEY", "fake-test-key-000000000000000000000000")
# Disable network providers so tests are fully offline
os.environ["GEOAPIFY_API_KEY"]    = ""
os.environ["NOMINATIM_ENABLED"]   = "false"
os.environ["PHOTON_ENABLED"]      = "false"


# ═══════════════════════════════════════════════════════════════════════════
# 1. StaticTableGeocoder — zero-network, always available
# ═══════════════════════════════════════════════════════════════════════════

class TestStaticTableGeocoder:
    def setup_method(self):
        from services.geocoding import StaticTableGeocoder
        self.g = StaticTableGeocoder()

    def test_varanasi_is_indian(self):
        r = self.g.geocode("Varanasi")
        assert r is not None
        assert r.country_code == "in"
        assert r.is_in_india() is True

    def test_vijayawada_is_indian(self):
        r = self.g.geocode("Vijayawada")
        assert r is not None and r.country_code == "in"

    def test_hyderabad_is_indian(self):
        r = self.g.geocode("Hyderabad")
        assert r is not None and r.country_code == "in"

    def test_delhi_is_indian(self):
        r = self.g.geocode("Delhi")
        assert r is not None and r.country_code == "in"

    def test_paris_is_international(self):
        r = self.g.geocode("Paris")
        assert r is not None
        assert r.country_code == "fr"
        assert r.is_in_india() is False

    def test_london_is_international(self):
        r = self.g.geocode("London")
        assert r is not None and r.country_code == "gb" and not r.is_in_india()

    def test_tokyo_is_international(self):
        r = self.g.geocode("Tokyo")
        assert r is not None and r.country_code == "jp"

    def test_dubai_is_international(self):
        r = self.g.geocode("Dubai")
        assert r is not None and r.country_code == "ae"

    def test_case_insensitive(self):
        assert self.g.geocode("VARANASI") is not None
        assert self.g.geocode("varanasi") is not None
        assert self.g.geocode("Varanasi") is not None

    def test_unknown_city_returns_none(self):
        assert self.g.geocode("Xyzzy_NoSuchCity_99999") is None

    def test_provider_name(self):
        r = self.g.geocode("Mumbai")
        assert r is not None and r.provider == "static"

    def test_varanasi_coordinates(self):
        r = self.g.geocode("Varanasi")
        assert r is not None
        assert 24.0 < r.lat < 27.0
        assert 81.0 < r.lng < 85.0


# ═══════════════════════════════════════════════════════════════════════════
# 2. GeocodingResult.is_in_india()
# ═══════════════════════════════════════════════════════════════════════════

class TestGeocodingResult:
    def _make(self, **kw):
        from services.geocoding import GeocodingResult
        return GeocodingResult(lat=kw.get("lat", 0), lng=kw.get("lng", 0),
                               country_code=kw.get("cc", ""))

    def test_india_by_country_code(self):
        assert self._make(lat=25.3, lng=82.9, cc="in").is_in_india() is True

    def test_international_by_country_code(self):
        assert self._make(lat=48.8, lng=2.3, cc="fr").is_in_india() is False

    def test_india_by_bbox_no_cc(self):
        assert self._make(lat=25.3, lng=82.9, cc="").is_in_india() is True

    def test_international_by_bbox_no_cc(self):
        assert self._make(lat=48.8, lng=2.3, cc="").is_in_india() is False


# ═══════════════════════════════════════════════════════════════════════════
# 3. GeocodingManager cascade — Google fails → static table rescues
# ═══════════════════════════════════════════════════════════════════════════

def _failing_gmaps():
    """Return a googlemaps.Client mock that raises REQUEST_DENIED on every call."""
    try:
        import googlemaps.exceptions
        exc = googlemaps.exceptions.ApiError("REQUEST_DENIED")
    except ImportError:
        exc = Exception("REQUEST_DENIED")

    mock = MagicMock()
    mock.geocode.side_effect = exc
    mock.places.side_effect  = exc
    return mock


class TestGeocodingManagerCascade:
    def _manager(self):
        from services.geocoding import GeocodingManager
        return GeocodingManager()

    def test_varanasi_resolved_via_static_when_google_fails(self):
        """CRITICAL original bug: Varanasi must resolve even when Google raises REQUEST_DENIED."""
        r = self._manager().geocode("Varanasi")
        assert r is not None, "Static table should have resolved Varanasi"
        assert r.country_code == "in"
        assert r.provider == "static"

    def test_paris_resolved_via_static_when_google_fails(self):
        r = self._manager().geocode("Paris")
        assert r is not None
        assert r.is_in_india() is False
        assert r.provider == "static"

    def test_cache_hit_on_repeated_call(self):
        mgr = self._manager()
        r1 = mgr.geocode("Mumbai")
        r2 = mgr.geocode("Mumbai")
        assert r1 is not None
        assert r2 is not None
        assert r2.cached is True

    def test_unknown_city_returns_none(self):
        r = self._manager().geocode("Xyzzy_Nonexistent_City_99999")
        assert r is None

    def test_varanasi_coordinates_are_reasonable(self):
        r = self._manager().geocode("Varanasi")
        assert r is not None
        assert 24.0 < r.lat < 27.0
        assert 81.0 < r.lng < 85.0


# ═══════════════════════════════════════════════════════════════════════════
# 4. validate_indian_location — 3-state return (True/False/None)
# ═══════════════════════════════════════════════════════════════════════════

def _make_agent_with_failing_google():
    """Create a minimal agent whose geocoder uses free fallback."""
    from services.geocoding import GeocodingManager
    from agents.information_agent import InformationAgent

    mgr = GeocodingManager()
    agent = MagicMock()
    agent.geocoder = mgr
    agent.validate_indian_location = (
        lambda loc: InformationAgent.validate_indian_location(agent, loc)
    )
    agent.city2geocode = (
        lambda city: InformationAgent.city2geocode(agent, city)
    )
    return agent


class TestValidateIndianLocation:
    def setup_method(self):
        self.agent = _make_agent_with_failing_google()

    def test_varanasi_returns_true_not_false(self):
        """THE original bug: Varanasi must return True even when Google is denied."""
        result = self.agent.validate_indian_location("Varanasi")
        assert result is True, (
            f"Varanasi with REQUEST_DENIED must return True (via static table), got {result!r}"
        )

    def test_paris_returns_false(self):
        assert self.agent.validate_indian_location("Paris") is False

    def test_london_returns_false(self):
        assert self.agent.validate_indian_location("London") is False

    def test_tokyo_returns_false(self):
        assert self.agent.validate_indian_location("Tokyo") is False

    def test_vijayawada_returns_true(self):
        assert self.agent.validate_indian_location("Vijayawada") is True

    def test_hyderabad_returns_true(self):
        assert self.agent.validate_indian_location("Hyderabad") is True

    def test_delhi_returns_true(self):
        assert self.agent.validate_indian_location("Delhi") is True

    def test_unknown_city_returns_none_not_false(self):
        """Unknown city → None (API unavailable), NOT False (international)."""
        result = self.agent.validate_indian_location("Xyzzy_NonExistent_City_99999")
        assert result is None, (
            f"Unknown city must return None, not False (which implies international). Got: {result!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. city2geocode — returns dict or None
# ═══════════════════════════════════════════════════════════════════════════

class TestCity2Geocode:
    def setup_method(self):
        self.agent = _make_agent_with_failing_google()

    def test_varanasi_returns_coords(self):
        r = self.agent.city2geocode("Varanasi")
        assert r is not None
        assert "lat" in r and "lng" in r
        assert 24.0 < r["lat"] < 27.0
        assert 81.0 < r["lng"] < 85.0

    def test_paris_returns_coords(self):
        r = self.agent.city2geocode("Paris")
        assert r is not None
        assert "lat" in r and "lng" in r

    def test_unknown_city_returns_none(self):
        r = self.agent.city2geocode("Xyzzy_Nonexistent_99999")
        assert r is None


# ═══════════════════════════════════════════════════════════════════════════
# 6. travel_graph._INDIA_LOCAL_CITY_SET — local tiebreaker
# ═══════════════════════════════════════════════════════════════════════════

class TestTravelGraphLocalCitySet:
    def setup_method(self):
        from workflows.travel_graph import _INDIA_LOCAL_CITY_SET
        self.s = _INDIA_LOCAL_CITY_SET

    def test_varanasi_present(self):
        assert "varanasi" in self.s

    def test_vijayawada_present(self):
        assert "vijayawada" in self.s

    def test_hyderabad_present(self):
        assert "hyderabad" in self.s

    def test_agra_present(self):
        assert "agra" in self.s

    def test_jaipur_present(self):
        assert "jaipur" in self.s

    def test_paris_absent(self):
        assert "paris" not in self.s

    def test_london_absent(self):
        assert "london" not in self.s

    def test_tokyo_absent(self):
        assert "tokyo" not in self.s


# ═══════════════════════════════════════════════════════════════════════════
# 7. Static table coverage — worldwide map requirement
# ═══════════════════════════════════════════════════════════════════════════

class TestStaticTableCoverage:
    def setup_method(self):
        from services.geocoding import _STATIC_COORDS
        self.coords = _STATIC_COORDS

    def test_international_cities_present(self):
        required = ["paris", "london", "tokyo", "dubai", "new york",
                    "singapore", "bangkok", "bali", "sydney", "rome"]
        for city in required:
            assert city in self.coords, f"International city '{city}' missing from static table"

    def test_up_cities_all_present(self):
        """All UP tourism cities from INDIA_STATES_MAP must be in the static table."""
        from workflows.travel_graph import INDIA_STATES_MAP
        for city in INDIA_STATES_MAP.get("uttar pradesh", {}).get("cities", []):
            assert city.lower() in self.coords, (
                f"UP city '{city}' not in static table — Varanasi scenario would fail"
            )

    def test_worldwide_not_india_only(self):
        """Static table must contain entries outside India's bounding box."""
        from services.geocoding import INDIA_LAT_MIN, INDIA_LAT_MAX, INDIA_LNG_MIN, INDIA_LNG_MAX
        non_india = [
            v for v in self.coords.values()
            if not (INDIA_LAT_MIN <= v[0] <= INDIA_LAT_MAX
                    and INDIA_LNG_MIN <= v[1] <= INDIA_LNG_MAX)
        ]
        assert len(non_india) >= 30, (
            f"Static table has only {len(non_india)} non-India entries — map would be India-only"
        )
