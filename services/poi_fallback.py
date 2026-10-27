import os
import random
import re
import requests
import threading
import time
import math
import hashlib
import unicodedata

from services.attraction_images import normalize_image_url


_WIKI_USER_AGENT = "ExploreXTravelPlanner/1.0 (educational trip-planning project)"

# MediaWiki asks clients to make requests serially.  The state search fans out
# over several cities, so a process-wide gate keeps the combined traffic polite
# instead of each city's worker firing its own burst.  One request per second
# keeps anonymous clients inside MediaWiki's fair-use window; 0.5s still drew
# HTTP 429s from a five-city state search.
_WIKI_REQUEST_LOCK = threading.Lock()
_WIKI_LAST_REQUEST_AT = 0.0
_WIKI_MIN_INTERVAL = 1.0


def _coerce_text(value):
    """Provider values are not guaranteed to be strings (Geoapify can emit a
    numeric ``name``); never call ``.lower()`` on the raw value."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _coerce_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _wiki_throttle():
    global _WIKI_LAST_REQUEST_AT
    with _WIKI_REQUEST_LOCK:
        wait = _WIKI_MIN_INTERVAL - (time.time() - _WIKI_LAST_REQUEST_AT)
        if wait > 0:
            time.sleep(wait)
        _WIKI_LAST_REQUEST_AT = time.time()

# Administrative / transport / commercial page titles that are never a tourist
# attraction worth showing in the selection grid. Kept deliberately narrow: a
# famous bridge or market must still survive.
_WIKI_EXCLUDE_TITLE = re.compile(
    r"\b("
    r"railway station|metro station|subway station|train station|bus terminal|"
    r"university|college|high school|elementary school|school|"
    r"hospital|clinic|embassy|consulate|prison|police station|fire station|"
    r"company|corporation|headquarters|bank|"
    r"ward|special ward|district|neighbourhood|neighborhood|"
    r"album|film|novel|song|video game|"
    r"siege|battle of|bombing|earthquake|riot|massacre|incident"
    r")\b",
    re.IGNORECASE,
)

# "Ueno Station" ends with the generic word; "Station Museum" does not.
_WIKI_TRAILING_STATION = re.compile(r"\bstation\s*(\(.*\))?\s*$", re.IGNORECASE)

# Wikipedia has an article for a company, a ward or a sumo stable too. The
# notability layer must only contribute *visitable places*, so require positive
# tourism evidence and reject obviously administrative/organisational topics.
_WIKI_POSITIVE_HINTS = (
    "tourist attraction", "visitor attraction", "attractions in",
    "museum", "gallery", "temple", "shrine", "church", "cathedral",
    "park", "garden", "castle", "palace", "monument", "memorial",
    "landmark", "observation", "tower", "bridge", "historic site",
    "world heritage", "market", "zoo", "aquarium", "amusement park",
    "theme park", "theatre", "theater", "festival", "beach", "mountain",
    "waterfall", "lake", "nature reserve", "national park", "scenic",
    "viewpoint", "onsen", "hot spring", "baths", "bathhouse",
    "scramble crossing", "intersection", "stadium", "arena", "botanical",
)

_WIKI_EXCLUDE_EVIDENCE = re.compile(
    r"("
    r"wards? of|special wards|municipalities|cities in|populated places|"
    r"neighbourhoods|neighborhoods|districts of|"
    r"companies|organizations|organisations|research institutes|"
    r"universities|colleges|schools|railway stations|metro stations|"
    r"airports|prisons|hospitals|stables|banks|financial markets|"
    r"power stations|incineration|factories|"
    r"roads|railway lines|military installations|embassies|"
    r"office buildings|apartment buildings|"
    r"lists of|disambiguation pages|tourism in|travel in"
    r")",
    re.IGNORECASE,
)

_WIKI_CATEGORY_HINTS = (
    ("observation", "viewpoints"),
    ("viewpoint", "viewpoints"),
    ("lookout", "viewpoints"),
    ("tower", "architecture"),
    ("skyscraper", "architecture"),
    ("bridge", "architecture"),
    ("museum", "museums"),
    ("gallery", "museums"),
    ("temple", "temples"),
    ("shrine", "temples"),
    ("place of worship", "temples"),
    ("castle", "history"),
    ("monument", "history"),
    ("historic", "history"),
    ("zoo", "family"),
    ("aquarium", "family"),
    ("amusement", "family"),
    ("theme park", "family"),
    ("park", "nature"),
    ("garden", "nature"),
    ("nature", "nature"),
    ("theatre", "culture"),
    ("theater", "culture"),
    ("festival", "culture"),
    ("market", "food"),
    ("shopping", "shopping"),
)


def _normalized_name(value):
    """Accent/punctuation-insensitive key for duplicate detection."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", "", text.lower(), flags=re.UNICODE)


def _haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in kilometres (no external dependency)."""
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))



def _parse_rating(value):
    """Return a float rating ONLY when a provider actually reported one.

    Never synthesize a score: an unknown rating is returned as None so the UI
    can show "Not rated" instead of an invented 4.0/4.5/4.8.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 < rating <= 5.0:
        return round(rating, 2)
    return None

_EATERY_AMENITY_LABELS = {
    "restaurant": "Restaurant",
    "cafe": "Cafe",
    "fast_food": "Fast food",
    "food_court": "Food court",
    "ice_cream": "Ice cream",
    "bar": "Bar",
    "pub": "Pub",
    "biergarten": "Beer garden",
    "street_vendor": "Street food",
    "bakery": "Bakery",
}


def _eatery_map_url(lat, lng):
    """OpenStreetMap permalink for a single verified POI coordinate."""
    return (
        f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lng:.6f}"
        f"#map=18/{lat:.6f}/{lng:.6f}"
    )


def _eatery_address(tags):
    """Compose an address only from OSM address tags that are actually set."""
    street = " ".join(
        part for part in (
            _coerce_text(tags.get("addr:housenumber")),
            _coerce_text(tags.get("addr:street")),
        ) if part
    ).strip()
    parts = [
        street,
        _coerce_text(tags.get("addr:suburb")),
        _coerce_text(tags.get("addr:city")),
        _coerce_text(tags.get("addr:postcode")),
    ]
    seen = set()
    clean = []
    for part in parts:
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            clean.append(part)
    return ", ".join(clean)


class POIManager:
    def __init__(self):
        self.geoapify_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
        # No hard-coded key: a secret committed into the repo is not a fallback,
        # it is a leak. Images simply stay unset when Pexels is not configured.
        self.pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
        self._cache = {} # In-memory cache for POIs
        # Successful Overpass queries are cached separately: a state search
        # repeats the same per-city query when the user regenerates a plan.
        self._query_cache = {}
        self._query_cache_lock = threading.Lock()
        # Serialises Overpass traffic and enforces a minimum interval between
        # calls so a burst of itinerary requests cannot trip the public mirrors.
        self._overpass_lock = threading.Lock()
        self._overpass_last_call = 0.0
        # Process-wide circuit breaker: when the public mirrors are down, later
        # steps (accommodations, restaurants, the next city) must fail fast
        # instead of repeating the same multi-mirror timeout cascade.
        self._overpass_breaker_until = 0.0
        self._overpass_consecutive_failures = 0
        try:
            self._overpass_min_interval = float(os.environ.get("OVERPASS_MIN_INTERVAL_SECONDS", "2"))
        except (TypeError, ValueError):
            self._overpass_min_interval = 2.0
        # Read endpoints from env, default to standard ones
        endpoints_env = os.environ.get("OVERPASS_ENDPOINTS", "")
        if endpoints_env:
            self.overpass_endpoints = [e.strip() for e in endpoints_env.split(",") if e.strip()]
        else:
            self.overpass_endpoints = [
                "https://overpass.kumi.systems/api/interpreter",
                "https://overpass-api.de/api/interpreter",
                "https://lz4.overpass-api.de/api/interpreter",
                "https://z.overpass-api.de/api/interpreter"
            ]
        # Structured provider health, readable by the workflow for observability.
        self._last_provider_status = {}
        self._provider_health = {}
        # Wikipedia GeoSearch is the notability layer: it supplies genuinely
        # well-known landmarks (with a description, image and article link) for
        # any city, so a destination is never represented only by obscure
        # OSM/Geoapify records. Opt-out via WIKIPEDIA_ENABLED=0.
        self.wikipedia_enabled = os.environ.get("WIKIPEDIA_ENABLED", "1").strip() != "0"
        self.wikipedia_language = os.environ.get("WIKIPEDIA_LANGUAGE", "en").strip() or "en"

    def _ensure_provider_state(self):
        """Create provider bookkeeping lazily.

        Callers (and the test suite) sometimes build a POIManager without running
        __init__; throttling and health reporting must never raise AttributeError
        because of that.
        """
        if not hasattr(self, "_overpass_lock"):
            self._overpass_lock = threading.Lock()
        if not hasattr(self, "_overpass_last_call"):
            self._overpass_last_call = 0.0
        if not hasattr(self, "_overpass_breaker_until"):
            self._overpass_breaker_until = 0.0
        if not hasattr(self, "_overpass_consecutive_failures"):
            self._overpass_consecutive_failures = 0
        if not hasattr(self, "_overpass_min_interval"):
            try:
                self._overpass_min_interval = float(os.environ.get("OVERPASS_MIN_INTERVAL_SECONDS", "2"))
            except (TypeError, ValueError):
                self._overpass_min_interval = 2.0
        if not hasattr(self, "_provider_health"):
            self._provider_health = {}
        if not hasattr(self, "_last_provider_status"):
            self._last_provider_status = {}
        if not hasattr(self, "_query_cache"):
            self._query_cache = {}
        if not hasattr(self, "_query_cache_lock"):
            self._query_cache_lock = threading.Lock()

    def get_provider_status(self):
        """Last known health per data provider (Phase 2/9 observability)."""
        self._ensure_provider_state()
        return dict(self._provider_health)

    def _record_status(self, provider, state, detail=""):
        self._ensure_provider_state()
        entry = {
            "state": state,
            "detail": detail,
            "checked_at": time.time(),
        }
        self._provider_health[provider] = entry
        self._last_provider_status[provider] = f"{state}:{detail}" if detail else state
        return entry

    def _publish_provider_status(self, provider_status):
        """Mirror a per-call status map into the durable health view.

        Every provider state is recorded - including a failure that happened
        alongside a *partial* success. Dropping the failed provider just because
        another one returned data is how a dead Overpass became invisible.
        """
        self._ensure_provider_state()
        self._last_provider_status = dict(provider_status)
        for provider, status in provider_status.items():
            state, _, detail = str(status).partition(":")
            if state == "skipped":
                continue
            if state == "ok":
                self._record_status(provider, "ok", f"{detail} POIs returned")
            elif state == "disabled":
                self._record_status(provider, "disabled", detail or "not configured")
            elif state == "empty":
                self._record_status(provider, "empty", "no POIs returned")
            else:
                self._record_status(provider, state, detail)
        return dict(self._provider_health)

    from functools import lru_cache

    @lru_cache(maxsize=1024)
    def _fetch_pexels_image(self, query, fallback_query=None):
        if not hasattr(self, 'pexels_key') or not self.pexels_key:
            return None
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": self.pexels_key}
        for q in [query, fallback_query]:
            if not q: continue
            params = {"query": q, "per_page": 1}
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('photos'):
                        return data['photos'][0]['src']['medium']
            except Exception:
                pass
        return None

    def get_attractions(self, lat: float, lng: float, radius: int = 20000, hobbies: str = None, poi_type: str = "tourist_attraction", retrieved_rag_pois=None, min_results: int = 30, city: str = None):
        """Comprehensive multi-source attraction discovery.

        Always queries BOTH Geoapify and Overpass and merges results, rather
        than short-circuiting after Geoapify. `min_results` is a *soft target*
        used only to decide whether to widen the search radius for a second
        pass — we never fabricate POIs to hit it. Radius default raised from
        10km to 20km so hill stations, dispersed regions and larger cities
        return their real breadth of attractions instead of a handful.
        """
        print(f"[POI_MANAGER] Fetching attractions for lat={lat}, lng={lng}, hobbies={hobbies}, radius={radius}")
        cache_key = self._generate_cache_key(lat, lng, radius, hobbies)

        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < 3600:
                print(f"[POI_MANAGER] Cache hit for {cache_key}. Returning {len(cache_entry['data'])} POIs.")
                return cache_entry['data']

        candidates = []
        provider_status = {"geoapify": "skipped", "overpass": "skipped", "wikipedia": "skipped"}
        if not self.geoapify_key:
            provider_status["geoapify"] = "disabled:no_api_key"
            self._record_status("geoapify", "disabled", "GEOAPIFY_API_KEY not configured")

        # 1. Geoapify — broad multi-category query (viewpoints, museums, heritage,
        #    nature, gardens, worship, water features etc.), NOT just famous landmarks.
        if self.geoapify_key:
            try:
                geo_results = self._fetch_geoapify_attractions(lat, lng, radius, hobbies)
                if geo_results:
                    print(f"[PROVIDER][GEOAPIFY] Returned {len(geo_results)} POIs.")
                    candidates.extend(geo_results)
                    provider_status["geoapify"] = f"ok:{len(geo_results)}"
                else:
                    provider_status["geoapify"] = "empty"
            except Exception as e:
                provider_status["geoapify"] = f"error:{type(e).__name__}"
                print(f"[PROVIDER][GEOAPIFY][WARN] Failed: {e}")

        # 2. Overpass ALWAYS runs (OSM tags many places that Geoapify misses —
        #    viewpoints, monasteries, waterfalls, markets, heritage monuments).
        #    We no longer skip it just because Geoapify returned enough.
        try:
            osm_results = self._fetch_osm_attractions(lat, lng, radius, hobbies)
            if osm_results:
                print(f"[PROVIDER][OVERPASS] Returned {len(osm_results)} POIs.")
                candidates.extend(osm_results)
                provider_status["overpass"] = f"ok:{len(osm_results)}"
            else:
                provider_status["overpass"] = "empty"
        except Exception as e:
            provider_status["overpass"] = f"error:{type(e).__name__}"
            print(f"[PROVIDER][OVERPASS][WARN] Failed: {e}")

        # 3. Wikipedia GeoSearch — notable, verifiable landmarks with a
        #    description and article link. This runs for every destination so a
        #    provider that only returns obscure local records cannot define the
        #    whole trip. Skipped on instances built without __init__ (test
        #    doubles) and when explicitly disabled.
        if getattr(self, "wikipedia_enabled", False):
            try:
                wiki_results = self._fetch_wikipedia_attractions(lat, lng, radius, city=city)
                if wiki_results:
                    print(f"[PROVIDER][WIKIPEDIA] Returned {len(wiki_results)} notable POIs.")
                    candidates.extend(wiki_results)
                    provider_status["wikipedia"] = f"ok:{len(wiki_results)}"
                else:
                    provider_status["wikipedia"] = "empty"
            except Exception as e:
                provider_status["wikipedia"] = f"error:{type(e).__name__}"
                print(f"[PROVIDER][WIKIPEDIA][WARN] Failed: {e}")

        # 4. Soft second pass — if both providers were thin, widen the radius
        #    ONCE (never loop unbounded) to give dispersed destinations a chance.
        merged_pre = self._deduplicate_pois(candidates) if candidates else []
        if len(merged_pre) < min_results and radius < 60000:
            wider = min(int(radius * 1.8), 60000)
            print(f"[POI_MANAGER] {len(merged_pre)} POIs < target {min_results}; widening radius to {wider}m for one retry.")
            try:
                if self.geoapify_key:
                    extra_geo = self._fetch_geoapify_attractions(lat, lng, wider, hobbies) or []
                    candidates.extend(extra_geo)
            except Exception as e:
                print(f"[PROVIDER][GEOAPIFY][WIDE-WARN] {e}")
            try:
                extra_osm = self._fetch_osm_attractions(lat, lng, wider, hobbies) or []
                candidates.extend(extra_osm)
            except Exception as e:
                print(f"[PROVIDER][OVERPASS][WIDE-WARN] {e}")

        # 4. Fallback to RAG POIs if live providers produced nothing.
        if not candidates and retrieved_rag_pois:
            print(f"[POI_MANAGER] Using {len(retrieved_rag_pois)} RAG POIs as fallback.")
            for p in retrieved_rag_pois:
                if 'location' in p and not 'latitude' in p:
                    p['latitude'] = p['location'].get('lat')
                    p['longitude'] = p['location'].get('lng')
            candidates.extend(retrieved_rag_pois)

        if not candidates:
            print(f"[POI_MANAGER] All live POI providers failed. Status: {provider_status}")
            # Expose provider status on the empty list so callers can report
            # the reason transparently instead of silently returning 0 hits.
            self._publish_provider_status(provider_status)
            return []

        final_pois = self._deduplicate_pois(candidates)
        print(f"[POI_MANAGER] Merged & Deduplicated: {len(final_pois)} total final POIs. Provider status: {provider_status}")
        self._publish_provider_status(provider_status)

        self._cache[cache_key] = {
            'timestamp': time.time(),
            'data': final_pois
        }

        return final_pois

    def _generate_cache_key(self, lat, lng, radius, hobbies):
        raw = f"{lat:.4f},{lng:.4f},{radius},{hobbies}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _deduplicate_pois(self, pois):
        """Merge provider records into unique places.

        Every record is normalised defensively: external providers occasionally
        return a numeric name, a string coordinate or a non-dict metadata blob.
        A single malformed record must not discard the rest of the city's
        results (the old ``(poi.get('name') or '').lower()`` call crashed the
        whole Kochi search when Geoapify emitted an integer name).
        """
        def calculate_distance(lat1, lon1, lat2, lon2):
            R = 6371  # Radius of the earth in km
            dLat = math.radians(lat2 - lat1)
            dLon = math.radians(lon2 - lon1)
            a = math.sin(dLat/2) * math.sin(dLat/2) + \
                math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
                math.sin(dLon/2) * math.sin(dLon/2)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            return R * c

        def poi_score(poi):
            score = 0
            metadata = poi.get('metadata') if isinstance(poi.get('metadata'), dict) else {}
            if metadata.get('wikidata_id'):
                score += 100
            # A Wikipedia article is the strongest available evidence that a
            # place is a real, notable attraction rather than incidental OSM
            # noise. It still merges data from the other providers below.
            if poi.get('source') == 'wikipedia':
                score += 80
            if poi.get('source') == 'osm':
                score += 20
            if poi.get('source') == 'geoapify':
                score += 10
            name = _coerce_text(poi.get('name')).lower()
            if 'viewpoint' in name or 'point de vue' in name:
                score -= 30
            if 'gate' in name or 'entrance' in name or 'parking' in name:
                score -= 50
            score -= (len(name) * 0.1)
            return score

        def merge_fields(best_poi, other_poi):
            """Carry missing/extra evidence from a duplicate into the winner."""
            metadata = best_poi.get('metadata')
            if not isinstance(metadata, dict):
                metadata = {}
                best_poi['metadata'] = metadata
            other_metadata = other_poi.get('metadata')
            if isinstance(other_metadata, dict):
                for key, value in other_metadata.items():
                    metadata.setdefault(key, value)

            for field in (
                'description', 'image_url', 'wikipedia_url', 'wikidata_id',
                'website', 'opening_hours', 'address', 'rating', 'subcategory',
                'city', 'state_city', 'region', 'country', 'country_code',
            ):
                if not best_poi.get(field) and other_poi.get(field):
                    best_poi[field] = other_poi[field]

            if not best_poi.get('category') or best_poi.get('category') == 'tourist_attraction':
                if other_poi.get('category') and other_poi.get('category') != 'tourist_attraction':
                    best_poi['category'] = other_poi['category']

            merged_types = list(best_poi.get('types') or [])
            other_types = other_poi.get('types')
            if isinstance(other_types, (list, tuple, set)):
                for value in other_types:
                    if value not in merged_types:
                        merged_types.append(value)
            if merged_types:
                best_poi['types'] = merged_types

            # Preserve every provider that corroborated this place.
            sources = list(best_poi.get('sources') or [])
            for value in [best_poi.get('source')] + list(other_poi.get('sources') or []) + [other_poi.get('source')]:
                if value and value not in sources:
                    sources.append(value)
            if sources:
                best_poi['sources'] = sources

        grouped_pois = []
        skipped = 0
        for raw_poi in pois or []:
            if not isinstance(raw_poi, dict):
                skipped += 1
                continue
            try:
                poi = dict(raw_poi)
                poi['name'] = _coerce_text(poi.get('name'))
                name = _normalized_name(poi.get('name'))
                if not name or str(poi['name']).isdigit():
                    # A bare number is not an attraction; skip it but keep the
                    # rest of the city's records (the old code crashed here).
                    skipped += 1
                    continue
                lat = _coerce_float(poi.get('latitude', poi.get('lat')))
                lng = _coerce_float(poi.get('longitude', poi.get('lng')))
                if lat is None or lng is None:
                    location = poi.get('location')
                    if isinstance(location, dict):
                        lat = _coerce_float(location.get('lat'))
                        lng = _coerce_float(location.get('lng'))
                if lat is None or lng is None:
                    lat, lng = 0.0, 0.0
                # Keep the original behaviour: a (0, 0) record has no usable
                # location and would otherwise collapse unrelated places.
                if lat == 0 and lng == 0:
                    skipped += 1
                    continue
                poi['latitude'] = lat
                poi['longitude'] = lng
                poi['location'] = {'lat': lat, 'lng': lng}
                if poi.get('image_url'):
                    normalised = normalize_image_url(poi.get('image_url'))
                    if normalised:
                        poi['image_url'] = normalised
                    else:
                        poi.pop('image_url', None)
            except Exception as exc:
                # Log the offending record type, never the whole payload.
                skipped += 1
                print(f"[POI_MANAGER][WARN] Skipping malformed POI "
                      f"({type(exc).__name__}: {exc})")
                continue

            matched_group_idx = -1
            for i, group in enumerate(grouped_pois):
                for seen_poi in group:
                    seen_name = _normalized_name(seen_poi.get('name'))
                    if not seen_name:
                        continue
                    seen_lat = _coerce_float(seen_poi.get('latitude'))
                    seen_lng = _coerce_float(seen_poi.get('longitude'))
                    if seen_lat is None or seen_lng is None:
                        continue

                    id_match = bool(poi.get('id') and poi.get('id') == seen_poi.get('id'))
                    # Normalised names make "Tōkyō Tower" / "Tokyo Tower" /
                    # "Tokyo Tower " one place instead of three cards.
                    exact_name = name == seen_name and len(name) >= 4
                    contained = (
                        (name in seen_name or seen_name in name)
                        and min(len(name), len(seen_name)) >= 6
                    )
                    dist = calculate_distance(lat, lng, seen_lat, seen_lng)
                    # Same place spelt differently may sit a few hundred metres
                    # apart in the two datasets; near-identical coordinates are
                    # duplicates regardless of the name.
                    name_match = (exact_name and dist < 2.0) or (contained and dist < 1.0)
                    duplicate_loc = dist < 0.01

                    if id_match or name_match or duplicate_loc:
                        matched_group_idx = i
                        break
                if matched_group_idx != -1:
                    break

            if matched_group_idx != -1:
                grouped_pois[matched_group_idx].append(poi)
            else:
                grouped_pois.append([poi])

        deduplicated = []
        for group in grouped_pois:
            group.sort(key=lambda p: poi_score(p), reverse=True)
            best_poi = group[0]
            for other_poi in group[1:]:
                merge_fields(best_poi, other_poi)

            # Reconstruct location field for backward compatibility
            best_poi['location'] = {'lat': best_poi['latitude'], 'lng': best_poi['longitude']}
            deduplicated.append(best_poi)

        if skipped:
            print(f"[POI_MANAGER] Deduplicated {len(deduplicated)} POIs; "
                  f"skipped {skipped} unusable record(s).")
        return deduplicated

    def get_accommodations(self, lat: float, lng: float, budget: str, number: int = 4, radius: int = 15000):
        print(f"[POI_MANAGER] Fetching accommodations for lat={lat}, lng={lng}, budget={budget}")
        cache_key = self._generate_cache_key(lat, lng, radius, f"accom_{budget}")
        
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < 3600 * 24:
                print(f"[POI_MANAGER] Cache hit for {cache_key}.")
                return cache_entry['data'][:number]

        results = []
        acc_status = {"overpass": "skipped", "geoapify": "skipped"}
        try:
            results = self._fetch_osm_accommodations(lat, lng, radius, budget, number)
            acc_status["overpass"] = f"ok:{len(results)}" if results else "empty"
        except Exception as e:
            acc_status["overpass"] = f"error:{type(e).__name__}"
            print(f"[PROVIDER][OVERPASS][WARN] Accommodations failed: {e}")

        if not results and self.geoapify_key:
            try:
                results = self._fetch_geoapify_accommodations(lat, lng, radius, budget, number)
                acc_status["geoapify"] = f"ok:{len(results)}" if results else "empty"
            except Exception as e:
                acc_status["geoapify"] = f"error:{type(e).__name__}"
                print(f"[PROVIDER][GEOAPIFY][WARN] Accommodations failed: {e}")
        elif not results:
            acc_status["geoapify"] = "disabled:no_api_key"
        for provider, status in acc_status.items():
            state, _, detail = status.partition(":")
            self._record_status(f"accommodation_{provider}", state, detail)

        if results:
            self._cache[cache_key] = {
                'timestamp': time.time(),
                'data': results
            }
            return results[:number]

        if cache_key in self._cache:
            print(f"[PROVIDER][ACCOMMODATION][DEGRADED] Using expired cache data.")
            return self._cache[cache_key]['data'][:number]

        print(f"[PROVIDER][ACCOMMODATION][DEGRADED] All accommodation providers failed "
              f"(status={acc_status}). Returning safe empty list.")
        return []

    def get_restaurants(self, lat: float, lng: float, radius: int = 5000, number: int = 20):
        print(f"[POI_MANAGER] Fetching restaurants for lat={lat}, lng={lng}")
        cache_key = self._generate_cache_key(lat, lng, radius, "restaurants")
        
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < 3600 * 24:
                print(f"[POI_MANAGER] Cache hit for {cache_key}.")
                return cache_entry['data'][:number]
                
        all_results = []
        try:
            osm_results = self._fetch_osm_restaurants(lat, lng, radius, number)
            if osm_results:
                all_results.extend(osm_results)
        except Exception as e:
            print(f"[PROVIDER][OVERPASS][WARN] Restaurants failed: {e}")

        if len(all_results) < number and self.geoapify_key:
            try:
                geo_results = self._fetch_geoapify_restaurants(lat, lng, radius, number)
                if geo_results:
                    all_results.extend(geo_results)
            except Exception as e:
                print(f"[PROVIDER][GEOAPIFY][WARN] Restaurants failed: {e}")
        
        # Deduplicate
        seen = set()
        deduped = []
        for r in all_results:
            name_val = r.get('name', '')
            name_key = str(name_val).lower().strip() if name_val is not None else ""
            if name_key and name_key not in seen:
                seen.add(name_key)
                deduped.append(r)
                if len(deduped) >= number: break
                
        if deduped:
            self._cache[cache_key] = {
                'timestamp': time.time(),
                'data': deduped
            }
            return deduped

        if cache_key in self._cache:
            print(f"[PROVIDER][RESTAURANT][DEGRADED] Using expired cache data.")
            return self._cache[cache_key]['data'][:number]
            
        print("[PROVIDER][RESTAURANT][DEGRADED] All providers failed. Returning safe empty list.")
        return []

    # -------------------------------------------------------------------------
    # NEARBY EATERIES (attraction modal)
    # -------------------------------------------------------------------------
    def get_nearby_eateries(self, lat, lng, radius=1000, number=12):
        """Return real food POIs around one exact attraction coordinate.

        Distances are haversine (great-circle) metres computed from the supplied
        attraction coordinate and each POI's own provider coordinate.  A POI
        without coordinates is omitted rather than given an estimated distance.
        Overpass is tried first (reusing the shared throttle/cache/breaker); the
        configured Geoapify key is a fallback when OSM returns nothing.
        """
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return self._nearby_eateries_payload(
                None, None, [], "none", "invalid", 0,
                "Attraction coordinates are missing or invalid.",
            )
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            return self._nearby_eateries_payload(
                None, None, [], "none", "invalid", 0,
                "Attraction coordinates are missing or invalid.",
            )

        try:
            radius = max(100, min(int(radius), 5000))
        except (TypeError, ValueError):
            radius = 1000
        try:
            number = max(1, min(int(number), 25))
        except (TypeError, ValueError):
            number = 12

        cache = getattr(self, "_cache", None)
        if cache is None:
            self._cache = cache = {}
        # Do not reuse the 4-decimal POI cache key here: nearby results include
        # distances, so two attractions even ~10 m apart must not share a value.
        cache_key = hashlib.md5(
            f"{lat:.6f},{lng:.6f},{radius},nearby_eats_{number}".encode("utf-8")
        ).hexdigest()
        cached = cache.get(cache_key)
        if cached and time.time() - cached.get("timestamp", 0) < 3600:
            cached_data = [r for r in (cached.get("data") or []) if isinstance(r, dict)]
            if cached_data:
                return self._nearby_eateries_payload(
                    lat, lng, cached_data[:number], cached.get("source", "osm"), "ok", radius,
                )

        overpass_failed = False
        elements = []
        try:
            elements = self._execute_overpass_query(
                self._nearby_eateries_query(lat, lng, radius), max_seconds=15
            ) or []
        except Exception as e:
            overpass_failed = True
            print(f"[PROVIDER][NEARBY][WARN] Overpass lookup failed: {type(e).__name__}")

        if not elements:
            provider_state = (self.get_provider_status().get("overpass") or {}).get("state")
            if provider_state in {"unavailable", "error", "timeout", "cooldown"}:
                overpass_failed = True

        results = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            entry = self._nearby_entry_from_osm(element, lat, lng)
            if entry:
                results.append(entry)
        source = "osm" if results else "none"

        geoapify_failed = False
        if not results and getattr(self, "geoapify_key", ""):
            try:
                results = self._fetch_geoapify_nearby(lat, lng, radius, number)
            except Exception as e:
                geoapify_failed = True
                results = []
                print(f"[PROVIDER][GEOAPIFY][WARN] Nearby eateries failed: {type(e).__name__}")
            if results:
                source = "geoapify"

        # A provider can return the same place more than once (node + way or a
        # second provider). Keep one entry per name+coordinate, nearest first.
        deduped = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            name_key = _normalized_name(item.get("name"))
            if not name_key:
                continue
            key = (name_key, round(item.get("latitude", 0), 5), round(item.get("longitude", 0), 5))
            current = deduped.get(key)
            if current is None or item.get("distance_m", 10 ** 9) < current.get("distance_m", 10 ** 9):
                deduped[key] = item
        results = sorted(
            deduped.values(),
            key=lambda r: (r.get("distance_m", 10 ** 9), str(r.get("name", "")).lower()),
        )[:number]

        if results:
            cache[cache_key] = {"timestamp": time.time(), "data": results, "source": source}
            return self._nearby_eateries_payload(lat, lng, results, source, "ok", radius)

        if overpass_failed and (not getattr(self, "geoapify_key", "") or geoapify_failed):
            return self._nearby_eateries_payload(
                lat, lng, [], "none", "unavailable", radius,
                "Nearby restaurant data is temporarily unavailable. Please try again.",
            )

        return self._nearby_eateries_payload(
            lat, lng, [], "none", "empty", radius,
            f"No eateries were found within {radius} m of this attraction.",
        )

    @staticmethod
    def _nearby_eateries_payload(lat, lng, restaurants, source, status, radius, message=None):
        """Stable response contract shared by the API and the frontend."""
        return {
            "status": status,
            "restaurants": restaurants,
            "count": len(restaurants),
            "radius_m": radius,
            "distance_type": "straight_line",
            "distance_method": "haversine",
            "source": source,
            "attraction": {"latitude": lat, "longitude": lng},
            "message": message,
        }

    @staticmethod
    def _nearby_eateries_query(lat, lng, radius):
        return (
            "[out:json][timeout:15];\n"
            "(\n"
            '  nwr["amenity"~"restaurant|cafe|fast_food|food_court|ice_cream|'
            'bar|pub|biergarten|street_vendor"]'
            f"(around:{radius},{lat},{lng});\n"
            '  nwr["shop"="bakery"]'
            f"(around:{radius},{lat},{lng});\n"
            ");\n"
            "out center 60;"
        )

    @staticmethod
    def _nearby_entry_from_osm(element, lat, lng):
        tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
        name = _coerce_text(tags.get("name") or tags.get("name:en"))
        if not name:
            return None

        center = element.get("center") if isinstance(element.get("center"), dict) else {}
        el_lat = _coerce_float(element.get("lat", center.get("lat")))
        el_lng = _coerce_float(element.get("lon", center.get("lon")))
        if el_lat is None or el_lng is None:
            return None
        if not (-90.0 <= el_lat <= 90.0 and -180.0 <= el_lng <= 180.0):
            return None

        amenity = _coerce_text(tags.get("amenity")).lower()
        shop = _coerce_text(tags.get("shop")).lower()
        kind = amenity or ("bakery" if shop == "bakery" else "")
        type_label = _EATERY_AMENITY_LABELS.get(kind)
        if not type_label:
            type_label = kind.replace("_", " ").title() if kind else "Eatery"

        distance_m = int(round(_haversine_km(lat, lng, el_lat, el_lng) * 1000))
        entry = {
            "name": name,
            "type": type_label,
            "category": "Food",
            "latitude": round(el_lat, 6),
            "longitude": round(el_lng, 6),
            "distance_m": distance_m,
            "distance_text": f"{distance_m} m" if distance_m < 1000 else f"{distance_m / 1000:.1f} km",
            "distance_type": "straight_line",
            "map_url": _eatery_map_url(el_lat, el_lng),
            "source": "osm",
        }

        cuisine = _coerce_text(tags.get("cuisine"))
        if cuisine:
            pieces = [
                piece.replace("_", " ").strip().title()
                for piece in cuisine.replace(";", ",").split(",")
                if piece.strip()
            ]
            if pieces:
                entry["cuisine"] = ", ".join(pieces)

        address = _eatery_address(tags)
        if address:
            entry["address"] = address
        opening_hours = _coerce_text(tags.get("opening_hours"))
        if opening_hours:
            entry["opening_hours"] = opening_hours
        website = _coerce_text(tags.get("website") or tags.get("contact:website"))
        if website:
            entry["website"] = website
        return entry

    def _fetch_geoapify_nearby(self, lat, lng, radius, number):
        """Geoapify fallback for nearby food POIs (no stock-photo calls)."""
        api_key = getattr(self, "geoapify_key", "")
        if not api_key:
            return []
        params = {
            "categories": (
                "catering.restaurant,catering.cafe,catering.fast_food,"
                "catering.food_court,catering.ice_cream"
            ),
            "filter": f"circle:{lng},{lat},{radius}",
            "limit": max(15, number * 3),
            "apiKey": api_key,
        }
        try:
            resp = requests.get("https://api.geoapify.com/v2/places", params=params, timeout=8)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.exceptions.RequestException, ValueError):
            return []

        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            return []

        generic = {"catering", "restaurant", "cafe", "fast_food", "food_court", "ice_cream"}
        results = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            name = _coerce_text(props.get("name"))
            if not name:
                continue

            geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
            coords = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else []
            geo_lat = _coerce_float(props.get("lat"))
            geo_lng = _coerce_float(props.get("lon"))
            if geo_lat is None and len(coords) >= 2:
                geo_lat = _coerce_float(coords[1])
            if geo_lng is None and len(coords) >= 2:
                geo_lng = _coerce_float(coords[0])
            if geo_lat is None or geo_lng is None:
                continue
            if not (-90.0 <= geo_lat <= 90.0 and -180.0 <= geo_lng <= 180.0):
                continue

            raw_categories = props.get("categories")
            categories = [str(c) for c in raw_categories if c] if isinstance(raw_categories, list) else []
            # Accessibility/attribute categories ("wheelchair",
            # "internet_access") are not food types. Only the catering branch
            # describes what kind of eatery this is.
            catering_categories = [
                c for c in categories if c == "catering" or c.startswith("catering.")
            ]
            leaves = [c.split(".")[-1] for c in catering_categories if c]
            # Geoapify lists broad categories first ("catering",
            # "catering.restaurant") and the specific subtype later.
            generic_leaf = next(
                (leaf for leaf in reversed(leaves) if leaf in generic and leaf in _EATERY_AMENITY_LABELS),
                "",
            )
            specific = next((leaf for leaf in leaves if leaf not in generic), "")
            type_label = _EATERY_AMENITY_LABELS.get(generic_leaf, "Eatery")

            cuisine = _coerce_text(props.get("cuisine"))
            if not cuisine and specific:
                cleaned = specific
                for suffix in ("_restaurant", "_cafe", "_fast_food", "_food_court", "_ice_cream"):
                    if cleaned.endswith(suffix):
                        cleaned = cleaned[: -len(suffix)]
                        break
                cuisine = cleaned.strip("_").replace("_", " ").title()
            if cuisine:
                cuisine = cuisine.replace("_", " ").replace(";", ", ").strip().title()

            distance_m = int(round(_haversine_km(lat, lng, geo_lat, geo_lng) * 1000))
            entry = {
                "name": name,
                "type": type_label,
                "category": "Food",
                "latitude": round(geo_lat, 6),
                "longitude": round(geo_lng, 6),
                "distance_m": distance_m,
                "distance_text": f"{distance_m} m" if distance_m < 1000 else f"{distance_m / 1000:.1f} km",
                "distance_type": "straight_line",
                "map_url": _eatery_map_url(geo_lat, geo_lng),
                "source": "geoapify",
            }
            if cuisine:
                entry["cuisine"] = cuisine
            address = _coerce_text(props.get("formatted"))
            if not address:
                address = ", ".join(
                    part for part in (
                        _coerce_text(props.get("address_line1")),
                        _coerce_text(props.get("address_line2")),
                    ) if part
                )
            if address:
                entry["address"] = address
            opening_hours = _coerce_text(props.get("opening_hours"))
            if opening_hours:
                entry["opening_hours"] = opening_hours
            website = _coerce_text(props.get("website"))
            if website:
                entry["website"] = website
            results.append(entry)
        return results

    # -------------------------------------------------------------------------
    # OVERPASS API
    # -------------------------------------------------------------------------
    def _throttled_post(self, endpoint, query, headers, connect_timeout, read_timeout):
        """Serialise + rate-limit Overpass traffic across threads.

        Public mirrors reject bursts from a single client; one shared minimum
        interval keeps every caller (attractions, accommodations, restaurants)
        inside the fair-use window without adding unbounded latency.
        """
        self._ensure_provider_state()
        with self._overpass_lock:
            wait = self._overpass_min_interval - (time.time() - self._overpass_last_call)
            if wait > 0:
                time.sleep(min(wait, self._overpass_min_interval))
            self._overpass_last_call = time.time()
        return requests.post(
            endpoint,
            data={"data": query},
            headers=headers,
            timeout=(connect_timeout, read_timeout),
        )

    @staticmethod
    def _retry_after_seconds(resp, fallback):
        """Respect a server supplied Retry-After (seconds or HTTP-date)."""
        raw = None
        try:
            raw = resp.headers.get("Retry-After")
        except Exception:
            raw = None
        if not raw:
            return fallback
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(raw)
                if dt is not None:
                    return max(0.0, (dt - dt.now(dt.tzinfo)).total_seconds())
            except Exception:
                pass
        return fallback

    def _note_overpass_failure(self, detail, force_breaker=False):
        """Count a failed query and open the shared breaker when mirrors are down."""
        self._overpass_consecutive_failures = getattr(self, "_overpass_consecutive_failures", 0) + 1
        if not force_breaker:
            try:
                breaker_failures = int(os.environ.get("OVERPASS_BREAKER_FAILURES", "2"))
            except (TypeError, ValueError):
                breaker_failures = 2
            if self._overpass_consecutive_failures < max(1, breaker_failures):
                return
        try:
            breaker_seconds = float(os.environ.get("OVERPASS_BREAKER_SECONDS", "90"))
        except (TypeError, ValueError):
            breaker_seconds = 90.0
        self._overpass_breaker_until = time.time() + max(30.0, breaker_seconds)
        print(f"[PROVIDER][OVERPASS][WARN] Circuit breaker open for {breaker_seconds:.0f}s "
              f"({detail}).")

    def _execute_overpass_query(self, query: str, max_seconds=None):
        headers = {
            "User-Agent": os.environ.get("OVERPASS_USER_AGENT", "ExploreX-Travel-App/1.0 (contact: app owner)"),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        # Successful responses are reused for the same query.  A state search
        # repeats per-city queries when the user regenerates a plan (and the
        # attractions/restaurants paths can share a bbox), so this avoids
        # hammering a public mirror that already answered us.
        self._ensure_provider_state()
        cache_key = hashlib.md5(query.encode("utf-8")).hexdigest()
        with self._query_cache_lock:
            cached = self._query_cache.get(cache_key)
        if cached and time.time() - cached[0] < 1800:
            elements = cached[1]
            self._record_status("overpass", "ok", f"{len(elements)} cached elements")
            return list(elements)

        # Public Overpass mirrors are frequently rate-limited (429/502) or slow.
        # A client read timeout SHORTER than the server-side [timeout:NN] made us
        # abandon queries that were about to succeed. Read a generous timeout from
        # env (default 25s, matching the server hint) but cap the whole query's
        # wall-clock budget so a dead network fails in tens of seconds, not
        # minutes. A failed Overpass round is NEVER fatal - callers keep whatever
        # Geoapify/Wikipedia returned.
        max_retries = 3
        try:
            read_timeout = float(os.environ.get("OVERPASS_TIMEOUT", "25"))
        except (TypeError, ValueError):
            read_timeout = 25.0
        try:
            connect_timeout = float(os.environ.get("OVERPASS_CONNECT_TIMEOUT", "5"))
        except (TypeError, ValueError):
            connect_timeout = 5.0
        try:
            max_seconds = float(max_seconds) if max_seconds is not None else float(os.environ.get("OVERPASS_MAX_SECONDS", "40"))
        except (TypeError, ValueError):
            max_seconds = 40.0

        if not hasattr(self, 'overpass_cooldowns'):
            self.overpass_cooldowns = {}

        now = time.time()
        if now < getattr(self, "_overpass_breaker_until", 0.0):
            self._record_status("overpass", "cooldown", "circuit breaker open")
            print("[PROVIDER][OVERPASS][WARN] Circuit breaker open; skipping this query (non-fatal).")
            return []

        deadline = now + max(5.0, max_seconds)
        available_endpoints = [e for e in self.overpass_endpoints if now > self.overpass_cooldowns.get(e, 0)]
        
        if not available_endpoints:
            print("[PROVIDER][OVERPASS][WARN] All endpoints in cooldown. Failing fast (non-fatal).")
            self._record_status("overpass", "cooldown", "all mirrors cooling down")
            self._note_overpass_failure("all mirrors cooling down", force_breaker=True)
            return []

        last_exception = None
        for attempt in range(max_retries):
            remaining = deadline - time.time()
            if remaining <= 1.0:
                last_exception = last_exception or TimeoutError("Overpass query budget exhausted")
                print("[PROVIDER][OVERPASS][WARN] Time budget exhausted; giving up on this query.")
                break

            # Recompute from the live cooldown map: mirrors that another thread
            # just failed must be skipped, not retried blindly.
            available_endpoints = [
                e for e in available_endpoints
                if time.time() > self.overpass_cooldowns.get(e, 0)
            ]
            if not available_endpoints:
                last_exception = last_exception or RuntimeError("all mirrors cooling down")
                self._record_status("overpass", "cooldown", "all mirrors cooling down")
                break

            endpoint = available_endpoints[0]
            attempt_timeout = (connect_timeout, min(read_timeout, max(1.0, remaining)))
            try:
                resp = self._throttled_post(endpoint, query, headers, *attempt_timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    backoff = self._retry_after_seconds(resp, (2 ** attempt) + random.uniform(0, 1))
                    print(f"[PROVIDER][OVERPASS][WARN] Recoverable error {resp.status_code} on {endpoint}. "
                          f"Backing off {backoff:.1f}s.")
                    self.overpass_cooldowns[endpoint] = time.time() + max(30, backoff)
                    # Try a DIFFERENT mirror on the next attempt rather than hammering
                    # the endpoint that just pushed us back.  A 429 means the mirror
                    # is not going to answer this request; don't burn the request's
                    # latency budget sleeping on it.
                    available_endpoints = available_endpoints[1:] + available_endpoints[:1] if len(available_endpoints) > 1 else available_endpoints
                    time.sleep(min(backoff, 3))
                    last_exception = Exception(f"HTTP {resp.status_code}")
                    continue
                    
                resp.raise_for_status()
                try:
                    payload = resp.json()
                except ValueError:
                    last_exception = ValueError("Overpass returned invalid JSON")
                    self._record_status("overpass", "error", "invalid JSON")
                    continue
                raw_elements = payload.get('elements') if isinstance(payload, dict) else None
                elements = raw_elements if isinstance(raw_elements, list) else []
                elements = [el for el in elements if isinstance(el, dict)]
                with self._query_cache_lock:
                    self._query_cache[cache_key] = (time.time(), elements)
                    if len(self._query_cache) > 256:
                        oldest = sorted(self._query_cache.items(), key=lambda item: item[1][0])[:64]
                        for old_key, _ in oldest:
                            self._query_cache.pop(old_key, None)
                self._record_status("overpass", "ok", f"{len(elements)} raw elements")
                self._overpass_consecutive_failures = 0
                self._overpass_breaker_until = 0.0
                return elements
            except requests.exceptions.Timeout as e:
                print(f"[PROVIDER][OVERPASS][WARN] Timeout on {endpoint}: {type(e).__name__}")
                self.overpass_cooldowns[endpoint] = time.time() + 60
                last_exception = e
                self._record_status("overpass", "timeout", type(e).__name__)
                time.sleep((2 ** attempt) + random.uniform(0, 1))
            except requests.exceptions.RequestException as e:
                last_exception = e
                print(f"[PROVIDER][OVERPASS][WARN] Attempt {attempt+1} failed on {endpoint}: {type(e).__name__}")
                self.overpass_cooldowns[endpoint] = time.time() + 15
                self._record_status("overpass", "error", type(e).__name__)
                time.sleep((2 ** attempt) + random.uniform(0, 1))
            except Exception as e:
                last_exception = e
                print(f"[PROVIDER][OVERPASS][WARN] Attempt {attempt+1} unexpected failure on {endpoint}: {type(e).__name__}")
                self.overpass_cooldowns[endpoint] = time.time() + 15
                self._record_status("overpass", "error", type(e).__name__)
                time.sleep((2 ** attempt) + random.uniform(0, 1))
        
        detail = f"{type(last_exception).__name__}: {last_exception}" if last_exception else "retries exhausted"
        print(f"[PROVIDER][OVERPASS][UNAVAILABLE] All attempts exhausted - continuing without OSM data. {detail}")
        self._record_status("overpass", "unavailable", detail[:200])
        self._note_overpass_failure(detail)
        # Returning [] keeps the workflow alive on partial Geoapify results.
        return []

    def _get_category_filters(self, hobbies, radius, lat, lng):
        """Build an Overpass union that covers the full spectrum of tourist-attraction
        categories from Requirement 1, not just famous landmarks.

        The hobbies string biases the ordering / adds extras but the *default*
        set is intentionally comprehensive so a destination without a strong
        hobby signal still yields viewpoints, nature, culture, heritage,
        museums, wildlife, markets and distinctive places.
        """
        import math
        earth_radius = 6371000
        lat_delta = (radius / earth_radius) * (180 / math.pi)
        lng_delta = (radius / earth_radius) * (180 / math.pi) / math.cos(lat * math.pi / 180)
        s = lat - lat_delta
        w = lng - lng_delta
        n = lat + lat_delta
        e = lng + lng_delta
        bbox = f"{s},{w},{n},{e}"

        filters = []
        h = str(hobbies).lower() if hobbies else ""

        # ── Universal baseline coverage (always included) ──────────────────
        filters.append(f'nwr["tourism"~"attraction|museum|viewpoint|gallery|theme_park|artwork|zoo|aquarium|wildlife_hospital|picnic_site"]({bbox});')
        filters.append(f'nwr["historic"]({bbox});')
        filters.append(f'nwr["leisure"~"park|nature_reserve|garden|beach_resort|marina|dog_park|water_park|recreation_ground"]({bbox});')
        filters.append(f'nwr["natural"~"waterfall|peak|beach|cave_entrance|spring|geyser|volcano|cliff|valley|lake|river"]({bbox});')
        filters.append(f'nwr["amenity"~"place_of_worship|theatre|marketplace"]({bbox});')

        # ── Hobby-specific expansions (added on top of the baseline) ───────
        if "history" in h or "architecture" in h or "heritage" in h:
            filters.append(f'nwr["tourism"~"historic_site|hotel"]["historic"]({bbox});')
        if "nature" in h or "adventure" in h or "wildlife" in h:
            filters.append(f'nwr["leisure"~"nature_reserve"]({bbox});')
            filters.append(f'nwr["tourism"~"wilderness_hut|alpine_hut|camp_site"]({bbox});')
        if "spiritual" in h or "temple" in h or "religion" in h or "monastery" in h:
            filters.append(f'nwr["amenity"~"place_of_worship"]["religion"]({bbox});')
        if "art" in h or "culture" in h or "museum" in h:
            filters.append(f'nwr["tourism"~"museum|gallery"]({bbox});')
        if "photography" in h or "viewpoint" in h:
            filters.append(f'nwr["tourism"="viewpoint"]({bbox});')
        if "shopping" in h or "market" in h or "local" in h:
            filters.append(f'nwr["amenity"="marketplace"]({bbox});')
            filters.append(f'nwr["shop"~"craft|art|antiques|gift"]({bbox});')
        if "food" in h:
            filters.append(f'nwr["amenity"~"marketplace|food_court"]({bbox});')

        # Preserve the previous broad fallback too.
        if not filters:
            filters.append(f'nwr["tourism"~"attraction|museum|viewpoint|gallery|theme_park"]({bbox});')

        # Deduplicate identical filter strings (multiple hobbies may add the same one)
        unique = list(dict.fromkeys(filters))
        return "".join(unique)

    def _fetch_osm_attractions(self, lat, lng, radius, hobbies):
        cat_filters = self._get_category_filters(hobbies, min(radius, 15000), lat, lng)
        # Using [out:json][timeout:25] and bounding box for elements, limited via Python logic rather than broken out limits
        query = f"[out:json][timeout:25];({cat_filters});out center;"
        
        elements = self._execute_overpass_query(query)
        normalized = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            tags = el.get('tags') if isinstance(el.get('tags'), dict) else {}
            name = _coerce_text(tags.get('name') or tags.get('name:en'))
            if not name or name.isdigit():
                continue

            if tags.get('amenity') in ['bank', 'atm', 'hospital', 'clinic', 'dentist', 'pharmacy', 'police', 'post_office', 'waste_basket', 'vending_machine', 'parking', 'fuel', 'taxi']:
                continue
            if tags.get('shop') or tags.get('office') or tags.get('highway'):
                continue
            
            el_type = tags.get('tourism') or tags.get('historic') or tags.get('amenity') or tags.get('leisure') or "attraction"
            
            addr_parts = [tags.get('addr:housenumber', ''), tags.get('addr:street', ''), tags.get('addr:city', ''), tags.get('addr:postcode', '')]
            address = ' '.join(p for p in addr_parts if p).strip() or tags.get('addr:full', '') or None

            osm_description = tags.get('description') or tags.get('description:en') or None

            center = el.get('center') if isinstance(el.get('center'), dict) else {}
            latitude = _coerce_float(el.get('lat', center.get('lat')))
            longitude = _coerce_float(el.get('lon', center.get('lon')))
            if latitude is None or longitude is None:
                continue

            poi = {
                'id': f"osm_{el.get('id')}",
                'source': 'osm',
                'name': name,
                'latitude': latitude,
                'longitude': longitude,
                'category': el_type,
                'subcategory': tags.get(el_type, ''),
                # No invented ratings/prices: OSM carries no review data, so these
                # fields are simply absent unless the map itself tags them.
                'description': osm_description,
                'opening_hours': tags.get('opening_hours'),
                'duration': 2,
                'address': address,
                'website': tags.get('website'),
                'metadata': {
                    'osm_type': _coerce_text(el.get('type')).lower(),
                    'osm_id': _coerce_text(el.get('id')),
                    'wikidata_id': tags.get('wikidata'),
                    'wikipedia': tags.get('wikipedia'),
                    'wikimedia_commons': tags.get('wikimedia_commons'),
                    'image': tags.get('image'),
                    'osm_tags': {k: v for k, v in tags.items() if k not in ['name', 'name:en']}
                },
                'verified': True,
                'image_url': None
            }
            real_rating = _parse_rating(tags.get('rating'))
            if real_rating is not None:
                poi['rating'] = real_rating
            # `fee=yes` states only that money is charged - it is not a price.
            raw_fee = tags.get('fee')
            if raw_fee and str(raw_fee).strip().lower() not in ('yes', 'no'):
                poi['entry_fee'] = raw_fee
            elif raw_fee:
                poi['fee_indicated'] = str(raw_fee).strip().lower()
            poi = {k: v for k, v in poi.items() if v is not None}
            normalized.append(poi)
            
        return normalized

    def _fetch_osm_accommodations(self, lat, lng, radius, budget, number):
        import math
        earth_radius = 6371000
        radius = min(radius, 15000)
        lat_delta = (radius / earth_radius) * (180 / math.pi)
        lng_delta = (radius / earth_radius) * (180 / math.pi) / math.cos(lat * math.pi / 180)
        s = lat - lat_delta
        w = lng - lng_delta
        n = lat + lat_delta
        e = lng + lng_delta
        bbox = f"{s},{w},{n},{e}"
        
        if budget == 'budget':
            acc_type = "hostel|guest_house|motel"
        elif budget == 'luxury':
            acc_type = "resort|hotel"
        else:
            acc_type = "hotel|apartment"

        query = f'[out:json][timeout:25];(node["tourism"~"{acc_type}"]({bbox}););out center;'
        
        elements = self._execute_overpass_query(query)
        accommodations = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            latitude = el.get('lat') or el.get('center', {}).get('lat')
            longitude = el.get('lon') or el.get('center', {}).get('lon')
            
            amenities = []
            if str(tags.get('internet_access', '')).strip().lower() not in ('', 'no'): amenities.append("Wi-Fi")
            if str(tags.get('wheelchair', '')).strip().lower() == 'yes': amenities.append("Wheelchair Accessible")
            if tags.get('swimming_pool') == 'yes': amenities.append("Pool")
            if tags.get('parking') in ('yes', 'private', 'public'): amenities.append("Parking")
            # A hotel with no tagged amenities has *unknown* amenities - not a
            # guessed "Wi-Fi + Air Conditioning" (that would mislead the traveller).

            real_acc_rating = _parse_rating(tags.get('rating'))

            accommodations.append({
                'id': f"osm_{el.get('id')}",
                'name': name,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Address not provided",
                'latitude': latitude,
                'longitude': longitude,
                'category': 'Accommodation',
                'price_per_night': None,
                'currency': None,
                'price_status': 'unavailable',
                'rating_status': 'unavailable' if real_acc_rating is None else 'reported_by_source',
                'amenities': amenities,
                'amenities_status': 'tagged' if amenities else 'unavailable',
                'image_url': self._fetch_pexels_image(f"{name} {tags.get('tourism', 'hotel')}"),
                'booking_url': tags.get('website', ''),
                'budget_tier': budget
            })
            if real_acc_rating is not None:
                accommodations[-1]['rating'] = real_acc_rating
            if len(accommodations) >= number:
                break
            
        return accommodations

    # -------------------------------------------------------------------------
    # GEOAPIFY API
    # -------------------------------------------------------------------------
    def _fetch_geoapify_attractions(self, lat, lng, radius, hobbies):
        """Geoapify Places API — comprehensive multi-category discovery.

        Previously requested only 4 broad categories with a limit of 50, which
        yielded around 10-14 unique attractions per city once OSM-side filtering
        was applied. Now requests the full spectrum of ExploreX-relevant
        categories, with the maximum permitted result window (Geoapify caps
        single call at 100 results), and merges the subcategories.
        """
        # Geoapify single-call categories ceiling is 100. Union the ExploreX-
        # relevant categories so we cover viewpoints, worship sites, gardens,
        # natural features, heritage, museums, markets and entertainment.
        # Geoapify rejects the ENTIRE request with HTTP 400 if *any* category
        # key is unknown, so we must use only names from Geoapify's official
        # taxonomy (verified against apidocs.geoapify.com). The previous list
        # mixed in non-Geoapify keys (heritage.*, tourism.museum,
        # amenity.place_of_worship, leisure.garden, natural.beach, ...) which
        # made every attraction call fail. Parent keys include their children,
        # so this compact set still spans viewpoints, heritage/monuments,
        # museums, galleries, aquariums, parks/gardens/nature reserves, water
        # features (incl. waterfalls), peaks/cliffs, beaches, places of worship
        # and national parks.
        categories = (
            "tourism.attraction,tourism.sights,"
            "entertainment.museum,entertainment.culture.gallery,entertainment.aquarium,"
            "leisure.park,"
            "natural.water,natural.mountain,natural.coastal,"
            "religion.place_of_worship,national_park"
        )
        url = "https://api.geoapify.com/v2/places"
        params = {
            "categories": categories,
            "filter": f"circle:{lng},{lat},{min(radius, 50000)}",
            "limit": 100,
            "apiKey": self.geoapify_key
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except requests.exceptions.RequestException as e:
            safe_msg = str(e).replace(self.geoapify_key, "****") if self.geoapify_key else str(e)
            raise Exception(f"Geoapify Request Failed: {safe_msg}")
        except ValueError as e:
            raise Exception(f"Geoapify returned invalid JSON: {type(e).__name__}")

        raw_features = payload.get('features') if isinstance(payload, dict) else None
        features = raw_features if isinstance(raw_features, list) else []

        normalized = []
        for f in features:
            if not isinstance(f, dict):
                continue
            props = f.get('properties') if isinstance(f.get('properties'), dict) else {}
            name = _coerce_text(props.get('name'))
            if not name or name.isdigit():
                # A bare number is not a usable attraction name; keep the rest
                # of the city's results instead of crashing on it.
                continue

            cats = props.get('categories')
            cats = [str(c) for c in cats if c] if isinstance(cats, list) else []
            primary_cat = cats[0] if cats else "tourist_attraction"

            latitude = _coerce_float(props.get('lat'))
            longitude = _coerce_float(props.get('lon'))
            if latitude is None or longitude is None:
                continue

            poi = {
                'id': _coerce_text(props.get('place_id')) or f"geo_{latitude:.5f}_{longitude:.5f}",
                'source': 'geoapify',
                'name': name,
                'latitude': latitude,
                'longitude': longitude,
                'category': primary_cat,
                'subcategory': cats[1] if len(cats) > 1 else '',
                'address': props.get('formatted'),
                'website': props.get('website'),
                'opening_hours': props.get('opening_hours'),
                'duration': 2,
                'metadata': {},
                'verified': True,
                'image_url': None,
            }
            # Geoapify's Places response contains no review data, so rating and
            # price level are omitted entirely rather than guessed.
            rating_data = props.get('rating_data') if isinstance(props.get('rating_data'), dict) else {}
            geo_rating = _parse_rating(props.get('rating') or rating_data.get('rating'))
            if geo_rating is not None:
                poi['rating'] = geo_rating
                poi['rating_status'] = 'reported_by_source'

            # Preserve the OSM reference and the wiki/media tags Geoapify
            # already returned.  Without this the image resolver has to search
            # Wikipedia blindly by name; with it, the exact article/entity/file
            # (or the place's own OSM image tag) can be used instead.
            wiki_and_media = props.get('wiki_and_media')
            datasource = props.get('datasource')
            raw_tags = {}
            if isinstance(datasource, dict):
                raw_datasource = datasource.get('raw')
                if isinstance(raw_datasource, dict):
                    raw_tags = raw_datasource
            metadata = poi['metadata']
            if isinstance(raw_tags, dict):
                for source_key, target_key in (
                    ('wikidata', 'wikidata_id'),
                    ('wikipedia', 'wikipedia'),
                    ('wikimedia_commons', 'wikimedia_commons'),
                    ('image', 'image'),
                ):
                    value = _coerce_text(raw_tags.get(source_key))
                    if value:
                        metadata[target_key] = value
                osm_type = _coerce_text(raw_tags.get('osm_type')).lower()
                osm_id = _coerce_text(raw_tags.get('osm_id'))
                if osm_type in ('n', 'w', 'r', 'node', 'way', 'relation') and osm_id:
                    metadata['osm_type'] = osm_type
                    metadata['osm_id'] = osm_id
                # Keep only the fields the image resolver/UI can use; the raw
                # OSM blob is often large and adds nothing here.
                keep = {
                    'name', 'wikidata', 'wikipedia', 'wikimedia_commons', 'image',
                    'tourism', 'historic', 'amenity', 'leisure', 'religion',
                    'natural', 'building', 'description', 'description:en',
                }
                metadata['osm_tags'] = {
                    key: value for key, value in raw_tags.items()
                    if key in keep and value not in (None, '')
                }
            if isinstance(wiki_and_media, dict):
                for source_key, target_key in (
                    ('wikidata', 'wikidata_id'),
                    ('wikipedia', 'wikipedia'),
                    ('image', 'image'),
                ):
                    value = _coerce_text(wiki_and_media.get(source_key))
                    if value and target_key not in metadata:
                        metadata[target_key] = value

            geo_desc = _coerce_text(props.get('description'))
            if geo_desc and len(geo_desc) > 40:
                poi['description'] = geo_desc

            poi = {k: v for k, v in poi.items() if v is not None}
            normalized.append(poi)

        return normalized

    # -------------------------------------------------------------------------
    # WIKIPEDIA NOTABILITY LAYER
    # -------------------------------------------------------------------------
    def _fetch_wikipedia_attractions(self, lat, lng, radius, city=None):
        """Return notable, visitable places near ``lat``/``lng`` from Wikipedia.

        OSM and Geoapify describe *everything* that is mapped, which is why a
        Tokyo search could be dominated by neighbourhood parks and local
        community halls. Wikipedia only has an article for places someone
        considered notable, and it also gives us a verifiable description,
        thumbnail and article URL — none of which we invent.

        The same code path works for any destination; nothing here is specific
        to Japan. Two complementary discovery modes are used:

        * ``generator=search`` with the destination name, which surfaces the
          genuinely famous sights (the ones a traveller expects to see);
        * ``generator=geosearch`` at a few overlapping points, which catches
          notable places that do not rank in a text search.

        GeoSearch caps a single query at a 10 km radius, so a larger city is
        covered by several points instead of only its western half.
        """
        language = getattr(self, "wikipedia_language", "en") or "en"
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        search_radius = max(500, min(int(radius), 10000))
        headers = {"User-Agent": _WIKI_USER_AGENT, "Accept": "application/json"}

        def _query(params):
            """One serial MediaWiki call.

            Wikipedia is optional enrichment: it must never stall the planning
            request.  A 429 gets one short, throttled retry and then gives up so
            the caller keeps the provider results it already has.
            """
            last_error = None
            for attempt in range(2):
                _wiki_throttle()
                try:
                    response = requests.get(
                        endpoint, params=params, headers=headers, timeout=8
                    )
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            wait = float(retry_after)
                        except (TypeError, ValueError):
                            wait = 1.0
                        last_error = RuntimeError("HTTP 429")
                        if attempt == 0:
                            time.sleep(min(max(wait, 0.0), 1.5))
                            continue
                        print("[PROVIDER][WIKIPEDIA] Rate limited; abandoning optional enrichment.")
                        raise last_error
                    response.raise_for_status()
                    payload = response.json()
                    pages = ((payload.get("query") or {}).get("pages") or [])
                    if isinstance(pages, dict):  # formatversion=1 compatibility
                        pages = list(pages.values())
                    if not isinstance(pages, list):
                        return []
                    return [page for page in pages if isinstance(page, dict)]
                except requests.exceptions.RequestException as exc:
                    last_error = exc
                    if attempt == 0:
                        time.sleep(0.5)
            raise last_error or RuntimeError("Wikipedia request failed")

        common = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "coordinates|pageimages|extracts|pageprops|info|categories|description",
            "exintro": 1,
            "explaintext": 1,
            "exsentences": 3,
            "exlimit": "max",
            "piprop": "thumbnail",
            "pithumbsize": 800,
            "inprop": "url",
            "cllimit": 20,
            "ppprop": "wikibase_item|disambiguation",
        }

        pages = []
        seed_pages = []
        failures = 0

        # 1. Text search: the famous attractions for this destination.
        if city:
            # "Tokyo, Japan" and "Tokyo" must resolve to the same search term;
            # the region suffix only confuses the encyclopaedia's text search.
            city_name = str(city).split(',')[0].strip()
            search_params = dict(common)
            search_params.update({
                "generator": "search",
                "gsrsearch": f"tourist attractions in {city_name}",
                "gsrnamespace": 0,
                "gsrlimit": 40,
            })
            try:
                found = _query(search_params)
                seed_pages.extend(found)
            except Exception as exc:
                failures += 1
                print(f"[PROVIDER][WIKIPEDIA] Search for '{city_name}' failed: {type(exc).__name__}")

            # Wikipedia's "Tourist attractions in <city>" category is a curated
            # list of notable places. It is not guaranteed to exist for every
            # destination, so a miss is harmless and simply adds no pages.
            category_params = dict(common)
            category_params.update({
                "generator": "categorymembers",
                "gcmtitle": f"Category:Tourist attractions in {city_name}",
                "gcmlimit": 50,
                "gcmtype": "page",
            })
            try:
                seed_pages.extend(_query(category_params))
            except Exception as exc:
                failures += 1
                print(f"[PROVIDER][WIKIPEDIA] Category for '{city_name}' failed: {type(exc).__name__}")

        pages.extend(seed_pages)

        # 2. GeoSearch around the places the text search already found. Their
        #    coordinates are reliable seeds even when the page itself is an area
        #    (e.g. "Asakusa") rather than a single attraction, and a geo query
        #    around them surfaces the actual temples/shrines/museums nearby.
        points = []
        for page in seed_pages:
            if not isinstance(page, dict):
                continue
            coords = page.get("coordinates") or []
            if not isinstance(coords, list) or not coords or not isinstance(coords[0], dict):
                continue
            seed_lat = _coerce_float(coords[0].get("lat"))
            seed_lng = _coerce_float(coords[0].get("lon"))
            if seed_lat is None or seed_lng is None:
                continue
            if _haversine_km(lat, lng, seed_lat, seed_lng) > (radius / 1000.0) * 1.25:
                continue
            # Keep the seeds spread out so four queries cover the city instead
            # of repeatedly querying the same block.
            if all(_haversine_km(seed_lat, seed_lng, p[0], p[1]) > 2.0 for p in points):
                points.append((seed_lat, seed_lng))
            if len(points) >= 4:
                break

        if not points:
            # No usable seed (unknown city / no search results): fall back to
            # the centre plus four cardinal points.
            points = [(lat, lng)]
            if radius > 10000:
                ring = min(max(radius * 0.5, 7000), 12000)
                lat_delta = ring / 111_320.0
                lng_delta = ring / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
                for bearing in (0, 90, 180, 270):
                    radians = math.radians(bearing)
                    points.append((
                        lat + lat_delta * math.cos(radians),
                        lng + lng_delta * math.sin(radians),
                    ))

        for index, (query_lat, query_lng) in enumerate(points):
            geo_params = dict(common)
            geo_params.update({
                "generator": "geosearch",
                "ggscoord": f"{query_lat:.5f}|{query_lng:.5f}",
                "ggsradius": search_radius,
                "ggslimit": 50,
            })
            try:
                pages.extend(_query(geo_params))
            except Exception as exc:
                failures += 1
                print(f"[PROVIDER][WIKIPEDIA] GeoSearch point {index} failed: {type(exc).__name__}")

        if not pages and failures:
            raise RuntimeError(f"all {failures} Wikipedia queries failed")

        results = []
        seen = set()
        seen_titles = set()
        for page in pages:
            if not isinstance(page, dict):
                continue
            title = _coerce_text(page.get("title"))
            if not title or _WIKI_EXCLUDE_TITLE.search(title) or _WIKI_TRAILING_STATION.search(title):
                continue

            page_id = page.get("pageid")
            key = _normalized_name(title)
            if not key or title in seen_titles or (page_id and page_id in seen):
                continue

            pageprops = page.get("pageprops") if isinstance(page.get("pageprops"), dict) else {}
            if "disambiguation" in pageprops:
                continue

            categories = [
                _coerce_text(c.get("title")) for c in (page.get("categories") or [])
                if isinstance(c, dict)
            ]
            short_description = _coerce_text(page.get("description"))
            joined_categories = " ".join(categories)
            evidence = f"{title} {short_description} {joined_categories}".lower()

            if _WIKI_EXCLUDE_EVIDENCE.search(evidence):
                continue
            # Wikipedia is a notability layer, not a dump of every geotagged
            # article: a visitable attraction must show tourism evidence.
            if not any(hint in evidence for hint in _WIKI_POSITIVE_HINTS):
                continue
            if title.lower().startswith("list of"):
                continue

            coords = page.get("coordinates") or []
            if not isinstance(coords, list) or not coords or not isinstance(coords[0], dict):
                continue
            latitude = _coerce_float(coords[0].get("lat"))
            longitude = _coerce_float(coords[0].get("lon"))
            if latitude is None or longitude is None:
                continue
            # Text search may return a same-named place elsewhere on earth.
            if _haversine_km(lat, lng, latitude, longitude) > (radius / 1000.0) * 1.25:
                continue

            seen.add(page_id)
            seen_titles.add(title)

            category = self._wikipedia_category(evidence, short_description)
            description = _coerce_text(page.get("extract")) or short_description
            if description:
                # Keep one compact paragraph; the UI renders plain text.
                description = re.sub(r"\s+", " ", description)[:600]

            thumbnail = page.get("thumbnail") if isinstance(page.get("thumbnail"), dict) else {}
            poi = {
                "id": f"wiki_{page_id or key}",
                "source": "wikipedia",
                "name": title,
                "latitude": latitude,
                "longitude": longitude,
                "category": category,
                "types": self._wikipedia_types(categories),
                "wikipedia_url": _coerce_text(page.get("fullurl")) or None,
                "description": description or None,
                "image_url": normalize_image_url(thumbnail.get("source")),
                "duration": 2,
                "verified": True,
                "metadata": {
                    "wikipedia_pageid": page_id,
                    "wikipedia_language": language,
                },
            }
            if short_description:
                poi["metadata"]["wikipedia_short_description"] = short_description
            wikidata_id = pageprops.get("wikibase_item")
            if wikidata_id:
                poi["metadata"]["wikidata_id"] = wikidata_id
            results.append({k: v for k, v in poi.items() if v is not None})

        return results

    @staticmethod
    def _wikipedia_category(category_blob, short_description):
        """Map Wikipedia categories/description onto ExploreX categories."""
        haystack = f"{category_blob} {short_description}".lower()
        for needle, category in _WIKI_CATEGORY_HINTS:
            if needle in haystack:
                return category
        return "tourist_attraction"

    @staticmethod
    def _wikipedia_types(categories):
        """Return the Wikipedia category names as structured type evidence."""
        types = []
        for value in categories or []:
            text = re.sub(r"^\s*category\s*:\s*", "", str(value), flags=re.IGNORECASE).strip()
            if text and text not in types:
                types.append(text)
        return types[:12]

    def _fetch_geoapify_accommodations(self, lat, lng, radius, budget, number):
        url = "https://api.geoapify.com/v2/places"
        
        if budget == 'budget':
            cat = "accommodation.hostel,accommodation.motel,accommodation.guest_house"
        elif budget == 'luxury':
            cat = "accommodation.hotel"
        else:
            cat = "accommodation.hotel,accommodation.apartment"

        params = {
            "categories": cat,
            "filter": f"circle:{lng},{lat},{radius}",
            "limit": max(10, number),
            "apiKey": self.geoapify_key
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            safe_msg = str(e).replace(self.geoapify_key, "****") if self.geoapify_key else str(e)
            raise Exception(f"Geoapify Request Failed: {safe_msg}")
        features = resp.json().get('features', [])
        
        accommodations = []
        for f in features:
            props = f.get('properties', {})
            if not props.get('name'): continue
            
            accommodations.append({
                'id': props.get('place_id', str(props.get('lat'))),
                'name': props.get('name'),
                'address': props.get('formatted', 'Address not provided'),
                'latitude': props.get('lat'),
                'longitude': props.get('lon'),
                'category': 'Accommodation',
                'price_per_night': None,
                'currency': None,
                'price_status': 'unavailable',
                'rating_status': 'unavailable',
                'amenities': [],
                'amenities_status': 'unavailable',
                'image_url': self._fetch_pexels_image(f"{props.get('name')} hotel"),
                'booking_url': props.get('website', ''),
                'budget_tier': budget
            })
            geo_acc_rating = _parse_rating(props.get('rating'))
            if geo_acc_rating is not None:
                accommodations[-1]['rating'] = geo_acc_rating
                accommodations[-1]['rating_status'] = 'reported_by_source'
            if len(accommodations) >= number:
                break
            
        return accommodations

    def _fetch_osm_restaurants(self, lat, lng, radius, number):
        import math
        earth_radius = 6371000
        radius = min(radius, 10000)
        lat_delta = (radius / earth_radius) * (180 / math.pi)
        lng_delta = (radius / earth_radius) * (180 / math.pi) / math.cos(lat * math.pi / 180)
        s = lat - lat_delta
        w = lng - lng_delta
        n = lat + lat_delta
        e = lng + lng_delta
        bbox = f"{s},{w},{n},{e}"
        
        query = f'[out:json][timeout:25];nwr["amenity"~"restaurant|cafe|fast_food|food_court"]({bbox});out center;'
        elements = self._execute_overpass_query(query)
        results = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            tags = el.get('tags') if isinstance(el.get('tags'), dict) else {}
            name = _coerce_text(tags.get('name') or tags.get('name:en'))
            if not name:
                continue

            raw_cuisine = _coerce_text(tags.get('cuisine')) or 'Local'
            cuisine = raw_cuisine.replace('_', ' ').title()
            amenity = _coerce_text(tags.get('amenity')) or 'restaurant'

            meal_types = []
            if 'cafe' in amenity:
                meal_types.extend(['breakfast', 'cafe'])
            elif 'fast_food' in amenity or 'street_food' in cuisine.lower():
                meal_types.extend(['lunch', 'street_food'])
            else:
                meal_types.extend(['lunch', 'dinner'])

            center = el.get('center') if isinstance(el.get('center'), dict) else {}
            results.append({
                'id': f"osm_rest_{el.get('id')}",
                'name': name,
                'cuisine': cuisine,
                'category': 'Food',
                'meal_types': meal_types,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Address not provided",
                'latitude': _coerce_float(el.get('lat', center.get('lat'))),
                'longitude': _coerce_float(el.get('lon', center.get('lon'))),
                'image_url': self._fetch_pexels_image(f"{name} food {cuisine}"),
                'description': f"{cuisine} {amenity.replace('_', ' ').title()}",
                'source': 'osm'
            })
            rest_rating = _parse_rating(tags.get('rating'))
            if rest_rating is not None:
                results[-1]['rating'] = rest_rating
            if len(results) >= number: break
        return results

    def _fetch_geoapify_restaurants(self, lat, lng, radius, number):
        url = f"https://api.geoapify.com/v2/places?categories=catering.restaurant,catering.cafe,catering.fast_food&filter=circle:{lng},{lat},{radius}&limit={number * 3}&apiKey={self.geoapify_key}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except requests.exceptions.RequestException:
            return []
        except ValueError:
            return []

        raw_features = payload.get('features') if isinstance(payload, dict) else None
        features = raw_features if isinstance(raw_features, list) else []
        results = []
        for feat in features:
            if not isinstance(feat, dict):
                continue
            props = feat.get('properties') if isinstance(feat.get('properties'), dict) else {}
            name = _coerce_text(props.get('name'))
            if not name:
                continue

            raw_cats = props.get('categories')
            cats = [str(c) for c in raw_cats if c] if isinstance(raw_cats, list) else []
            cat_str = ' '.join(cats).lower()

            meal_types = []
            if 'cafe' in cat_str or 'coffee' in cat_str:
                meal_types.extend(['breakfast', 'cafe'])
            elif 'fast_food' in cat_str or 'street_food' in cat_str:
                meal_types.extend(['lunch', 'street_food'])
            else:
                meal_types.extend(['lunch', 'dinner'])

            cuisine = (_coerce_text(props.get('cuisine')) or 'Local').replace('_', ' ').title()

            results.append({
                'id': f"geoapify_rest_{props.get('place_id')}",
                'name': name,
                'cuisine': cuisine,
                'category': 'Food',
                'meal_types': meal_types,
                'address': props.get('formatted', 'Address not provided'),
                'latitude': _coerce_float(props.get('lat')),
                'longitude': _coerce_float(props.get('lon')),
                'image_url': self._fetch_pexels_image(f"{name} food {cuisine}"),
                'description': cats[0].replace('catering.', '').replace('_', ' ').title() if cats else "Restaurant",
                'source': 'geoapify'
            })
            geo_rest_rating = _parse_rating(props.get('rating'))
            if geo_rest_rating is not None:
                results[-1]['rating'] = geo_rest_rating
            if len(results) >= number:
                break
        return results


_SHARED_POI_MANAGER = None
_SHARED_POI_MANAGER_LOCK = threading.Lock()


def get_shared_poi_manager():
    """Process-wide POIManager for request paths that have no session.

    Reusing one instance keeps the Overpass throttle, response cache and
    circuit breaker effective for guest users as well as logged-in workflows.
    """
    global _SHARED_POI_MANAGER
    if _SHARED_POI_MANAGER is None:
        with _SHARED_POI_MANAGER_LOCK:
            if _SHARED_POI_MANAGER is None:
                _SHARED_POI_MANAGER = POIManager()
    return _SHARED_POI_MANAGER
