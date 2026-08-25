"""
services/geocoding.py — Multi-Provider Geocoding Manager for ExploreX
======================================================================

Provider cascade (in order of preference):
  1. Google Maps    — preferred when MAPS_API_KEY has billing enabled
  2. Geoapify       — free tier 3 000 req/day (needs GEOAPIFY_API_KEY)
  3. Nominatim OSM  — free public geocoder, max 1 req/s (on by default)
  4. Photon         — OSM-based open-source geocoder (opt-in)
  5. Static table   — ~300 major cities, zero-network, always available

All providers return a normalised GeocodingResult dataclass.
Results are cached in-memory (TTL: GEOCODER_CACHE_TTL env var, default 3600 s).

A Google Maps REQUEST_DENIED / billing error causes the manager to fall through
to the next provider transparently.  The caller receives the same GeocodingResult
regardless of which provider succeeded.

Environment variables (all optional):
    GEOAPIFY_API_KEY    — Geoapify key (free tier, no billing required)
    NOMINATIM_ENABLED   — "true" / "false"  (default: "true")
    PHOTON_ENABLED      — "true" / "false"  (default: "false")
    GEOCODER_CACHE_TTL  — cache TTL in seconds (default: 3600)

India bounding box: lat 6.4–36.0 N, lng 68.0–97.5 E
"""

from __future__ import annotations

import copy
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Google Maps API has been completely removed.

# ---------------------------------------------------------------------------
# India bounding box
# ---------------------------------------------------------------------------
INDIA_LAT_MIN, INDIA_LAT_MAX = 6.4, 36.0
INDIA_LNG_MIN, INDIA_LNG_MAX = 68.0, 97.5


# ---------------------------------------------------------------------------
# Normalised result
# ---------------------------------------------------------------------------

@dataclass
class GeocodingResult:
    """Normalised geocoding result shared by every provider."""

    lat: float
    lng: float
    display_name: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    country_code: str = ""   # ISO 3166-1 alpha-2 lowercase ("in", "fr", …)
    provider: str = ""
    cached: bool = False

    def is_in_india(self) -> bool:
        """Return True if this result represents an Indian location."""
        if self.country_code:
            return self.country_code.lower() == "in"
        # Bounding-box fallback when country_code is absent
        return (INDIA_LAT_MIN <= self.lat <= INDIA_LAT_MAX and
                INDIA_LNG_MIN <= self.lng <= INDIA_LNG_MAX)


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class GeocodingProvider(ABC):
    """Every geocoding source must implement this interface."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def geocode(self, city: str) -> Optional[GeocodingResult]:
        """Return a GeocodingResult or None if the city was not found / provider failed."""
        ...




# ---------------------------------------------------------------------------
# Provider 2 — Geoapify
# ---------------------------------------------------------------------------

class GeoapifyGeocoder(GeocodingProvider):
    """Geoapify Geocoding API — free tier 3 000 req/day, no billing required."""

    name = "geoapify"
    _BASE = "https://api.geoapify.com/v1/geocode/search"

    def __init__(self, api_key: str = None):
        self._key = api_key or os.getenv("GEOAPIFY_API_KEY", "")

    def geocode(self, city: str) -> Optional[GeocodingResult]:
        if not self._key:
            return None
        try:
            import requests as _req
            params = {"text": city, "format": "json", "apiKey": self._key,
                      "limit": 1, "lang": "en"}
            resp = _req.get(self._BASE, params=params, timeout=8)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return None
            r = results[0]
            lat, lon = r.get("lat"), r.get("lon")
            if lat is None or lon is None:
                return None
            return GeocodingResult(
                lat=float(lat), lng=float(lon),
                display_name=r.get("formatted", city),
                city=r.get("city", r.get("town", r.get("village", ""))),
                state=r.get("state", ""),
                country=r.get("country", ""),
                country_code=r.get("country_code", "").lower(),
                provider=self.name,
            )
        except Exception as e:
            logger.warning(f"[GEOCODER][Geoapify] '{city}': {e}")
            return None


# ---------------------------------------------------------------------------
# Provider 3 — Nominatim (OpenStreetMap public server)
# ---------------------------------------------------------------------------

class NominatimGeocoder(GeocodingProvider):
    """
    Nominatim public geocoder.

    Usage-policy compliance:
    - Descriptive User-Agent.
    - Hard rate-limit: max 1 req/s enforced by _throttle().
    - GeocodingManager caches results — Nominatim is NOT hit on repeated calls.
    - Not used for bulk / autocomplete queries.
    """

    name = "nominatim"
    _BASE = "https://nominatim.openstreetmap.org/search"
    _UA   = "ExploreX-Travel-Planner/1.0 (educational; contact@explorex.app)"
    _MIN_S = 1.1   # slightly above 1 req/s policy

    def __init__(self):
        self._last: float = 0.0

    def _throttle(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self._MIN_S:
            time.sleep(self._MIN_S - elapsed)
        self._last = time.monotonic()

    def geocode(self, city: str) -> Optional[GeocodingResult]:
        self._throttle()
        try:
            import requests as _req
            params = {"q": city, "format": "json", "addressdetails": 1,
                      "limit": 1, "accept-language": "en"}
            resp = _req.get(self._BASE, params=params,
                            headers={"User-Agent": self._UA}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            r = data[0]
            lat = float(r.get("lat") or 0)
            lon = float(r.get("lon") or 0)
            if lat == 0.0 and lon == 0.0:
                return None
            addr = r.get("address", {})
            city_name = (addr.get("city") or addr.get("town")
                         or addr.get("village") or "")
            return GeocodingResult(
                lat=lat, lng=lon,
                display_name=r.get("display_name", city),
                city=city_name,
                state=addr.get("state", ""),
                country=addr.get("country", ""),
                country_code=addr.get("country_code", "").lower(),
                provider=self.name,
            )
        except Exception as e:
            logger.warning(f"[GEOCODER][Nominatim] '{city}': {e}")
            return None


# ---------------------------------------------------------------------------
# Provider 4 — Photon (komoot, OSM-based, opt-in)
# ---------------------------------------------------------------------------

class PhotonGeocoder(GeocodingProvider):
    """Photon geocoder — open-source, OSM-based. Opt-in via PHOTON_ENABLED=true."""

    name = "photon"
    _BASE = "https://photon.komoot.io/api/"

    def geocode(self, city: str) -> Optional[GeocodingResult]:
        try:
            import requests as _req
            resp = _req.get(self._BASE, params={"q": city, "limit": 1, "lang": "en"}, timeout=8)
            resp.raise_for_status()
            feats = resp.json().get("features", [])
            if not feats:
                return None
            feat  = feats[0]
            props = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                return None
            lon, lat = float(coords[0]), float(coords[1])
            cc = props.get("countrycode", "").lower()
            city_name = (props.get("city") or props.get("locality")
                         or props.get("name", ""))
            return GeocodingResult(
                lat=lat, lng=lon,
                display_name=props.get("name", city),
                city=city_name,
                state=props.get("state", ""),
                country=props.get("country", ""),
                country_code=cc,
                provider=self.name,
            )
        except Exception as e:
            logger.warning(f"[GEOCODER][Photon] '{city}': {e}")
            return None


# ---------------------------------------------------------------------------
# Provider 5 — Static lookup table (zero-network, always available)
# ---------------------------------------------------------------------------
# fmt: off
# (lat, lng, ISO-3166-1-alpha-2 lowercase)
# Covers every city in INDIA_KNOWN_NAMES (retrieval_agent.py) and all tourism
# cities in INDIA_STATES_MAP (travel_graph.py) plus 100+ world tourist cities.
_STATIC_COORDS: dict[str, tuple[float, float, str]] = {
    # ── Rajasthan ──────────────────────────────────────────────────────────
    "jaipur":             (26.9124, 75.7873, "in"),
    "udaipur":            (24.5854, 73.7125, "in"),
    "jodhpur":            (26.2389, 73.0243, "in"),
    "jaisalmer":          (26.9157, 70.9083, "in"),
    "pushkar":            (26.4897, 74.5511, "in"),
    "ajmer":              (26.4499, 74.6399, "in"),
    "bikaner":            (28.0229, 73.3119, "in"),
    "mount abu":          (24.5926, 72.7156, "in"),
    "ranthambore":        (26.0173, 76.5026, "in"),
    "sawai madhopur":     (26.0173, 76.5026, "in"),
    # ── Kerala ─────────────────────────────────────────────────────────────
    "kochi":              ( 9.9312, 76.2673, "in"),
    "munnar":             (10.0889, 77.0595, "in"),
    "alleppey":           ( 9.4981, 76.3388, "in"),
    "alappuzha":          ( 9.4981, 76.3388, "in"),
    "thiruvananthapuram": ( 8.5241, 76.9366, "in"),
    "wayanad":            (11.6854, 76.1320, "in"),
    "kovalam":            ( 8.4004, 76.9787, "in"),
    "varkala":            ( 8.7379, 76.7163, "in"),
    "thrissur":           (10.5276, 76.2144, "in"),
    "kollam":             ( 8.8932, 76.6141, "in"),
    "kozhikode":          (11.2588, 75.7804, "in"),
    # ── Goa ────────────────────────────────────────────────────────────────
    "panaji":             (15.4909, 73.8278, "in"),
    "calangute":          (15.5440, 73.7552, "in"),
    "vasco da gama":      (15.3982, 73.8142, "in"),
    "margao":             (15.2993, 73.9862, "in"),
    "anjuna":             (15.5763, 73.7434, "in"),
    "palolem":            (15.0101, 74.0233, "in"),
    "goa":                (15.2993, 74.1240, "in"),
    # ── Himachal Pradesh ───────────────────────────────────────────────────
    "shimla":             (31.1048, 77.1734, "in"),
    "manali":             (32.2432, 77.1892, "in"),
    "dharamshala":        (32.2190, 76.3234, "in"),
    "mcleod ganj":        (32.2427, 76.3209, "in"),
    "dalhousie":          (32.5382, 75.9737, "in"),
    "kasauli":            (30.9004, 76.9570, "in"),
    "spiti":              (32.2457, 78.0331, "in"),
    "kullu":              (31.9578, 77.1095, "in"),
    # ── Uttarakhand ────────────────────────────────────────────────────────
    "rishikesh":          (30.0869, 78.2676, "in"),
    "haridwar":           (29.9457, 78.1642, "in"),
    "mussoorie":          (30.4598, 78.0664, "in"),
    "nainital":           (29.3803, 79.4636, "in"),
    "auli":               (30.5318, 79.5664, "in"),
    "jim corbett":        (29.5300, 79.0000, "in"),
    "corbett":            (29.5300, 79.0000, "in"),
    "dehradun":           (30.3165, 78.0322, "in"),
    # ── Tamil Nadu ─────────────────────────────────────────────────────────
    "chennai":            (13.0827, 80.2707, "in"),
    "madurai":            ( 9.9252, 78.1198, "in"),
    "ooty":               (11.4102, 76.6950, "in"),
    "mahabalipuram":      (12.6269, 80.1927, "in"),
    "thanjavur":          (10.7869, 79.1378, "in"),
    "coimbatore":         (11.0168, 76.9558, "in"),
    "tiruchirapalli":     (10.7905, 78.7047, "in"),
    "trichy":             (10.7905, 78.7047, "in"),
    "kanchipuram":        (12.8342, 79.7036, "in"),
    "kodaikanal":         (10.2381, 77.4892, "in"),
    "pondicherry":        (11.9416, 79.8083, "in"),
    "puducherry":         (11.9416, 79.8083, "in"),
    "auroville":          (12.0050, 79.8117, "in"),
    "rameswaram":         ( 9.2876, 79.3129, "in"),
    "rameshwaram":        ( 9.2876, 79.3129, "in"),
    "kanyakumari":        ( 8.0883, 77.5385, "in"),
    # ── Karnataka ──────────────────────────────────────────────────────────
    "bangalore":          (12.9716, 77.5946, "in"),
    "bengaluru":          (12.9716, 77.5946, "in"),
    "mysore":             (12.2958, 76.6394, "in"),
    "mysuru":             (12.2958, 76.6394, "in"),
    "hampi":              (15.3350, 76.4600, "in"),
    "coorg":              (12.3375, 75.8069, "in"),
    "badami":             (15.9102, 75.6790, "in"),
    "chikmagalur":        (13.3161, 75.7720, "in"),
    "udupi":              (13.3409, 74.7421, "in"),
    "gokarna":            (14.5479, 74.3188, "in"),
    "mangalore":          (12.9141, 74.8560, "in"),
    # ── Maharashtra ────────────────────────────────────────────────────────
    "mumbai":             (19.0760, 72.8777, "in"),
    "pune":               (18.5204, 73.8567, "in"),
    "aurangabad":         (19.8762, 75.3433, "in"),
    "nashik":             (19.9975, 73.7898, "in"),
    "nasik":              (19.9975, 73.7898, "in"),
    "mahabaleshwar":      (17.9236, 73.6586, "in"),
    "lonavala":           (18.7548, 73.4075, "in"),
    "nagpur":             (21.1458, 79.0882, "in"),
    # ── Uttar Pradesh ──────────────────────────────────────────────────────
    "agra":               (27.1767, 78.0081, "in"),
    "varanasi":           (25.3176, 82.9739, "in"),
    "lucknow":            (26.8467, 80.9462, "in"),
    "mathura":            (27.4924, 77.6737, "in"),
    "prayagraj":          (25.4358, 81.8463, "in"),
    "vrindavan":          (27.5806, 77.6969, "in"),
    "allahabad":          (25.4358, 81.8463, "in"),
    "kanpur":             (26.4499, 80.3319, "in"),
    "ayodhya":            (26.7922, 82.1998, "in"),
    # ── West Bengal ────────────────────────────────────────────────────────
    "kolkata":            (22.5726, 88.3639, "in"),
    "darjeeling":         (27.0410, 88.2663, "in"),
    "siliguri":           (26.7271, 88.3953, "in"),
    "shantiniketan":      (23.6831, 87.6844, "in"),
    "bishnupur":          (23.0773, 87.3193, "in"),
    # ── Gujarat ────────────────────────────────────────────────────────────
    "ahmedabad":          (23.0225, 72.5714, "in"),
    "vadodara":           (22.3072, 73.1812, "in"),
    "surat":              (21.1702, 72.8311, "in"),
    "somnath":            (20.9078, 70.3989, "in"),
    "dwarka":             (22.2441, 68.9685, "in"),
    "rajkot":             (22.3039, 70.8022, "in"),
    "jamnagar":           (22.4707, 70.0577, "in"),
    "bhavnagar":          (21.7645, 72.1519, "in"),
    "gir":                (21.1243, 70.8242, "in"),
    # ── Punjab ─────────────────────────────────────────────────────────────
    "amritsar":           (31.6340, 74.8723, "in"),
    "chandigarh":         (30.7333, 76.7794, "in"),
    "ludhiana":           (30.9009, 75.8573, "in"),
    "anandpur sahib":     (31.2378, 76.4964, "in"),
    "patiala":            (30.3398, 76.3869, "in"),
    # ── Madhya Pradesh ─────────────────────────────────────────────────────
    "bhopal":             (23.2599, 77.4126, "in"),
    "indore":             (22.7196, 75.8577, "in"),
    "gwalior":            (26.2183, 78.1828, "in"),
    "khajuraho":          (24.8318, 79.9199, "in"),
    "ujjain":             (23.1793, 75.7849, "in"),
    "orchha":             (25.3509, 78.6406, "in"),
    # ── Andhra Pradesh ─────────────────────────────────────────────────────
    "visakhapatnam":      (17.6868, 83.2185, "in"),
    "vizag":              (17.6868, 83.2185, "in"),
    "tirupati":           (13.6288, 79.4192, "in"),
    "vijayawada":         (16.5062, 80.6480, "in"),
    "araku valley":       (18.3271, 83.0500, "in"),
    "kakinada":           (16.9891, 82.2475, "in"),
    "srisailam":          (16.0741, 78.8698, "in"),
    # ── Telangana ──────────────────────────────────────────────────────────
    "hyderabad":          (17.3850, 78.4867, "in"),
    "warangal":           (17.9784, 79.5941, "in"),
    "nizamabad":          (18.6726, 78.0940, "in"),
    "nagarjuna sagar":    (16.5724, 79.3153, "in"),
    "karimnagar":         (18.4386, 79.1288, "in"),
    # ── J&K / Ladakh ───────────────────────────────────────────────────────
    "srinagar":           (34.0837, 74.7973, "in"),
    "gulmarg":            (34.0484, 74.3805, "in"),
    "pahalgam":           (34.0161, 75.3150, "in"),
    "sonamarg":           (34.2990, 75.2888, "in"),
    "jammu":              (32.7266, 74.8570, "in"),
    "leh":                (34.1526, 77.5771, "in"),
    "nubra valley":       (34.7164, 77.5760, "in"),
    "pangong lake":       (33.7542, 78.6842, "in"),
    "zanskar":            (33.5238, 76.8590, "in"),
    "kargil":             (34.5551, 76.1349, "in"),
    # ── Sikkim ─────────────────────────────────────────────────────────────
    "gangtok":            (27.3389, 88.6065, "in"),
    "pelling":            (27.2944, 88.1056, "in"),
    "namchi":             (27.1650, 88.3556, "in"),
    "yuksom":             (27.3624, 88.2344, "in"),
    "ravangla":           (27.2938, 88.3530, "in"),
    "nathu la":           (27.3873, 88.8294, "in"),
    # ── Meghalaya ──────────────────────────────────────────────────────────
    "shillong":           (25.5788, 91.8933, "in"),
    "cherrapunji":        (25.2700, 91.7326, "in"),
    "mawlynnong":         (25.2023, 91.9170, "in"),
    "dawki":              (25.1988, 92.0148, "in"),
    "tura":               (25.5123, 90.2206, "in"),
    # ── Assam ──────────────────────────────────────────────────────────────
    "guwahati":           (26.1445, 91.7362, "in"),
    "kaziranga":          (26.5775, 93.1709, "in"),
    "majuli":             (27.0000, 94.2000, "in"),
    "tezpur":             (26.6329, 92.8004, "in"),
    "sivasagar":          (26.9817, 94.6384, "in"),
    # ── Odisha ─────────────────────────────────────────────────────────────
    "bhubaneswar":        (20.2961, 85.8245, "in"),
    "puri":               (19.8135, 85.8312, "in"),
    "konark":             (19.8876, 86.0947, "in"),
    "cuttack":            (20.4625, 85.8830, "in"),
    "berhampur":          (19.3149, 84.7941, "in"),
    # ── Bihar ──────────────────────────────────────────────────────────────
    "patna":              (25.5941, 85.1376, "in"),
    "gaya":               (24.7955, 85.0002, "in"),
    "bodh gaya":          (24.6961, 84.9913, "in"),
    "nalanda":            (25.1362, 85.4439, "in"),
    "rajgir":             (25.0261, 85.4194, "in"),
    # ── Jharkhand ──────────────────────────────────────────────────────────
    "ranchi":             (23.3441, 85.3096, "in"),
    "jamshedpur":         (22.8046, 86.2029, "in"),
    "dhanbad":            (23.7957, 86.4304, "in"),
    "deoghar":            (24.4839, 86.6913, "in"),
    "netarhat":           (23.4794, 84.2681, "in"),
    "bokaro":             (23.6693, 86.1511, "in"),
    # ── Chhattisgarh ───────────────────────────────────────────────────────
    "raipur":             (21.2514, 81.6296, "in"),
    "jagdalpur":          (19.0708, 82.0130, "in"),
    "bilaspur":           (22.0797, 82.1391, "in"),
    # ── NE states ──────────────────────────────────────────────────────────
    "imphal":             (24.8170, 93.9368, "in"),
    "kohima":             (25.6751, 94.1086, "in"),
    "dimapur":            (25.9014, 93.7210, "in"),
    "mokokchung":         (26.3267, 94.5140, "in"),
    "aizawl":             (23.7271, 92.7176, "in"),
    "lunglei":            (22.8750, 92.7332, "in"),
    "champhai":           (23.4615, 93.3283, "in"),
    "itanagar":           (27.0844, 93.6053, "in"),
    "tawang":             (27.5859, 91.8643, "in"),
    "ziro":               (27.5498, 93.8237, "in"),
    "bomdila":            (27.2650, 92.4209, "in"),
    "pasighat":           (28.0655, 95.3251, "in"),
    "agartala":           (23.8315, 91.2868, "in"),
    # ── Delhi / NCR ────────────────────────────────────────────────────────
    "delhi":              (28.6139, 77.2090, "in"),
    "new delhi":          (28.6139, 77.2090, "in"),
    "old delhi":          (28.6580, 77.2280, "in"),
    "gurugram":           (28.4595, 77.0266, "in"),
    "gurgaon":            (28.4595, 77.0266, "in"),
    "noida":              (28.5355, 77.3910, "in"),
    "faridabad":          (28.4089, 77.3178, "in"),
    "rohini":             (28.7395, 77.1139, "in"),
    # ── Haryana ────────────────────────────────────────────────────────────
    "kurukshetra":        (29.9695, 76.8783, "in"),
    "panipat":            (29.3909, 76.9635, "in"),
    "ambala":             (30.3782, 76.7767, "in"),
    # ── Union Territories ──────────────────────────────────────────────────
    "port blair":         (11.6234, 92.7265, "in"),
    "havelock island":    (12.0179, 92.9753, "in"),
    "neil island":        (11.8283, 92.9217, "in"),
    "kavaratti":          (10.5593, 72.6358, "in"),
    "agatti":             (10.9091, 72.2021, "in"),
    "minicoy":            ( 8.2786, 73.0408, "in"),
    "daman":              (20.3974, 72.8328, "in"),
    "diu":                (20.7151, 70.9866, "in"),
    "silvassa":           (20.2766, 73.0069, "in"),
    "baratang":           (12.0020, 92.7690, "in"),
    "diglipur":           (13.0693, 92.9753, "in"),
    # ── International — Europe ─────────────────────────────────────────────
    "paris":              (48.8566,   2.3522, "fr"),
    "london":             (51.5074,  -0.1278, "gb"),
    "rome":               (41.9028,  12.4964, "it"),
    "milan":              (45.4654,   9.1859, "it"),
    "venice":             (45.4408,  12.3155, "it"),
    "florence":           (43.7696,  11.2558, "it"),
    "barcelona":          (41.3851,   2.1734, "es"),
    "madrid":             (40.4168,  -3.7038, "es"),
    "amsterdam":          (52.3676,   4.9041, "nl"),
    "berlin":             (52.5200,  13.4050, "de"),
    "munich":             (48.1351,  11.5820, "de"),
    "vienna":             (48.2082,  16.3738, "at"),
    "prague":             (50.0755,  14.4378, "cz"),
    "budapest":           (47.4979,  19.0402, "hu"),
    "warsaw":             (52.2297,  21.0122, "pl"),
    "zurich":             (47.3769,   8.5417, "ch"),
    "stockholm":          (59.3293,  18.0686, "se"),
    "oslo":               (59.9139,  10.7522, "no"),
    "copenhagen":         (55.6761,  12.5683, "dk"),
    "helsinki":           (60.1699,  24.9384, "fi"),
    "dublin":             (53.3498,  -6.2603, "ie"),
    "brussels":           (50.8503,   4.3517, "be"),
    "lisbon":             (38.7169,  -9.1399, "pt"),
    "athens":             (37.9838,  23.7275, "gr"),
    "istanbul":           (41.0082,  28.9784, "tr"),
    "moscow":             (55.7558,  37.6173, "ru"),
    "st. petersburg":     (59.9311,  30.3609, "ru"),
    "edinburgh":          (55.9533,  -3.1883, "gb"),
    # ── International — Middle East / Africa ───────────────────────────────
    "dubai":              (25.2048,  55.2708, "ae"),
    "abu dhabi":          (24.4539,  54.3773, "ae"),
    "doha":               (25.2854,  51.5310, "qa"),
    "riyadh":             (24.6877,  46.7219, "sa"),
    "muscat":             (23.5880,  58.3829, "om"),
    "cairo":              (30.0444,  31.2357, "eg"),
    "cape town":          (-33.9249,  18.4241, "za"),
    "johannesburg":       (-26.2041,  28.0473, "za"),
    "nairobi":            ( -1.2921,  36.8219, "ke"),
    "marrakech":          ( 31.6295,  -7.9811, "ma"),
    "tel aviv":           ( 32.0853,  34.7818, "il"),
    "jerusalem":          ( 31.7683,  35.2137, "il"),
    "tehran":             ( 35.6892,  51.3890, "ir"),
    # ── International — Asia ───────────────────────────────────────────────
    "tokyo":              (35.6762, 139.6503, "jp"),
    "osaka":              (34.6937, 135.5023, "jp"),
    "kyoto":              (35.0116, 135.7681, "jp"),
    "beijing":            (39.9042, 116.4074, "cn"),
    "shanghai":           (31.2304, 121.4737, "cn"),
    "hong kong":          (22.3193, 114.1694, "hk"),
    "taipei":             (25.0330, 121.5654, "tw"),
    "seoul":              (37.5665, 126.9780, "kr"),
    "singapore":          ( 1.3521, 103.8198, "sg"),
    "bangkok":            (13.7563, 100.5018, "th"),
    "phuket":             ( 7.8804,  98.3923, "th"),
    "chiang mai":         (18.7883,  98.9853, "th"),
    "bali":               (-8.4095, 115.1889, "id"),
    "denpasar":           (-8.6705, 115.2126, "id"),
    "jakarta":            (-6.2088, 106.8456, "id"),
    "kuala lumpur":       ( 3.1390, 101.6869, "my"),
    "penang":             ( 5.4164, 100.3327, "my"),
    "manila":             (14.5995, 120.9842, "ph"),
    "colombo":            ( 6.9271,  79.8612, "lk"),
    "kathmandu":          (27.7172,  85.3240, "np"),
    "pokhara":            (28.2096,  83.9856, "np"),
    "thimphu":            (27.4728,  89.6390, "bt"),
    "dhaka":              (23.8103,  90.4125, "bd"),
    "islamabad":          (33.6844,  73.0479, "pk"),
    "lahore":             (31.5204,  74.3587, "pk"),
    "karachi":            (24.8607,  67.0011, "pk"),
    "male":               ( 4.1755,  73.5093, "mv"),
    "yangon":             (16.8661,  96.1951, "mm"),
    "hanoi":              (21.0278, 105.8342, "vn"),
    "ho chi minh city":   (10.8231, 106.6297, "vn"),
    "phnom penh":         (11.5564, 104.9282, "kh"),
    "siem reap":          (13.3671, 103.8448, "kh"),
    "ulaanbaatar":        (47.8864, 106.9057, "mn"),
    # ── International — Americas ───────────────────────────────────────────
    "new york":           (40.7128, -74.0060, "us"),
    "los angeles":        (34.0522,-118.2437, "us"),
    "chicago":            (41.8781, -87.6298, "us"),
    "miami":              (25.7617, -80.1918, "us"),
    "san francisco":      (37.7749,-122.4194, "us"),
    "las vegas":          (36.1699,-115.1398, "us"),
    "washington dc":      (38.9072, -77.0369, "us"),
    "washington":         (38.9072, -77.0369, "us"),
    "boston":             (42.3601, -71.0589, "us"),
    "toronto":            (43.6532, -79.3832, "ca"),
    "vancouver":          (49.2827,-123.1207, "ca"),
    "montreal":           (45.5017, -73.5673, "ca"),
    "mexico city":        (19.4326, -99.1332, "mx"),
    "buenos aires":       (-34.6037,-58.3816, "ar"),
    "sao paulo":          (-23.5505,-46.6333, "br"),
    "rio de janeiro":     (-22.9068,-43.1729, "br"),
    "lima":               (-12.0464,-77.0428, "pe"),
    # ── International — Oceania ────────────────────────────────────────────
    "sydney":             (-33.8688, 151.2093, "au"),
    "melbourne":          (-37.8136, 144.9631, "au"),
    "brisbane":           (-27.4698, 153.0251, "au"),
    "perth":              (-31.9505, 115.8605, "au"),
    "auckland":           (-36.8485, 174.7633, "nz"),
    "christchurch":       (-43.5320, 172.6306, "nz"),
    "queenstown":         (-45.0312, 168.6626, "nz"),
}
# fmt: on


class StaticTableGeocoder(GeocodingProvider):
    """
    Zero-network fallback using a hard-coded city table.
    Always available — no API key, no network, no rate limit.
    """

    name = "static"

    def geocode(self, city: str) -> Optional[GeocodingResult]:
        key = city.lower().strip()
        entry = _STATIC_COORDS.get(key)
        if entry is None:
            return None
        lat, lng, cc = entry
        return GeocodingResult(lat=lat, lng=lng, display_name=city,
                               country_code=cc, provider=self.name)


# ---------------------------------------------------------------------------
# Geocoding Manager — main public interface
# ---------------------------------------------------------------------------

class GeocodingManager:
    """
    Multi-provider geocoding manager for ExploreX.

    Tries providers in priority order and returns the first successful result.
    Caches successful results in-memory to avoid redundant calls and to respect
    Nominatim's 1 req/s rate limit.

    Priority:
        1. Google Maps  (existing, billing-dependent)
        2. Geoapify     (free 3 000 req/day — needs GEOAPIFY_API_KEY)
        3. Nominatim    (free OSM public server — on by default)
        4. Photon       (OSM-based — opt-in PHOTON_ENABLED=true)
        5. Static table (always last, always available)
    """

    def __init__(self):
        self._cache: dict[str, tuple[GeocodingResult, float]] = {}
        self._ttl = int(os.getenv("GEOCODER_CACHE_TTL", "3600"))

        self._providers: list[GeocodingProvider] = []

        # 2. Geoapify
        if os.getenv("GEOAPIFY_API_KEY", ""):
            self._providers.append(GeoapifyGeocoder())
            print("[GEOCODER] Geoapify provider enabled")
        else:
            print("[GEOCODER] Geoapify disabled (GEOAPIFY_API_KEY not set) — "
                  "set it for a free additional fallback")

        # 3. Nominatim
        if os.getenv("NOMINATIM_ENABLED", "true").lower() != "false":
            self._providers.append(NominatimGeocoder())
            print("[GEOCODER] Nominatim provider enabled")
        else:
            print("[GEOCODER] Nominatim disabled (NOMINATIM_ENABLED=false)")

        # 4. Photon (opt-in)
        if os.getenv("PHOTON_ENABLED", "false").lower() == "true":
            self._providers.append(PhotonGeocoder())
            print("[GEOCODER] Photon provider enabled")

        # 5. Static table — always last
        self._providers.append(StaticTableGeocoder())
        print(f"[GEOCODER] Provider cascade: {[p.name for p in self._providers]}")

    # ─────────────────────────────────────────────────────────────────────

    def geocode(self, city: str) -> Optional[GeocodingResult]:
        """
        Geocode a city name using the provider cascade.

        Returns the first successful GeocodingResult, or None if every provider
        fails (including the static table).  Caches successful results.
        """
        if not city or not city.strip():
            return None

        key = city.lower().strip()
        cached = self._from_cache(key)
        if cached is not None:
            cached.cached = True
            print(f"[GEOCODER] Cache hit: '{city}' → provider={cached.provider}")
            return cached

        for provider in self._providers:
            try:
                result = provider.geocode(city)
                if result is not None:
                    print(f"[GEOCODER] '{city}' → {provider.name}: "
                          f"lat={result.lat:.4f}, lng={result.lng:.4f}, "
                          f"cc='{result.country_code}'")
                    self._to_cache(key, result)
                    return result
                # else: provider returned None, try next
                print(f"[GEOCODER] {provider.name}: no result for '{city}'")
            except Exception as e:
                logger.warning(f"[GEOCODER] {provider.name} exception for '{city}': "
                               f"{type(e).__name__}: {e}")

        print(f"[GEOCODER] All providers failed for '{city}' — returning None")
        return None

    def is_in_india(self, result: Optional[GeocodingResult]) -> bool:
        """Convenience: check whether a result is within India."""
        return result is not None and result.is_in_india()

    # ── Cache helpers ──────────────────────────────────────────────────────

    def _from_cache(self, key: str) -> Optional[GeocodingResult]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        result, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._cache[key]
            return None
        return copy.copy(result)

    def _to_cache(self, key: str, result: GeocodingResult):
        self._cache[key] = (copy.copy(result), time.monotonic())
