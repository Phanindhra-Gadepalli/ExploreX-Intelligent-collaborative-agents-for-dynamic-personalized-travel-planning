"""Destination scope resolution — countries, regions, cities, multi-city trips.

The October Japan run geocoded the *country* "Japan" to a single point near
Numata and then searched a 20 km circle around it.  That is not a country trip,
and the application never told the traveller that its scope had silently
collapsed to one locality.

This module classifies a destination before any POI search:

* ``country``   — e.g. Japan → a transparent, editable multi-city proposal
* ``region``    — e.g. Hokkaido → its verified cities
* ``city``      — e.g. Tokyo → exactly Tokyo (the user's choice is preserved)
* ``multi_city``— e.g. "Tokyo, Kyoto, Osaka" → all three, no silent narrowing
* ``unknown``   — no verified scope → caller must ask for clarification

The static city/region tables are a *reference index*, not a fabricated POI
list.  They contain only well-known administrative cities and their coordinates
so a country-level request can start with a real, disclosed scope.  When a
destination is not in the index, the existing geocoder is consulted and its
feature type is used; a bare coordinate is never treated as a country.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
# lat/lng are approximate city-centre coordinates (well-known public geography),
# used only as search centres.  No attraction, rating, price or availability is
# stored here.
def _city(name: str, region: str, lat: float, lng: float) -> Dict[str, Any]:
    return {"name": name, "region": region, "lat": lat, "lng": lng,
            "source": "static_reference"}


COUNTRIES: Dict[str, Dict[str, Any]] = {
    "japan": {
        "display": "Japan", "code": "jp", "bbox": (24.0, 46.0, 122.0, 146.0),
        "cities": [
            _city("Tokyo", "Kanto", 35.6762, 139.6503),
            _city("Kyoto", "Kansai", 35.0116, 135.7681),
            _city("Osaka", "Kansai", 34.6937, 135.5023),
            _city("Sapporo", "Hokkaido", 43.0618, 141.3545),
            _city("Hakone", "Kanto", 35.2324, 139.1069),
            _city("Nara", "Kansai", 34.6851, 135.8048),
        ],
    },
    "france": {
        "display": "France", "code": "fr", "bbox": (41.0, 51.5, -5.5, 9.8),
        "cities": [
            _city("Paris", "Ile-de-France", 48.8566, 2.3522),
            _city("Lyon", "Auvergne-Rhone-Alpes", 45.7640, 4.8357),
            _city("Nice", "Provence-Alpes-Cote d'Azur", 43.7102, 7.2620),
        ],
    },
    "italy": {
        "display": "Italy", "code": "it", "bbox": (35.0, 47.5, 6.6, 18.8),
        "cities": [
            _city("Rome", "Lazio", 41.9028, 12.4964),
            _city("Florence", "Tuscany", 43.7696, 11.2558),
            _city("Venice", "Veneto", 45.4408, 12.3155),
            _city("Milan", "Lombardy", 45.4654, 9.1859),
        ],
    },
    "spain": {
        "display": "Spain", "code": "es", "bbox": (35.0, 44.0, -9.5, 4.5),
        "cities": [
            _city("Madrid", "Community of Madrid", 40.4168, -3.7038),
            _city("Barcelona", "Catalonia", 41.3851, 2.1734),
            _city("Seville", "Andalusia", 37.3891, -5.9845),
        ],
    },
    "united kingdom": {
        "display": "United Kingdom", "code": "gb", "bbox": (49.5, 61.0, -8.5, 2.0),
        "cities": [
            _city("London", "England", 51.5074, -0.1278),
            _city("Edinburgh", "Scotland", 55.9533, -3.1883),
            _city("Manchester", "England", 53.4808, -2.2426),
        ],
    },
    "usa": {
        "display": "United States", "code": "us", "bbox": (24.0, 49.5, -125.0, -66.0),
        "cities": [
            _city("New York", "New York", 40.7128, -74.0060),
            _city("Los Angeles", "California", 34.0522, -118.2437),
            _city("San Francisco", "California", 37.7749, -122.4194),
            _city("Chicago", "Illinois", 41.8781, -87.6298),
        ],
    },
    "uae": {
        "display": "United Arab Emirates", "code": "ae", "bbox": (22.5, 26.5, 51.0, 56.5),
        "cities": [
            _city("Dubai", "Dubai", 25.2048, 55.2708),
            _city("Abu Dhabi", "Abu Dhabi", 24.4539, 54.3773),
        ],
    },
    "thailand": {
        "display": "Thailand", "code": "th", "bbox": (5.5, 20.5, 97.0, 106.0),
        "cities": [
            _city("Bangkok", "Bangkok", 13.7563, 100.5018),
            _city("Chiang Mai", "Chiang Mai", 18.7883, 98.9853),
            _city("Phuket", "Phuket", 7.8804, 98.3923),
        ],
    },
    "singapore": {
        "display": "Singapore", "code": "sg", "bbox": (1.1, 1.5, 103.6, 104.1),
        "cities": [_city("Singapore", "Singapore", 1.3521, 103.8198)],
    },
    "indonesia": {
        "display": "Indonesia", "code": "id", "bbox": (-11.0, 6.0, 95.0, 141.0),
        "cities": [
            _city("Denpasar", "Bali", -8.6705, 115.2126),
            _city("Jakarta", "Jakarta", -6.2088, 106.8456),
            _city("Yogyakarta", "Yogyakarta", -7.7956, 110.3695),
        ],
    },
    "south korea": {
        "display": "South Korea", "code": "kr", "bbox": (33.0, 38.7, 124.0, 132.0),
        "cities": [
            _city("Seoul", "Seoul", 37.5665, 126.9780),
            _city("Busan", "Busan", 35.1796, 129.0756),
            _city("Gyeongju", "Gyeongsangbuk-do", 35.8562, 129.2247),
        ],
    },
    "australia": {
        "display": "Australia", "code": "au", "bbox": (-44.0, -10.0, 112.0, 154.0),
        "cities": [
            _city("Sydney", "New South Wales", -33.8688, 151.2093),
            _city("Melbourne", "Victoria", -37.8136, 144.9631),
            _city("Brisbane", "Queensland", -27.4698, 153.0251),
        ],
    },
    "switzerland": {
        "display": "Switzerland", "code": "ch", "bbox": (45.8, 47.9, 5.9, 10.5),
        "cities": [
            _city("Zurich", "Zurich", 47.3769, 8.5417),
            _city("Lucerne", "Lucerne", 47.0502, 8.3093),
            _city("Interlaken", "Bern", 46.6863, 7.8632),
        ],
    },
    "germany": {
        "display": "Germany", "code": "de", "bbox": (47.0, 55.0, 5.8, 15.1),
        "cities": [
            _city("Berlin", "Berlin", 52.5200, 13.4050),
            _city("Munich", "Bavaria", 48.1351, 11.5820),
            _city("Cologne", "North Rhine-Westphalia", 50.9375, 6.9603),
        ],
    },
    "netherlands": {
        "display": "Netherlands", "code": "nl", "bbox": (50.7, 53.6, 3.3, 7.3),
        "cities": [
            _city("Amsterdam", "North Holland", 52.3676, 4.9041),
            _city("Rotterdam", "South Holland", 51.9244, 4.4777),
        ],
    },
    "portugal": {
        "display": "Portugal", "code": "pt", "bbox": (36.9, 42.2, -9.6, -6.2),
        "cities": [
            _city("Lisbon", "Lisbon", 38.7169, -9.1399),
            _city("Porto", "Porto", 41.1579, -8.6291),
        ],
    },
    "greece": {
        "display": "Greece", "code": "gr", "bbox": (34.5, 41.8, 19.0, 29.7),
        "cities": [
            _city("Athens", "Attica", 37.9838, 23.7275),
            _city("Thessaloniki", "Central Macedonia", 40.6401, 22.9444),
        ],
    },
    "turkey": {
        "display": "Turkey", "code": "tr", "bbox": (35.8, 42.2, 25.6, 44.8),
        "cities": [
            _city("Istanbul", "Istanbul", 41.0082, 28.9784),
            _city("Cappadocia", "Nevsehir", 38.6431, 34.8289),
        ],
    },
    "egypt": {
        "display": "Egypt", "code": "eg", "bbox": (22.0, 31.7, 24.7, 36.9),
        "cities": [
            _city("Cairo", "Cairo", 30.0444, 31.2357),
            _city("Luxor", "Luxor", 25.6872, 32.6396),
        ],
    },
    "malaysia": {
        "display": "Malaysia", "code": "my", "bbox": (0.8, 7.4, 99.6, 119.3),
        "cities": [
            _city("Kuala Lumpur", "Kuala Lumpur", 3.1390, 101.6869),
            _city("Penang", "Penang", 5.4164, 100.3327),
        ],
    },
    "vietnam": {
        "display": "Vietnam", "code": "vn", "bbox": (8.5, 23.4, 102.0, 109.5),
        "cities": [
            _city("Hanoi", "Hanoi", 21.0278, 105.8342),
            _city("Ho Chi Minh City", "Ho Chi Minh", 10.8231, 106.6297),
            _city("Da Nang", "Da Nang", 16.0544, 108.2022),
        ],
    },
    "sri lanka": {
        "display": "Sri Lanka", "code": "lk", "bbox": (5.9, 9.9, 79.6, 81.9),
        "cities": [
            _city("Colombo", "Western", 6.9271, 79.8612),
            _city("Kandy", "Central", 7.2906, 80.6337),
        ],
    },
    "nepal": {
        "display": "Nepal", "code": "np", "bbox": (26.3, 30.5, 80.0, 88.3),
        "cities": [
            _city("Kathmandu", "Bagmati", 27.7172, 85.3240),
            _city("Pokhara", "Gandaki", 28.2096, 83.9856),
        ],
    },
    "canada": {
        "display": "Canada", "code": "ca", "bbox": (41.0, 62.0, -141.0, -52.0),
        "cities": [
            _city("Toronto", "Ontario", 43.6532, -79.3832),
            _city("Vancouver", "British Columbia", 49.2827, -123.1207),
            _city("Montreal", "Quebec", 45.5017, -73.5673),
        ],
    },
    "new zealand": {
        "display": "New Zealand", "code": "nz", "bbox": (-47.5, -34.0, 166.0, 179.0),
        "cities": [
            _city("Auckland", "Auckland", -36.8485, 174.7633),
            _city("Queenstown", "Otago", -45.0312, 168.6626),
        ],
    },
}

# Alternate spellings / common short forms.
COUNTRY_ALIASES: Dict[str, str] = {
    "jpn": "japan", "jp": "japan",
    "uk": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
    "england": "united kingdom", "scotland": "united kingdom",
    "united states": "usa", "united states of america": "usa", "us": "usa", "america": "usa",
    "uae": "uae", "emirates": "uae", "united arab emirates": "uae",
    "korea": "south korea", "republic of korea": "south korea",
    "holland": "netherlands",
}

REGIONS: Dict[str, Dict[str, Any]] = {
    # Japan is the reference implementation for the logged failure, but the
    # mechanism is generic: any region with verified cities can be added.
    "hokkaido": {
        "display": "Hokkaido", "country": "Japan", "code": "jp",
        "cities": [
            _city("Sapporo", "Hokkaido", 43.0618, 141.3545),
            _city("Otaru", "Hokkaido", 43.1907, 140.9947),
            _city("Hakodate", "Hokkaido", 41.7687, 140.7288),
            _city("Asahikawa", "Hokkaido", 43.7708, 142.3650),
        ],
    },
    "kanto": {
        "display": "Kanto", "country": "Japan", "code": "jp",
        "cities": [
            _city("Tokyo", "Kanto", 35.6762, 139.6503),
            _city("Nikko", "Tochigi", 36.7199, 139.6982),
            _city("Hakone", "Kanagawa", 35.2324, 139.1069),
            _city("Yokohama", "Kanagawa", 35.4437, 139.6380),
        ],
    },
    "kansai": {
        "display": "Kansai", "country": "Japan", "code": "jp",
        "cities": [
            _city("Kyoto", "Kyoto", 35.0116, 135.7681),
            _city("Osaka", "Osaka", 34.6937, 135.5023),
            _city("Nara", "Nara", 34.6851, 135.8048),
            _city("Kobe", "Hyogo", 34.6901, 135.1955),
        ],
    },
    "chubu": {
        "display": "Chubu", "country": "Japan", "code": "jp",
        "cities": [
            _city("Nagoya", "Aichi", 35.1815, 136.9066),
            _city("Takayama", "Gifu", 36.1461, 137.2522),
            _city("Kanazawa", "Ishikawa", 36.5613, 136.6562),
            _city("Matsumoto", "Nagano", 36.2380, 137.9720),
        ],
    },
    "kyushu": {
        "display": "Kyushu", "country": "Japan", "code": "jp",
        "cities": [
            _city("Fukuoka", "Fukuoka", 33.5904, 130.4017),
            _city("Nagasaki", "Nagasaki", 32.7503, 129.8777),
            _city("Kagoshima", "Kagoshima", 31.5966, 130.5571),
            _city("Beppu", "Oita", 33.2846, 131.4912),
        ],
    },
    "tohoku": {
        "display": "Tohoku", "country": "Japan", "code": "jp",
        "cities": [
            _city("Sendai", "Miyagi", 38.2682, 140.8694),
            _city("Aomori", "Aomori", 40.8222, 140.7474),
        ],
    },
    "okinawa": {
        "display": "Okinawa", "country": "Japan", "code": "jp",
        "cities": [
            _city("Naha", "Okinawa", 26.2124, 127.6809),
            _city("Ishigaki", "Okinawa", 24.3448, 124.1572),
        ],
    },
    "shikoku": {
        "display": "Shikoku", "country": "Japan", "code": "jp",
        "cities": [
            _city("Matsuyama", "Ehime", 33.8392, 132.7657),
            _city("Takamatsu", "Kagawa", 34.3401, 134.0434),
        ],
    },
}

REGION_ALIASES: Dict[str, str] = {
    "hokkaido prefecture": "hokkaido",
    "kansai region": "kansai", "kanto region": "kanto",
    "kyushu region": "kyushu", "okinawa prefecture": "okinawa",
}

_POI_WORDS = {
    "temple", "shrine", "museum", "park", "tower", "castle", "station", "street",
    "market", "garden", "viewpoint", "observation", "deck", "bridge", "mountain",
    "lake", "waterfall", "beach", "island", "airport", "hotel",
}

_COUNTRY_FEATURE_TYPES = {"country", "nation", "country_region"}
_REGION_FEATURE_TYPES = {"state", "region", "province", "prefecture", "county", "administrative"}
_CITY_FEATURE_TYPES = {
    "city", "town", "village", "municipality", "locality", "hamlet", "borough",
    "suburb", "district", "place", "address", "house", "poi", "amenity",
}


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------
@dataclass
class ResolvedDestination:
    raw: str
    kind: str = "unknown"                 # country | region | city | multi_city | unknown | invalid
    display_name: str = ""
    country: str = ""
    country_code: str = ""
    is_international: bool = True
    cities: List[Dict[str, Any]] = field(default_factory=list)
    scope_status: str = "needs_clarification"  # resolved | proposed | needs_clarification | invalid
    lat: Optional[float] = None
    lng: Optional[float] = None
    provider: str = "static"
    confidence: float = 0.0
    notes: List[str] = field(default_factory=list)

    @property
    def is_country_scope(self) -> bool:
        return self.kind == "country"

    def needs_clarification(self) -> bool:
        return self.scope_status in ("needs_clarification", "invalid")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "kind": self.kind,
            "display_name": self.display_name or self.raw,
            "country": self.country,
            "country_code": self.country_code,
            "is_international": self.is_international,
            "cities": [dict(c) for c in self.cities],
            "scope_status": self.scope_status,
            "lat": self.lat,
            "lng": self.lng,
            "provider": self.provider,
            "confidence": self.confidence,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    text = _clean(value).lower()
    text = re.sub(r"[.']", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _country_key(value: str) -> Optional[str]:
    key = _key(value)
    if key in COUNTRIES:
        return key
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    return None


def _region_key(value: str) -> Optional[str]:
    key = _key(value)
    if key in REGIONS:
        return key
    return REGION_ALIASES.get(key)


def _build_city_index() -> Dict[str, Tuple[str, str, Dict[str, Any]]]:
    index: Dict[str, Tuple[str, str, Dict[str, Any]]] = {}
    for country_key, data in COUNTRIES.items():
        for city in data["cities"]:
            index[_key(city["name"])] = (country_key, data["display"], city)
    for region_key, data in REGIONS.items():
        for city in data["cities"]:
            pair = (_country_key(data["country"]) or "japan", data["country"], city)
            index.setdefault(_key(city["name"]), pair)
    # Common English/Japanese aliases for reference cities.
    aliases = {
        "tokio": "tokyo", "sapporo city": "sapporo", "kyoto city": "kyoto",
        "osaka city": "osaka", "tokyo metropolis": "tokyo",
        "nyc": "new york", "new york city": "new york",
        "sf": "san francisco", "la": "los angeles",
        "rome city": "rome", "firenze": "florence", "venezia": "venice",
        "munchen": "munich", "koln": "cologne", "lisboa": "lisbon",
    }
    for alias, target in aliases.items():
        if target in index:
            index[alias] = index[target]
    return index


CITY_INDEX = _build_city_index()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (deterministic)."""
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return float("inf")
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return radius * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def within_country(lat: float, lng: float, country_key: str) -> bool:
    data = COUNTRIES.get(country_key)
    if not data:
        return True
    lat_min, lat_max, lng_min, lng_max = data["bbox"]
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def validate_point_for_scope(lat: float, lng: float, resolved: ResolvedDestination,
                             max_km: float = 120.0) -> bool:
    """True when a POI is inside the country and near one of the scope cities.

    A country is never validated by its bounding box alone: a point in the
    middle of Japan is not part of a Tokyo-only trip.
    """
    if lat is None or lng is None:
        return False
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if resolved.country_code:
        country_key = _country_key(resolved.country) or _country_key(resolved.display_name)
        if country_key and not within_country(lat, lng, country_key):
            return False
    if not resolved.cities:
        return True
    nearest = min(
        haversine_km(lat, lng, c.get("lat"), c.get("lng")) for c in resolved.cities
    )
    # Multi-city country scopes search each city with a 20-25 km radius; 120 km
    # allows genuine day-trip sights without accepting another prefecture.
    return nearest <= max_km


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def resolve_destination(name: Any, geocoder: Any = None) -> ResolvedDestination:
    """Classify ``name`` and return a transparent travel scope.

    The function never guesses a single locality for a country.  It may return
    a *proposal* (``scope_status="proposed"``) which the caller must disclose
    and allow the traveller to edit.
    """
    raw = _clean(name)
    if not raw:
        return ResolvedDestination(raw="", kind="invalid", display_name="",
                                   scope_status="invalid",
                                   notes=["No destination was provided."])
    return _resolve(raw, geocoder, depth=0)


def _resolve(raw: str, geocoder: Any, depth: int) -> ResolvedDestination:
    if depth > 3:
        return ResolvedDestination(raw=raw, kind="unknown", display_name=raw,
                                   scope_status="needs_clarification")

    # 1. Direct country / alias.
    country_key = _country_key(raw)
    if country_key:
        return _country_result(raw, country_key)

    # 2. Direct region / prefecture.
    region_key = _region_key(raw)
    if region_key:
        return _region_result(raw, region_key)

    # 3. Direct city.
    city_hit = CITY_INDEX.get(_key(raw))
    if city_hit:
        return _city_result(raw, *city_hit)

    # 4. Multi-city string ("Tokyo, Kyoto, Osaka").
    parts = _split_parts(raw)
    if len(parts) > 1:
        multi = _try_multi_city(raw, parts, geocoder, depth)
        if multi is not None:
            return multi

    # 5. Geocoder feature type (never a bare centroid for a country).
    return _resolve_with_geocoder(raw, geocoder)


def _split_parts(raw: str) -> List[str]:
    # Keep comma-separated administrative context ("Tokyo, Japan") together.
    parts = [p.strip() for p in re.split(r"\s*(?:,|;|\+|/|\band\b|→|->)\s*", raw) if p.strip()]
    return parts


def _try_multi_city(raw: str, parts: Sequence[str], geocoder: Any,
                    depth: int) -> Optional[ResolvedDestination]:
    # "Tokyo, Japan" is a city plus its country, not a two-city trip.
    if len(parts) == 2:
        first, second = parts[0], parts[1]
        second_country = _country_key(second)
        first_city = CITY_INDEX.get(_key(first))
        if second_country and first_city and first_city[0] == second_country:
            resolved = _city_result(first, *first_city)
            resolved.raw = raw
            resolved.display_name = f"{first_city[2]['name']}, {COUNTRIES[second_country]['display']}"
            return resolved

    cities: List[Dict[str, Any]] = []
    country_key = None
    country_name = ""
    for part in parts:
        ck = _country_key(part)
        if ck:
            country_key = country_key or ck
            country_name = country_name or COUNTRIES[ck]["display"]
            continue
        region_key = _region_key(part)
        if region_key:
            for c in REGIONS[region_key]["cities"]:
                cities.append(dict(c))
            country_name = country_name or REGIONS[region_key]["country"]
            country_key = country_key or _country_key(country_name)
            continue
        hit = CITY_INDEX.get(_key(part))
        if hit:
            ck, cname, city = hit
            cities.append(dict(city))
            country_key = country_key or ck
            country_name = country_name or cname
            continue
        # Unknown part: consult the geocoder only if we have one, and only
        # accept a city-like result (not a country centroid).
        if geocoder is not None:
            geo = _safe_geocode(geocoder, part)
            if geo and _feature_kind(geo) in ("city", "region"):
                if _feature_kind(geo) == "city":
                    city = _city_from_geo(part, geo)
                    cities.append(city)
                    country_key = country_key or _country_key(geo.country)
                    country_name = country_name or geo.country
                    continue
        return None

    # De-duplicate cities by name, preserving the user's order.
    deduped = []
    seen = set()
    for city in cities:
        key = _key(city.get("name"))
        if key and key not in seen:
            seen.add(key)
            deduped.append(city)
    if len(deduped) < 2:
        return None

    return ResolvedDestination(
        raw=raw, kind="multi_city",
        display_name=", ".join(c["name"] for c in deduped),
        country=country_name or (COUNTRIES[country_key]["display"] if country_key else ""),
        country_code=COUNTRIES[country_key]["code"] if country_key else "",
        is_international=(country_key != "india"),
        cities=deduped, scope_status="resolved",
        provider="static_multi_city", confidence=0.9,
        notes=[f"Kept all {len(deduped)} requested destinations: "
               + ", ".join(c["name"] for c in deduped)],
    )


def _country_result(raw: str, country_key: str) -> ResolvedDestination:
    data = COUNTRIES[country_key]
    cities = [dict(c) for c in data["cities"]]
    notes = [
        f"'{raw}' is a country, not a single search point.",
        "Proposed scope: " + ", ".join(c["name"] for c in cities) + ".",
        "The proposal is editable; it does not replace your requested country.",
    ]
    return ResolvedDestination(
        raw=raw, kind="country", display_name=data["display"],
        country=data["display"], country_code=data["code"],
        is_international=(data["code"] != "in"),
        cities=cities,
        scope_status="proposed" if cities else "needs_clarification",
        lat=sum(c["lat"] for c in cities) / len(cities) if cities else None,
        lng=sum(c["lng"] for c in cities) / len(cities) if cities else None,
        provider="static_country", confidence=0.9 if cities else 0.4,
        notes=notes,
    )


def _region_result(raw: str, region_key: str) -> ResolvedDestination:
    data = REGIONS[region_key]
    country_key = _country_key(data["country"])
    cities = [dict(c) for c in data["cities"]]
    return ResolvedDestination(
        raw=raw, kind="region", display_name=data["display"],
        country=data["country"],
        country_code=COUNTRIES[country_key]["code"] if country_key else data.get("code", ""),
        is_international=(country_key != "india"),
        cities=cities, scope_status="resolved" if cities else "needs_clarification",
        lat=sum(c["lat"] for c in cities) / len(cities) if cities else None,
        lng=sum(c["lng"] for c in cities) / len(cities) if cities else None,
        provider="static_region", confidence=0.9,
        notes=[f"'{raw}' is a region; searching its verified cities: "
               + ", ".join(c["name"] for c in cities)],
    )


def _city_result(raw: str, country_key: str, country_name: str,
                 city: Dict[str, Any]) -> ResolvedDestination:
    return ResolvedDestination(
        raw=raw, kind="city", display_name=city["name"],
        country=country_name,
        country_code=COUNTRIES.get(country_key, {}).get("code", ""),
        is_international=(country_key != "india"),
        cities=[dict(city)], scope_status="resolved",
        lat=city["lat"], lng=city["lng"],
        provider="static_city", confidence=0.95,
        notes=[f"Search scope is {city['name']} only."],
    )


def _safe_geocode(geocoder: Any, query: str):
    try:
        return geocoder.geocode(query)
    except Exception:
        return None


def _feature_kind(result: Any) -> str:
    ft = str(getattr(result, "feature_type", "") or "").lower()
    pt = str(getattr(result, "place_type", "") or "").lower()
    values = [v for v in (ft, pt) if v]
    if any(v in _COUNTRY_FEATURE_TYPES or v == "country" for v in values):
        return "country"
    if any(v in _REGION_FEATURE_TYPES for v in values):
        return "region"
    if any(v in _CITY_FEATURE_TYPES for v in values):
        return "city"
    if getattr(result, "city", ""):
        return "city"
    return "unknown"


def _city_from_geo(name: str, geo: Any) -> Dict[str, Any]:
    city_name = getattr(geo, "city", "") or name
    return {
        "name": city_name,
        "region": getattr(geo, "state", "") or "",
        "lat": float(getattr(geo, "lat")),
        "lng": float(getattr(geo, "lng")),
        "source": getattr(geo, "provider", "geocoder") or "geocoder",
    }


def _resolve_with_geocoder(raw: str, geocoder: Any) -> ResolvedDestination:
    geo = _safe_geocode(geocoder, raw) if geocoder is not None else None
    if geo is None:
        return ResolvedDestination(
            raw=raw, kind="unknown", display_name=raw,
            scope_status="needs_clarification",
            provider="none", confidence=0.0,
            notes=["No verified geographic record was found for this destination."],
        )

    kind = _feature_kind(geo)
    country_name = getattr(geo, "country", "") or ""
    country_code = str(getattr(geo, "country_code", "") or "").lower()
    country_key = _country_key(country_name) or next(
        (k for k, v in COUNTRIES.items() if v["code"] == country_code), None
    )

    if kind == "country":
        # A geocoder country centroid is NEVER a city scope.  If we have a
        # curated proposal, use it; otherwise ask for a region/city.
        if country_key:
            result = _country_result(raw, country_key)
            result.provider = getattr(geo, "provider", "geocoder") or "geocoder"
            return result
        return ResolvedDestination(
            raw=raw, kind="country", display_name=country_name or raw,
            country=country_name, country_code=country_code,
            is_international=(country_code != "in"),
            cities=[], scope_status="needs_clarification",
            lat=float(geo.lat), lng=float(geo.lng),
            provider=getattr(geo, "provider", "geocoder") or "geocoder",
            confidence=0.5,
            notes=[f"'{raw}' is a country. Please choose the region or cities you want to visit."],
        )

    if kind == "region":
        if country_key:
            # If the region's name is a curated region, use its cities.
            region_key = _region_key(raw) or _region_key(getattr(geo, "state", ""))
            if region_key:
                return _region_result(raw, region_key)
        city_name = getattr(geo, "city", "") or getattr(geo, "state", "") or raw
        city = _city_from_geo(city_name, geo)
        return ResolvedDestination(
            raw=raw, kind="region", display_name=raw,
            country=country_name, country_code=country_code,
            is_international=(country_code != "in"),
            cities=[city], scope_status="resolved",
            lat=float(geo.lat), lng=float(geo.lng),
            provider=getattr(geo, "provider", "geocoder") or "geocoder",
            confidence=0.6,
            notes=[f"Region resolved with {city['name']} as the initial search centre; "
                   "add more cities if you want a wider trip."],
        )

    city = _city_from_geo(raw, geo)
    # A result that looks like a POI/address inside a city still gives a valid
    # city scope, but we preserve the user's exact request as a POI centre.
    return ResolvedDestination(
        raw=raw, kind="city", display_name=city["name"] or raw,
        country=country_name, country_code=country_code,
        is_international=(country_code != "in"),
        cities=[city], scope_status="resolved",
        lat=float(geo.lat), lng=float(geo.lng),
        provider=getattr(geo, "provider", "geocoder") or "geocoder",
        confidence=0.75 if kind == "city" else 0.5,
        notes=[f"Search scope is {city['name'] or raw}."],
    )


def scope_city_names(resolved: ResolvedDestination) -> List[str]:
    return [c.get("name", "") for c in resolved.cities if c.get("name")]


def disclosure_text(resolved: ResolvedDestination) -> str:
    """Human-readable scope disclosure used in the chat transcript."""
    if resolved.scope_status == "invalid":
        return "Please tell me which destination you would like to visit."
    if resolved.scope_status == "needs_clarification":
        return (f"I can see **{resolved.display_name or resolved.raw}**, but I need a more specific "
                "travel scope before searching. Which region or cities would you like to visit?")
    if resolved.kind == "country":
        names = ", ".join(scope_city_names(resolved))
        return (f"🌍 **{resolved.display_name}** is a whole country, so I have not reduced it to "
                f"one town. I'm using a transparent, editable proposal: **{names}**. "
                "Tell me if you want different cities or fewer regions and I'll re-plan.")
    if resolved.kind == "multi_city":
        names = ", ".join(scope_city_names(resolved))
        return f"🗺️ Keeping all your requested destinations: **{names}**."
    if resolved.kind == "region":
        names = ", ".join(scope_city_names(resolved))
        return f"📍 Searching the **{resolved.display_name}** region across **{names}**."
    return f"📍 Search scope: **{resolved.display_name or resolved.raw}**."
