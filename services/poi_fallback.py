import os
import random
import requests
import threading
import time
import math
import hashlib


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

class POIManager:
    def __init__(self):
        self.geoapify_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
        # No hard-coded key: a secret committed into the repo is not a fallback,
        # it is a leak. Images simply stay unset when Pexels is not configured.
        self.pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
        self._cache = {} # In-memory cache for POIs
        # Serialises Overpass traffic and enforces a minimum interval between
        # calls so a burst of itinerary requests cannot trip the public mirrors.
        self._overpass_lock = threading.Lock()
        self._overpass_last_call = 0.0
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
        if not hasattr(self, "_overpass_min_interval"):
            try:
                self._overpass_min_interval = float(os.environ.get("OVERPASS_MIN_INTERVAL_SECONDS", "2"))
            except (TypeError, ValueError):
                self._overpass_min_interval = 2.0
        if not hasattr(self, "_provider_health"):
            self._provider_health = {}
        if not hasattr(self, "_last_provider_status"):
            self._last_provider_status = {}

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

    def get_attractions(self, lat: float, lng: float, radius: int = 20000, hobbies: str = None, poi_type: str = "tourist_attraction", retrieved_rag_pois=None, min_results: int = 30):
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
        provider_status = {"geoapify": "skipped", "overpass": "skipped"}
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

        # 3. Soft second pass — if both providers were thin, widen the radius
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
            if poi.get('metadata', {}).get('wikidata_id'):
                score += 100
            name = poi.get('name', '').lower()
            if 'viewpoint' in name or 'point de vue' in name:
                score -= 30
            if 'gate' in name or 'entrance' in name or 'parking' in name:
                score -= 50
            score -= (len(name) * 0.1)
            if poi.get('source') == 'osm':
                score += 20
            if poi.get('source') == 'geoapify':
                score += 10
            return score
            
        grouped_pois = []
        for poi in pois:
            name = poi.get('name', '').strip().lower()
            if not name:
                continue
            try:
                lat = float(poi.get('latitude', 0))
                lng = float(poi.get('longitude', 0))
            except (ValueError, TypeError):
                continue
            if lat == 0 and lng == 0:
                continue
                
            matched_group_idx = -1
            for i, group in enumerate(grouped_pois):
                for seen_poi in group:
                    seen_name = seen_poi.get('name', '').strip().lower()
                    try:
                        seen_lat = float(seen_poi.get('latitude', 0))
                        seen_lng = float(seen_poi.get('longitude', 0))
                    except (ValueError, TypeError):
                        continue
                    
                    id_match = (poi.get('id') and poi.get('id') == seen_poi.get('id'))
                    name_match = (name in seen_name and len(name) > 5) or (seen_name in name and len(seen_name) > 5) or (name == seen_name)
                    dist = calculate_distance(lat, lng, seen_lat, seen_lng)
                    dist_match = dist < 0.5  # 500 meters for similar names
                    duplicate_loc = dist < 0.01 # 10 meters for duplicate locations regardless of name
                    
                    if id_match or (name_match and dist_match) or duplicate_loc:
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
                if not best_poi.get('metadata', {}).get('wikidata_id') and other_poi.get('metadata', {}).get('wikidata_id'):
                    if 'metadata' not in best_poi:
                        best_poi['metadata'] = {}
                    best_poi['metadata']['wikidata_id'] = other_poi['metadata']['wikidata_id']
                if not best_poi.get('address') and other_poi.get('address'):
                    best_poi['address'] = other_poi['address']
            
            # Reconstruct location field for backward compatibility
            best_poi['location'] = {'lat': best_poi['latitude'], 'lng': best_poi['longitude']}
            deduplicated.append(best_poi)
            
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

    def _execute_overpass_query(self, query: str):
        headers = {
            "User-Agent": os.environ.get("OVERPASS_USER_AGENT", "ExploreX-Travel-App/1.0 (contact: app owner)"),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # Public Overpass mirrors are frequently rate-limited (429/502) or slow.
        # A client read timeout SHORTER than the server-side [timeout:NN] made us
        # abandon queries that were about to succeed. Read a generous timeout from
        # env (default 25s, matching the server hint) and cap retries at 3 so the
        # worst-case blocking time per query stays bounded; cooled-down endpoints
        # are skipped so follow-up queries in the same request fail fast. A failed
        # Overpass round is NEVER fatal - callers keep whatever Geoapify returned.
        max_retries = 3
        try:
            read_timeout = float(os.environ.get("OVERPASS_TIMEOUT", "25"))
        except (TypeError, ValueError):
            read_timeout = 25.0
        try:
            connect_timeout = float(os.environ.get("OVERPASS_CONNECT_TIMEOUT", "5"))
        except (TypeError, ValueError):
            connect_timeout = 5.0
        
        if not hasattr(self, 'overpass_cooldowns'):
            self.overpass_cooldowns = {}
            
        now = time.time()
        available_endpoints = [e for e in self.overpass_endpoints if now > self.overpass_cooldowns.get(e, 0)]
        
        if not available_endpoints:
            print("[PROVIDER][OVERPASS][WARN] All endpoints in cooldown. Failing fast (non-fatal).")
            self._record_status("overpass", "cooldown", "all mirrors cooling down")
            return []

        last_exception = None
        for attempt in range(max_retries):
            endpoint = available_endpoints[attempt % len(available_endpoints)]
            try:
                resp = self._throttled_post(endpoint, query, headers, connect_timeout, read_timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    backoff = self._retry_after_seconds(resp, (2 ** attempt) + random.uniform(0, 1))
                    print(f"[PROVIDER][OVERPASS][WARN] Recoverable error {resp.status_code} on {endpoint}. "
                          f"Backing off {backoff:.1f}s.")
                    self.overpass_cooldowns[endpoint] = time.time() + max(30, backoff)
                    # Try a DIFFERENT mirror on the next attempt rather than hammering
                    # the endpoint that just pushed us back.
                    available_endpoints = available_endpoints[1:] + available_endpoints[:1] if len(available_endpoints) > 1 else available_endpoints
                    time.sleep(min(backoff, 10))
                    last_exception = Exception(f"HTTP {resp.status_code}")
                    continue
                    
                resp.raise_for_status()
                elements = resp.json().get('elements', [])
                self._record_status("overpass", "ok", f"{len(elements)} raw elements")
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
        h = hobbies.lower() if hobbies else ""

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
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            if tags.get('amenity') in ['bank', 'atm', 'hospital', 'clinic', 'dentist', 'pharmacy', 'police', 'post_office', 'waste_basket', 'vending_machine', 'parking', 'fuel', 'taxi']:
                continue
            if tags.get('shop') or tags.get('office') or tags.get('highway'):
                continue
            
            el_type = tags.get('tourism') or tags.get('historic') or tags.get('amenity') or tags.get('leisure') or "attraction"
            
            addr_parts = [tags.get('addr:housenumber', ''), tags.get('addr:street', ''), tags.get('addr:city', ''), tags.get('addr:postcode', '')]
            address = ' '.join(p for p in addr_parts if p).strip() or tags.get('addr:full', '') or None

            osm_description = tags.get('description') or tags.get('description:en') or None
            
            latitude = el.get('lat') or el.get('center', {}).get('lat')
            longitude = el.get('lon') or el.get('center', {}).get('lon')

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
                    'wikidata_id': tags.get('wikidata'),
                    'wikipedia': tags.get('wikipedia'),
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
        except requests.exceptions.RequestException as e:
            safe_msg = str(e).replace(self.geoapify_key, "****") if self.geoapify_key else str(e)
            raise Exception(f"Geoapify Request Failed: {safe_msg}")
        features = resp.json().get('features', [])
        
        normalized = []
        for f in features:
            props = f.get('properties', {})
            if not props.get('name'): continue
            
            cats = props.get('categories', [])
            primary_cat = cats[0] if cats else "tourist_attraction"

            latitude = props.get('lat')
            longitude = props.get('lon')

            poi = {
                'id': props.get('place_id', str(latitude)),
                'source': 'geoapify',
                'name': props.get('name'),
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
            geo_rating = _parse_rating(props.get('rating') or (props.get('rating_data') or {}).get('rating'))
            if geo_rating is not None:
                poi['rating'] = geo_rating
                poi['rating_status'] = 'reported_by_source'
            
            wikidata_id = None
            if 'wiki_and_media' in props and 'wikidata' in props['wiki_and_media']:
                wikidata_id = props['wiki_and_media']['wikidata']
            elif 'datasource' in props and 'raw' in props['datasource'] and 'wikidata' in props['datasource']['raw']:
                wikidata_id = props['datasource']['raw']['wikidata']
            if wikidata_id:
                poi['metadata']['wikidata_id'] = wikidata_id
                
            geo_desc = props.get('description', '')
            if geo_desc and len(geo_desc) > 40:
                poi['description'] = geo_desc

            poi = {k: v for k, v in poi.items() if v is not None}
            normalized.append(poi)
            
        return normalized

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
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            cuisine = tags.get('cuisine', 'Local').replace('_', ' ').title()
            amenity = tags.get('amenity', 'restaurant')
            
            meal_types = []
            if 'cafe' in amenity:
                meal_types.extend(['breakfast', 'cafe'])
            elif 'fast_food' in amenity or 'street_food' in cuisine.lower():
                meal_types.extend(['lunch', 'street_food'])
            else:
                meal_types.extend(['lunch', 'dinner'])
                
            results.append({
                'id': f"osm_rest_{el.get('id')}",
                'name': name,
                'cuisine': cuisine,
                'category': 'Food',
                'meal_types': meal_types,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Address not provided",
                'latitude': el.get('lat') or el.get('center', {}).get('lat'),
                'longitude': el.get('lon') or el.get('center', {}).get('lon'),
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
            features = resp.json().get('features', [])
            results = []
            for feat in features:
                props = feat.get('properties', {})
                name = props.get('name')
                if not name: continue
                
                cats = props.get('categories', [])
                cat_str = ' '.join(cats).lower()
                
                meal_types = []
                if 'cafe' in cat_str or 'coffee' in cat_str:
                    meal_types.extend(['breakfast', 'cafe'])
                elif 'fast_food' in cat_str or 'street_food' in cat_str:
                    meal_types.extend(['lunch', 'street_food'])
                else:
                    meal_types.extend(['lunch', 'dinner'])
                
                cuisine = props.get('cuisine', 'Local').replace('_', ' ').title()
                
                results.append({
                    'id': f"geoapify_rest_{props.get('place_id')}",
                    'name': name,
                    'cuisine': cuisine,
                    'category': 'Food',
                    'meal_types': meal_types,
                    'address': props.get('formatted', 'Address not provided'),
                    'latitude': props.get('lat'),
                    'longitude': props.get('lon'),
                    'image_url': self._fetch_pexels_image(f"{name} food {cuisine}"),
                    'description': cats[0].replace('catering.', '').replace('_', ' ').title() if cats else "Restaurant",
                    'source': 'geoapify'
                })
                geo_rest_rating = _parse_rating(props.get('rating'))
                if geo_rest_rating is not None:
                    results[-1]['rating'] = geo_rest_rating
                if len(results) >= number: break
            return results
        except Exception:
            return []
