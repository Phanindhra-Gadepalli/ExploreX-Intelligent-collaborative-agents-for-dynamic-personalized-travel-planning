import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import os
import sys
import json
import math
import hashlib
import googlemaps
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Add the parent directory to sys.path to allow imports from services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Removed services.maps_api import
from services.weather_api import WeatherService
from services.car_rental_api import CarRentalService
from services.fuel_price_api import get_gas_price
from services.geocoding import shared_geocoder as _shared_geocoder
from services.interest_match import (
    attraction_text_pool,
    interest_score as _interest_component,
    interests_from_preferences,
    match_interests as _match_interests,
    normalize_interests,
)
from services.attraction_images import enrich_poi_images, normalize_image_url
from services.destination_resolver import (
    resolve_destination,
    validate_point_for_scope,
    within_country,
    haversine_km,
)
from services.structured_parse import (
    records_from_text,
    clamp_rating,
    clean_name,
)

def format_duration(seconds):
    """Format duration in seconds to a human-readable string (hours and minutes)."""
    if seconds is None:
        return "N/A"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    duration_str = ""
    if hours > 0:
        duration_str += f"{hours} hour{'s' if hours > 1 else ''} "
    if minutes > 0:
        duration_str += f"{minutes} min{'s' if minutes > 1 else ''}"
    if not duration_str: # Handle cases less than a minute
         duration_str = f"{sec} sec{'s' if sec > 1 else ''}"
    return duration_str.strip()

# Helper function for formatting distance
def format_distance(meters):
    """Format distance in meters to a string with kilometers and miles."""
    if meters is None:
        return "N/A"
    km = meters / 1000.0
    miles = meters / 1609.34
    return f"{km:.1f} km / {miles:.1f} miles"

class InformationAgent:
    # Public OSRM demo server: fail fast instead of hanging the itinerary.
    OSRM_TIMEOUT_SECONDS = float(os.getenv("OSRM_TIMEOUT_SECONDS", "6"))

    def __init__(self, car_api_key=None, llm_model_name="gemini-flash-lite-latest"):
        """Initialize the InformationAgent with API keys and LLM model name."""
        from services.weather_api import WeatherService
        from services.poi_fallback import POIManager
        from dotenv import load_dotenv
        
        # Load environment variables
        load_dotenv()
        
        self.poi_manager = POIManager()
        # Multi-provider geocoding cascade: Geoapify → Nominatim → Photon → Static
        # Reuse the module-level geocoder singleton (created once per process)
        self.geocoder = _shared_geocoder
        self.weather_service = WeatherService()
        self.car_rental_service = None
        self.rapidapi_key = car_api_key or os.getenv("RAPIDAPI_KEY")

        if self.rapidapi_key and self.rapidapi_key != "YOUR_RAPIDAPI_KEY" and len(self.rapidapi_key) >= 30:
            try:
                self.car_rental_service = CarRentalService(rapidapi_key=self.rapidapi_key)
            except ValueError as e:
                print(f"Error initializing CarRentalService: {e}. Car rental will use mock data.")
                self.car_rental_service = None
        else:
            print("RAPIDAPI_KEY not configured correctly for CarRentalService. Car rental will use mock data.")

        try:
            self.llm = ChatGoogleGenerativeAI(model=llm_model_name, temperature=0.5)
        except Exception as e:
            print(f"Error initializing LLM ({llm_model_name}): {e}. LLM-dependent features might not work.")
            self.llm = None

        self.weather_summary_writer = self.llm 
        self.llm_rerank_cache = {}
        self.last_discovery_status = {}
        self.last_image_status = {"state": "idle"}

    def _get_rerank_cache_key(self, user_prefs, attractions_ids_tuple, weather_summary):
        """Generate a cache key for LLM re-ranking based on user preferences, attraction IDs, and weather."""
        prefs_str = json.dumps(user_prefs, sort_keys=True)
        ids_str = json.dumps(attractions_ids_tuple, sort_keys=True)
        weather_str = weather_summary if weather_summary else ""
        hash_object = hashlib.sha256(f"{prefs_str}-{ids_str}-{weather_str}".encode())
        return hash_object.hexdigest()

    def _create_llm_rerank_prompt(self, user_prefs, attractions_for_llm, weather_summary):
        """Create a prompt for the LLM to re-rank attractions."""
        attractions_str = json.dumps(attractions_for_llm, indent=2, ensure_ascii=False)
        user_prefs_str = json.dumps(user_prefs, indent=2, ensure_ascii=False)
        weather_str = weather_summary if weather_summary else "No specific weather summary provided."

        prompt = f"""
        You are an expert travel recommender. Your task is to rank the provided list of attractions
        based on the user's preferences, attraction details, and current weather summary.

        User Preferences:
        {user_prefs_str}

        Weather Summary for the trip period:
        {weather_str}

        Attractions List:
        {attractions_str}

        Please evaluate each attraction considering:
        1.  **User Hobbies & Interests**: E.g., '{user_prefs.get('hobbies', 'general sightseeing')}'.
        2.  **Tourist Importance**: Is this a major, must-see landmark or a minor incidental object?
        3.  **Data Quality**: Penalize completely irrelevant entries (like generic offices or banks).
        4.  **Weather & Health Constraints**.

        CRITICAL: Completely avoid and remove any "travel agencies", "tour operators", "food courts", "restaurants", "ATMs", "banks", or "booking services".

        Return a JSON array of objects. Each object MUST have:
        - "id": The exact ID of the attraction.
        - "relevance_score": An integer from 0 to 100 based on overall relevance.
        - "interest_match": An array of matched interest strings (e.g., ["heritage", "sightseeing"]).
        - "tourist_value": A string: "high", "medium", or "low".

        Output MUST be a valid JSON array. Example:
        [
            {{
                "id": "osm_12345",
                "relevance_score": 95,
                "interest_match": ["heritage", "sightseeing"],
                "tourist_value": "high"
            }}
        ]

        Only return the JSON array. No other text.
        """
        return prompt

    def _heuristic_interest_match(self, attr, user_prefs):
        """Return the canonical interest groups matched by this attraction.

        Delegates to :mod:`services.interest_match`, which understands
        multilingual names, OSM tags and category metadata.  The old English
        name-only keyword list is intentionally gone: it produced zero matches
        for "viewpoints and photography" in Japan even when the records carried
        ``tourism=viewpoint``.
        """
        matched, reasons = _match_interests(attr, user_prefs or {})
        attr["interest_match_reasons"] = reasons
        # Kept for backwards compatibility with callers that read the weights.
        requested = interests_from_preferences(user_prefs or {})
        attr["_interest_weights"] = dict(requested)
        return matched

    def _evaluate_attraction_quality(self, attr):
        """Evaluate base data quality to filter out incidental OSM nodes."""
        a_name = str(attr.get('name', '')).lower()
        a_category = str(attr.get('category', '')).lower()

        # Extremely low quality or irrelevant entities
        low_value_keywords = ['bank', 'atm', 'hospital', 'clinic', 'pharmacy', 'dentist', 'office', 'company', 'factory', 'industrial', 'road junction', 'traffic', 'parking', 'supermarket', 'grocery', 'travel agency']

        if any(k in a_category or k in str(attr.get('types', [])).lower() for k in low_value_keywords):
            return "low"

        if any(f"amenity': '{k}'" in str(attr.get('osm_tags', {})) for k in low_value_keywords):
            return "low"

        return "medium"

    def _calculate_score(self, attr, user_prefs, llm_score=0):
        """Calculate a transparent 0-100 relevance score.

        The score is deliberately split into auditable components:

        * interest    0-40  (deterministic, evidence-based)
        * quality     0-20  (real provider metadata only)
        * popularity  0-15  (real rating/review count only)
        * completeness 0-15 (description/coordinates/source)
        * LLM review  0-10  (advisory only; never authoritative)

        A strong interest mismatch cannot be rescued by popularity: an
        attraction with no matched interest receives 0 interest points.
        """
        quality = self._evaluate_attraction_quality(attr)
        if quality == "low":
            return -1, []

        matched_groups, reasons = _match_interests(attr, user_prefs or {})
        attr["interest_match_reasons"] = reasons
        attr["matched_interests"] = matched_groups
        attr["_interest_weights"] = dict(interests_from_preferences(user_prefs or {}))

        # 1. Interest relevance (0-40)
        interest_points, _, _ = _interest_component(attr, user_prefs or {})

        # 2. Data quality (0-20) — only facts the provider supplied.
        quality_points = 8.0
        metadata = attr.get("metadata") if isinstance(attr.get("metadata"), dict) else {}
        if metadata.get("wikidata_id") or metadata.get("wikipedia") or attr.get("wikidata_id"):
            quality_points += 6.0
        if attr.get("source") in ("geoapify", "google", "rag", "web_rag", "openstreetmap", "overpass"):
            quality_points += 2.0
        if attr.get("source") == "wikipedia" or attr.get("wikipedia_url"):
            # A Wikipedia article is verifiable notability, not a popularity
            # estimate. It outranks an unmapped-by-anyone OSM node.
            quality_points += 4.0
        if attr.get("description") and len(str(attr["description"])) > 20:
            quality_points += 4.0
        quality_points = min(20.0, quality_points)

        # 3. Verified popularity (0-15) — no invented rating.
        popularity_points = 0.0
        rating = attr.get("rating")
        if isinstance(rating, (int, float)) and 0 < float(rating) <= 5:
            popularity_points += (float(rating) / 5.0) * 10.0
        review_count = attr.get("review_count") or attr.get("user_ratings_total")
        if isinstance(review_count, (int, float)) and review_count > 0:
            # log10(1000) = 3 -> max 5 points; huge review counts do not dominate.
            popularity_points += min(5.0, math.log10(float(review_count) + 1) * 1.25)
        popularity_points = min(15.0, popularity_points)

        # 4. Completeness (0-15)
        completeness = 0.0
        if attr.get("name"):
            completeness += 3.0
        loc = attr.get("location") if isinstance(attr.get("location"), dict) else {}
        if loc.get("lat") is not None and loc.get("lng") is not None:
            completeness += 5.0
        if attr.get("address"):
            completeness += 2.0
        if attr.get("image_url"):
            completeness += 1.0
        if attr.get("tags") or attr.get("osm_tags") or attr.get("types"):
            completeness += 4.0
        completeness = min(15.0, completeness)

        # 5. LLM advisory (0-10) — clamped, never the whole score.
        llm_points = 0.0
        if isinstance(llm_score, (int, float)):
            llm_points = max(0.0, min(10.0, float(llm_score) / 10.0))

        score = interest_points + quality_points + popularity_points + completeness + llm_points
        match_percentage = int(round(min(100.0, max(0.0, score))))
        attr["match_percentage"] = match_percentage
        attr["score_components"] = {
            "interest": round(interest_points, 2),
            "quality": round(quality_points, 2),
            "popularity": round(popularity_points, 2),
            "completeness": round(completeness, 2),
            "llm": round(llm_points, 2),
        }
        return match_percentage, matched_groups

    def _rerank_attractions_with_llm(self, attractions_list: list, user_prefs: dict, weather_summary: str = None):
        """Re-rank attractions using an LLM based on user preferences and weather."""
        if not attractions_list:
            return []

        # 1. Ask LLM for scores
        llm_results_map = {}
        if self.llm and user_prefs:
            attractions_for_llm = []
            for attr in attractions_list:
                attractions_for_llm.append({
                    "id": attr.get("id"), "name": attr.get("name"), "category": attr.get("category"),
                    "description": attr.get("description", attr.get("name","No description available.")),
                })
            
            attraction_ids_tuple = tuple(sorted([str(attr.get('id') or '') for attr in attractions_for_llm]))
            cache_key = self._get_rerank_cache_key(user_prefs, attraction_ids_tuple, weather_summary)

            if cache_key in self.llm_rerank_cache:
                print(f"Returning cached LLM re-ranking for key: {cache_key}")
                llm_parsed = self.llm_rerank_cache[cache_key]
            else:
                prompt_str = self._create_llm_rerank_prompt(user_prefs, attractions_for_llm, weather_summary)
                messages = [
                    SystemMessage(content="You are an expert travel recommender. Follow formatting strictly."),
                    HumanMessage(content=prompt_str)
                ]
                try:
                    response = self.llm.invoke(messages)
                    llm_parsed = records_from_text(getattr(response, "content", response))
                    self.llm_rerank_cache[cache_key] = llm_parsed
                except Exception as e:
                    print(f"[INFO_AGENT_LLM_ERROR] LLM parsing failed: {e}")
                    llm_parsed = []
                    
            if isinstance(llm_parsed, (list, dict)):
                items = llm_parsed if isinstance(llm_parsed, list) else [llm_parsed]
                for item in items:
                    if isinstance(item, dict) and "id" in item:
                        llm_results_map[str(item["id"])] = item

        # 2. Compute final deterministic scores
        scored_attractions = []
        requested_interests = interests_from_preferences(user_prefs or {})
        for attr in attractions_list:
            llm_info = llm_results_map.get(str(attr.get("id")), {})
            llm_score = llm_info.get("relevance_score", 0)
            if isinstance(llm_score, str) and llm_score.strip().isdigit():
                llm_score = int(llm_score)
            if not isinstance(llm_score, (int, float)):
                llm_score = 0
            
            final_score, matched_groups = self._calculate_score(attr, user_prefs, llm_score)
            attr["_internal_score"] = final_score
            attr["matched_interests"] = matched_groups

            # The model's "tourist_value" is advisory only and cannot rescue a
            # genuine interest mismatch; _calculate_score already clamps the
            # whole LLM contribution to 10 points.
            metadata = attr.get("metadata") if isinstance(attr.get("metadata"), dict) else {}
            notable = bool(
                attr.get("source") == "wikipedia"
                or attr.get("wikipedia_url")
                or metadata.get("wikidata_id")
                or attr.get("wikidata_id")
            )
            if not requested_interests:
                attr["recommendation_type"] = "discovery"
            elif matched_groups:
                attr["recommendation_type"] = "interest_based"
            elif notable:
                # A verifiable notable place that does not match a hobby is a
                # legitimate *discovery* recommendation, not a discarded
                # "outside_interests" record.  This keeps the Explore grid
                # diverse without misrepresenting the interest match.
                attr["recommendation_type"] = "discovery"
            else:
                attr["recommendation_type"] = "outside_interests"
            scored_attractions.append(attr)

        # 3. Sort by score
        scored_attractions.sort(key=lambda x: x["_internal_score"], reverse=True)
        
        # 4. Filter out negative scores (low quality)
        final_list = [a for a in scored_attractions if a["_internal_score"] >= 0]
        
        # If we filtered everything, fallback to original but limit damage
        if not final_list:
            final_list = scored_attractions

        # No arbitrary top-50 truncation.  Discovery breadth is set by the
        # caller's duration-aware target; ranking must not silently throw away
        # verified places (a 5-day Kerala trip lost two thirds of its pool
        # here before).
        print(f"[INFO_AGENT_LLM] Re-ranked list size: {len(final_list)}")
        
        # 5. Print a quick debug summary
        print(f"\\n[RECOMMENDATION SCORE DEBUG] Top 5 recommendations:")
        for attr in final_list[:5]:
            print(f"  Name: {attr.get('name')}")
            print(f"  Matched Interests: {attr.get('matched_interests')}")
            print(f"  Score: {attr.get('_internal_score')}\\n")
            
        return final_list

    def validate_indian_location(self, location_name: str):
        """
        Determine whether a given location is in India.

        Uses a multi-provider geocoding cascade (Google Maps → Geoapify → Nominatim
        → Photon → static lookup table) so that a single-provider failure such as
        Google Maps REQUEST_DENIED cannot produce an incorrect classification.

        Returns:
            True  — confirmed Indian destination
            False — confirmed international destination
            None  — ALL geocoding providers unavailable or city not found;
                    the caller MUST NOT interpret this as "not India".
        """
        result = self.geocoder.geocode(location_name)

        if result is None:
            # Every provider (including the static fallback) returned nothing.
            # Return None so the caller uses its own tiebreaker rather than
            # silently classifying an unknown city as international.
            print(
                f"[GEOCODER] All providers unavailable for '{location_name}'. "
                f"Returning None — caller must not classify this as international."
            )
            return None

        is_indian = result.is_in_india()
        label = "Indian" if is_indian else "International"
        print(
            f"[GEOCODER] validate_indian_location('{location_name}') → {label} "
            f"(provider={result.provider}, cc='{result.country_code}', "
            f"lat={result.lat:.4f}, lng={result.lng:.4f}"
            + (", cached" if result.cached else "") + ")"
        )
        return is_indian

    def city2geocode(self, city: str, expected_country: str = None):
        """
        Convert a city name to geographic coordinates.

        Uses the multi-provider geocoding cascade:
            Google Maps → Geoapify → Nominatim → Photon → static lookup table.

        Returns:
            dict with 'lat' and 'lng' keys (same format as before), or None
            if every provider fails.
        """
        result = self.geocoder.geocode(city, expected_country=expected_country)
        if result is not None:
            cached_label = " [cached]" if result.cached else ""
            print(
                f"[GEOCODER] city2geocode('{city}') → "
                f"lat={result.lat:.4f}, lng={result.lng:.4f} "
                f"via {result.provider}{cached_label}"
            )
            return {"lat": result.lat, "lng": result.lng}

        print(f"[GEOCODER] city2geocode('{city}') → None (all providers failed)")
        return None

    def _country_code_for(self, city_name: str) -> str:
        """Best-effort ISO country code for a destination (never a guess)."""
        resolved = getattr(self, "resolved_destination", None)
        if resolved is not None and getattr(resolved, "country_code", ""):
            return str(resolved.country_code).lower()
        try:
            result = self.geocoder.geocode(city_name)
            if result is not None and result.country_code:
                return str(result.country_code).lower()
        except Exception:
            pass
        return ""

    def _validate_generated_pois(self, pois, city, dest_lat, dest_lng,
                                 country_code: str = "", max_km: float = 80.0):
        """Geocode model-suggested POIs and keep only real, in-scope places.

        Returns ``(validated, dropped)``.  A generated POI without a verified
        coordinate is never presented as a routable attraction.
        """
        validated = []
        dropped = []
        for poi in pois or []:
            name = clean_name(poi.get("name"))
            if not name:
                continue
            query = f"{name}, {city}".strip(", ")

            # Already geocoded by a previous stage? Validate it instead of
            # calling the provider again.
            existing_lat = self._coerce_coord(
                poi.get("latitude", poi.get("lat"))
            )
            existing_lng = self._coerce_coord(
                poi.get("longitude", poi.get("lng"))
            )
            if existing_lat is not None and existing_lng is not None:
                in_scope = True
                if dest_lat is not None and dest_lng is not None:
                    in_scope = haversine_km(dest_lat, dest_lng, existing_lat, existing_lng) <= max_km
                if in_scope:
                    poi.update({
                        "location": {"lat": existing_lat, "lng": existing_lng},
                        "latitude": existing_lat,
                        "longitude": existing_lng,
                        "coords_verified": True,
                        "verified": False,
                        "verification_source": "reported",
                    })
                    validated.append(poi)
                else:
                    dropped.append({"name": name, "reason": "outside_city"})
                continue

            result = None
            try:
                result = self.geocoder.geocode(query, expected_country=country_code or None)
            except Exception as exc:
                print(f"[INFO_AGENT] Geocode failed for generated POI '{query}': {exc}")

            if result is None:
                dropped.append({"name": name, "reason": "geocode_failed"})
                continue

            lat, lng = float(result.lat), float(result.lng)
            result_cc = str(getattr(result, "country_code", "") or "").lower()
            if country_code and result_cc and result_cc != country_code.lower():
                dropped.append({"name": name, "reason": f"country_mismatch:{result_cc}"})
                continue
            if dest_lat is not None and dest_lng is not None:
                distance = haversine_km(dest_lat, dest_lng, lat, lng)
                if distance > max_km:
                    dropped.append({"name": name, "reason": f"outside_city:{distance:.0f}km"})
                    continue

            poi.update({
                "location": {"lat": lat, "lng": lng},
                "latitude": lat,
                "longitude": lng,
                "coords_verified": True,
                "verified": False,
                "verification_source": getattr(result, "provider", "geocoder"),
            })
            if not poi.get("address"):
                poi["address"] = getattr(result, "display_name", "") or ""
            validated.append(poi)

        return validated, dropped

    def get_attractions(self, lat: float, lng: float, user_prefs: dict, weather_summary: str = None,
                        number: int = 30, 
                        poi_type: str = "tourist_attraction", 
                        sort_by: str = "rating", 
                        radius: int = 20000,
                        rerank_with_llm: bool = True):
        """Get a comprehensive list of attractions for a given location, ranked
        by LLM based on user preferences and weather.

        `number` is now a soft display target used only to size the LLM/RAG
        fallback deficit — the function returns the full deduplicated set so
        the Explore UI can offer users genuine selection breadth instead of
        stopping at an arbitrary 10-14. Default radius widened from 10km to
        20km so dispersed destinations (hill stations, backwater regions,
        heritage corridors) are properly covered.

        Args:
            rerank_with_llm: If False, skip LLM re-ranking (used for multi-city state searches
                             where a single combined rerank is done after merging all cities).
        """
        hobbies = user_prefs.get('hobbies') if user_prefs else None
        
        initial_pois = self.poi_manager.get_attractions(
            lat, lng, radius, hobbies, poi_type, city=(user_prefs or {}).get('city')
        )
        
        # ``days`` is the field TravelState actually stores; ``duration_days``
        # was never written by the chat flow, so the required-POI budget was
        # silently pinned to the 3-day default for every trip.
        duration_days = int(
            (user_prefs or {}).get('days')
            or (user_prefs or {}).get('duration_days')
            or 3
        )
        required_pois = max(10, duration_days * 3)
        city = user_prefs.get('city') if user_prefs else None
        
        # ── Fallback discovery ────────────────────────────────────────────────
        # Live providers are authoritative. Model output is used only to *top
        # up* a thin provider result and every suggested place must then be
        # geocoded and checked against the requested city, so a hallucinated or
        # foreign POI cannot enter the trip.
        provider_pois = list(initial_pois or [])
        generated: list = []
        generated_dropped: list = []

        def _extract_pois(raw_content, source, id_prefix):
            records = []
            for record in records_from_text(raw_content):
                name = clean_name(record.get("name") or record.get("title"))
                if not name:
                    continue
                category = str(record.get("category") or record.get("type") or "attraction")
                description = str(record.get("description") or record.get("summary") or "").strip()
                poi = {
                    "id": f"{id_prefix}_{hashlib.md5(name.encode()).hexdigest()[:12]}",
                    "name": name,
                    "category": category,
                    "description": description,
                    "source": source,
                    # Ratings/prices are never accepted from a model.  Only a
                    # value explicitly present in retrieved source text could be
                    # trusted, and the prompt tells the model not to invent one.
                    "rating": clamp_rating(record.get("rating")),
                    "price_level": record.get("price_level"),
                    "location": None,
                    "coords_verified": False,
                    "verified": False,
                }
                if record.get("address"):
                    poi["address"] = record["address"]
                records.append(poi)
            return records

        if len(provider_pois) < required_pois and city:
            deficit = required_pois - len(provider_pois)
            print(f"[INFO_AGENT] POI providers returned {len(provider_pois)} for '{city}'. Need {required_pois} (deficit: {deficit}).")

            # 1. Grounded extraction from retrieved knowledge (RAG).
            if not provider_pois:
                print(f"[INFO_AGENT] Triggering RAG Fallback for '{city}'.")
                try:
                    from agents.retrieval_agent import RetrievalAgent
                    if not hasattr(self, 'retrieval_agent') or self.retrieval_agent is None:
                        self.retrieval_agent = RetrievalAgent()

                    rag_context = self.retrieval_agent.retrieve_context(user_prefs, city)

                    if rag_context and "No additional background knowledge found" not in rag_context and "No destination provided" not in rag_context:
                        print(f"[INFO_AGENT] RAG Context found for {city}. Asking LLM to extract POIs.")
                        if self.llm:
                            prompt = f"""
                            Extract only the tourist attractions that are EXPLICITLY named in the
                            text below about {city}. Do not add places from your own knowledge.

                            Return a valid JSON array of objects with exactly these keys:
                            - "name": string (the name exactly as written in the text)
                            - "category": string (e.g. "heritage", "nature", "museum", "temple", "sightseeing")
                            - "description": string (only details stated in the text)

                            Do NOT output a rating, review count, price or opening hours unless the
                            text itself states it. Never guess those values.

                            Text:
                            {rag_context}

                            Respond ONLY with the JSON array.
                            """
                            try:
                                response = self.llm.invoke([HumanMessage(content=prompt)])
                                rag_pois = _extract_pois(
                                    getattr(response, "content", response), "rag", "rag"
                                )
                                if rag_pois:
                                    print(f"[INFO_AGENT] Successfully extracted {len(rag_pois)} POIs from RAG.")
                                    generated.extend(rag_pois)
                            except Exception as e:
                                print(f"[INFO_AGENT] LLM RAG extraction failed: {e}")
                except Exception as e:
                    print(f"[INFO_AGENT] RAG Fallback failed: {e}")

            # 2. Model suggestions — only when providers AND RAG produced
            #    nothing. These are unverified suggestions and are kept only if
            #    the geocoder can place them inside the requested city.
            if not provider_pois and not generated and self.llm:
                print(f"[INFO_AGENT] Triggering unverified LLM suggestion pass for '{city}'.")
                gen_count = min(25, deficit + 5)
                llm_prompt = f"""
                List up to {gen_count} real, well-known tourist attractions in or immediately
                around {city}. Never invent a place and never use generic entries such as
                "City Center" or "Local Market".

                Return a valid JSON array of objects with exactly these keys:
                - "name": string (the real name of the place)
                - "category": string (e.g. "heritage", "nature", "museum", "temple", "sightseeing")
                - "description": string (one short factual sentence)

                Do NOT invent ratings, review counts, prices or coordinates. They will be
                verified separately.

                Respond ONLY with the JSON array.
                """
                try:
                    response = self.llm.invoke([HumanMessage(content=llm_prompt)])
                    llm_pois = _extract_pois(
                        getattr(response, "content", response), "llm_suggested", "llm"
                    )
                    if llm_pois:
                        print(f"[INFO_AGENT] LLM suggested {len(llm_pois)} POIs (unverified).")
                        generated.extend(llm_pois)
                except Exception as e:
                    print(f"[INFO_AGENT] LLM Generation failed: {e}")

            # 3. Geocode and scope-check every generated record.
            if generated:
                validated, dropped = self._validate_generated_pois(
                    generated, city, lat, lng, country_code=self._country_code_for(city)
                )
                generated = validated
                generated_dropped = dropped
                if dropped:
                    print(f"[INFO_AGENT] Dropped {len(dropped)} generated POIs that could not be "
                          f"verified inside '{city}': {[d['name'] for d in dropped][:8]}")

        # De-duplicate provider + generated records by normalised name.
        seen_names = set()
        deduped = []
        for p in provider_pois + generated:
            n = str(p.get('name', '')).lower().strip()
            if n and n not in seen_names:
                seen_names.add(n)
                deduped.append(p)
        initial_pois = deduped
        if not initial_pois:
            print(f"[INFO_AGENT] No live or verifiable POIs for '{city}'. "
                  "Returning an empty list instead of fabricated attractions.")

        self.last_discovery_status = {
            "city": city,
            "provider_count": len(provider_pois),
            "generated_count": len(generated),
            "dropped_generated": generated_dropped,
            "used_llm_suggestions": any(p.get("source") == "llm_suggested" for p in generated),
            "used_rag_extraction": any(p.get("source") == "rag" for p in generated),
            "empty": not initial_pois,
        }
        
        # Add estimated_duration if missing and validate image_url
        initial_pois = self.enrich_attractions(initial_pois, city)
        
        print(f"[INFO_AGENT] Processed details for {len(initial_pois)} POIs.")
        if not initial_pois:
            return []

        if sort_by == 'price':
            initial_pois.sort(key=lambda x: (x.get('price_level') is None, x.get('price_level', float('inf'))))
        elif sort_by == 'rating':
            initial_pois.sort(key=lambda x: (x.get('rating') is None, -(float(x.get('rating', 0.0) or 0.0))))

        if user_prefs and self.llm and rerank_with_llm:
            print(f"[INFO_AGENT] Re-ranking {len(initial_pois)} attractions with LLM.")
            llm_ranked_pois = self._rerank_attractions_with_llm(initial_pois, user_prefs, weather_summary)
            return llm_ranked_pois
        else:
            print(f"[INFO_AGENT] Skipping LLM re-ranking. Returning all {len(initial_pois)} attractions from initial sort.")
            return initial_pois

    @staticmethod
    def _discovery_sort_key(poi):
        """Rank a candidate for broad discovery without inventing popularity.

        Wikipedia/Wikidata-backed places are verifiable and notable, so they
        come first; a reported rating breaks ties.  Unrated provider records
        are not discarded - they simply rank after notable/rated ones.
        """
        if not isinstance(poi, dict):
            return (9, 9, 9, 0.0)
        metadata = poi.get("metadata") if isinstance(poi.get("metadata"), dict) else {}
        source_rank = {"wikipedia": 3, "osm": 2, "geoapify": 1}.get(poi.get("source"), 0)
        notable = 0 if (metadata.get("wikidata_id") or poi.get("wikipedia_url") or poi.get("wikidata_id")) else 1
        try:
            rating = float(poi.get("rating"))
        except (TypeError, ValueError):
            rating = None
        return (-source_rank, notable, rating is None, -(rating or 0.0))

    @classmethod
    def _balanced_cap(cls, pois, cap):
        """Trim a merged pool while keeping every city represented.

        A plain ``[:cap]`` on a state search lets the first cities consume the
        entire budget, silently discarding the others.  Round-robin selection
        across city groups keeps the pool geographically balanced.
        """
        items = [p for p in (pois or []) if isinstance(p, dict)]
        if cap is None or cap <= 0 or len(items) <= cap:
            return items

        groups = {}
        for poi in items:
            key = poi.get("state_city") or poi.get("city") or poi.get("region") or ""
            groups.setdefault(str(key), []).append(poi)

        for group in groups.values():
            group.sort(key=cls._discovery_sort_key)

        ordered = []
        cursors = {key: 0 for key in groups}
        while len(ordered) < cap:
            progressed = False
            for key in groups:
                index = cursors[key]
                group = groups[key]
                if index < len(group):
                    ordered.append(group[index])
                    cursors[key] = index + 1
                    progressed = True
                    if len(ordered) >= cap:
                        break
            if not progressed:
                break
        return ordered

    def get_attractions_for_state(self, state_name: str, state_cities: list, user_prefs: dict,
                                  weather_summary: str = None, total_number: int = 80):
        """Comprehensive state-wide attraction discovery.

        The previous hard cap of 40 (and the per-city 15km radius) truncated
        hill-station and backwater itineraries to only 10-14 real choices for
        a 10-day trip. We now (a) widen the per-city radius, (b) derive the
        result cap from the trip duration when available, and (c) return the
        full merged set to the caller so users can *browse* broadly and select
        what they actually want. Discovery is intentionally decoupled from
        itinerary selection — the strategy agent decides what to schedule.

        Latency strategy: cities are geocoded SEQUENTIALLY (fast, cached, and it
        respects Nominatim's 1 req/s policy which is enforced via a shared
        timestamp), while the expensive per-city POI fetches (Overpass/Geoapify,
        which can each burn tens of seconds on 504 backoffs) run CONCURRENTLY.
        This makes a multi-city state search cost roughly its slowest city rather
        than the sum of every city, so it finishes well within the frontend's
        idle watchdog instead of stalling for minutes.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_pois = []
        seen_ids = set()

        hobbies = user_prefs.get('hobbies') if user_prefs else None

        # 1. Sequential geocode pass — preserves Nominatim rate-limit compliance
        #    and warms the shared geocoder cache. Cheap relative to POI fetches.
        geocoded = []  # [(city_name, coords)] in state_cities order
        for city_name in state_cities:
            city_query = f"{city_name}, {state_name}, India"
            try:
                coords = self.city2geocode(city_query, expected_country="in")
            except Exception as e:
                print(f"[STATE_SEARCH] Geocoding failed for '{city_query}': {e}")
                coords = None
            if coords:
                geocoded.append((city_name, coords))
            else:
                print(f"[ATTRACTION_PIPELINE] city={city_name} status=geocode_failed")

        # 2. Concurrent POI fetch — the slow network step, parallelised per city.
        def _fetch_city(city_name, coords):
            try:
                pois = self.poi_manager.get_attractions(
                    lat=coords['lat'], lng=coords['lng'],
                    radius=20000, hobbies=hobbies, poi_type="tourist_attraction",
                    city=city_name,
                )
                return city_name, (pois or [])
            except Exception as e:
                print(f"[ATTRACTION_PIPELINE] city={city_name} status=provider_failure error={e}")
                return city_name, []

        results_by_city = {}
        if geocoded:
            max_workers = min(5, len(geocoded))
            print(f"[STATE_SEARCH] Fetching POIs for {len(geocoded)} cities concurrently (max_workers={max_workers}).")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_fetch_city, cn, co) for cn, co in geocoded]
                for fut in as_completed(futures):
                    try:
                        city_name, pois = fut.result()
                        results_by_city[city_name] = pois
                    except Exception as e:
                        print(f"[STATE_SEARCH] POI worker error: {e}")

        # 3. Merge deterministically in original city order, de-duplicating by id.
        success_count = 0
        failed_count = 0
        for city_name, _coords in geocoded:
            pois = results_by_city.get(city_name, [])
            if pois:
                success_count += 1
                print(f"[ATTRACTION_PIPELINE] city={city_name} status=success poi_count={len(pois)}")
            else:
                failed_count += 1
                print(f"[ATTRACTION_PIPELINE] city={city_name} status=failed")
            for poi in pois:
                pid = poi.get('id')
                if pid and pid not in seen_ids:
                    # Attribute every POI to the city that produced it. The old
                    # `state_city = state_name` erased the per-city spread, so
                    # balanced capping and the UI could not tell Kochi from
                    # Wayanad. `state` keeps the higher-level scope.
                    poi.setdefault('city', city_name)
                    poi['state_city'] = city_name
                    poi['state'] = state_name
                    all_pois.append(poi)
                    seen_ids.add(pid)

        print(f"[ATTRACTION_PIPELINE] State search completed. Total cities attempted: {len(state_cities)}. Successful: {success_count}. Failed: {failed_count}. Raw POIs deduplicated: {len(all_pois)}")
        raw_poi_count = len(all_pois)

        if not all_pois:
            # Disclose the failure instead of leaving the UI with an empty,
            # unexplained discovery panel.
            self.last_discovery_status = {
                "scope": state_name,
                "cities": {name: 0 for name, _ in geocoded},
                "failed_cities": failed_count,
                "raw": 0,
                "empty": True,
                "reason": "all_cities_failed",
            }
            return []

        # Enforce a duration-aware cap BEFORE enrichment. For a 10-day trip
        # this yields ~120 choices; a 3-day trip still gets a broad 60. The cap
        # is a latency guard, not a recommendation decision, and the balanced
        # selection keeps every searched city represented.
        try:
            duration_days = int(
                (user_prefs or {}).get('days')
                or (user_prefs or {}).get('duration_days')
                or 0
            )
        except (TypeError, ValueError):
            duration_days = 0
        dynamic_cap = max(total_number, duration_days * 12) if duration_days else total_number
        dynamic_cap = min(dynamic_cap, 180)  # Bound to keep enrichment latency sane.
        all_pois = self._balanced_cap(all_pois, dynamic_cap)
        capped_count = len(all_pois)

        # Use centralized enrichment
        if all_pois:
            all_pois = self.enrich_attractions(all_pois, state_name)

        # Single LLM rerank across the entire state pool
        if user_prefs and self.llm and len(all_pois) > 0:
            print(f"[STATE_SEARCH] Running single LLM rerank on {len(all_pois)} combined state POIs.")
            all_pois = self._rerank_attractions_with_llm(all_pois, user_prefs, weather_summary)

        print(f"[STATE_SEARCH] Returning all {len(all_pois)} state-wide POIs.")
        # Per-city discovery diagnostics: which cities actually produced POIs,
        # how many survived the cap and how much image coverage there is. This
        # is what the scope/provider disclosure in the UI reads, so a state
        # search must publish it just like the single-city path does.
        self.last_discovery_status = {
            "scope": state_name,
            "cities": {name: len(results_by_city.get(name, [])) for name, _ in geocoded},
            "cities_with_results": {
                name: sum(1 for p in all_pois if p.get("state_city") == name)
                for name, _ in geocoded
            },
            "cities_attempted": len(geocoded),
            "successful_cities": success_count,
            "failed_cities": failed_count,
            "raw": raw_poi_count,
            "capped": capped_count,
            "total": len(all_pois),
            "empty": False,
            "multi_city": len(geocoded) > 1,
            "image_status": dict(getattr(self, "last_image_status", None) or {}),
        }
        return all_pois

    def get_attractions_for_cities(self, cities, user_prefs, weather_summary: str = None,
                                   radius: int = 20000, total_number: int = 80,
                                   rerank_with_llm: bool = True):
        """Discover attractions across several verified city centres.

        Country- and region-level requests are handled here instead of being
        collapsed onto one geocoder centroid.  Each city is searched
        independently, every record is tagged with the city it came from, and a
        POI is kept only when its coordinates really are near that city (or a
        genuine day-trip radius around it).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        hobbies = user_prefs.get('hobbies') if user_prefs else None
        cities = [c for c in (cities or []) if c and c.get('name')]
        if not cities:
            self.last_discovery_status = {"cities": [], "empty": True, "reason": "no_city_scope"}
            return []

        def _fetch_city(city):
            name = city.get('name')
            try:
                pois = self.poi_manager.get_attractions(
                    lat=float(city['lat']), lng=float(city['lng']),
                    radius=radius, hobbies=hobbies, poi_type="tourist_attraction",
                    city=name,
                )
                return name, city, (pois or [])
            except Exception as e:
                print(f"[CITY_SEARCH] city={name} status=provider_failure error={e}")
                return name, city, []

        results = {}
        max_workers = min(5, len(cities))
        print(f"[CITY_SEARCH] Fetching POIs for {len(cities)} cities concurrently "
              f"(max_workers={max_workers}).")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_fetch_city, c) for c in cities]
            for future in as_completed(futures):
                try:
                    name, city, pois = future.result()
                    results[name] = (city, pois)
                except Exception as e:
                    print(f"[CITY_SEARCH] worker error: {e}")

        all_pois = []
        seen = set()
        per_city = {}
        for city in cities:  # original order keeps the proposal deterministic
            name = city['name']
            _, pois = results.get(name, (city, []))
            per_city[name] = {"provider_count": len(pois), "kept": 0}
            for poi in pois:
                lat = self._coerce_coord(poi.get('latitude', poi.get('lat')))
                lng = self._coerce_coord(poi.get('longitude', poi.get('lng')))
                if lat is None or lng is None:
                    continue
                distance = haversine_km(city['lat'], city['lng'], lat, lng)
                # A 20 km provider radius plus a small tolerance; this rejects
                # results from a different prefecture/city entirely.
                if distance > max(30.0, radius / 1000.0 + 10.0):
                    continue
                poi['city'] = name
                poi['region'] = city.get('region') or ''
                poi['city_distance_km'] = round(distance, 2)
                if city.get('country'):
                    poi['country'] = city['country']
                key = poi.get('id') or (str(poi.get('name', '')).lower(), round(lat, 4), round(lng, 4))
                if key in seen:
                    continue
                seen.add(key)
                all_pois.append(poi)
                per_city[name]["kept"] += 1

        print(f"[CITY_SEARCH] {sum(v['kept'] for v in per_city.values())} unique POIs "
              f"from {len(cities)} cities.")

        if not all_pois:
            self.last_discovery_status = {
                "cities": per_city, "empty": True, "reason": "all_cities_failed",
            }
            return []

        try:
            duration_days = int(
                (user_prefs or {}).get('days')
                or (user_prefs or {}).get('duration_days')
                or 0
            )
        except (TypeError, ValueError):
            duration_days = 0
        dynamic_cap = max(total_number, duration_days * 12) if duration_days else total_number
        dynamic_cap = min(dynamic_cap, 200)
        all_pois = self._balanced_cap(all_pois, dynamic_cap)

        all_pois = self.enrich_attractions(
            all_pois, ", ".join(c['name'] for c in cities[:3])
        )

        if user_prefs and self.llm and rerank_with_llm and all_pois:
            print(f"[CITY_SEARCH] Running single LLM rerank on {len(all_pois)} POIs.")
            all_pois = self._rerank_attractions_with_llm(all_pois, user_prefs, weather_summary)

        self.last_discovery_status = {
            "cities": per_city,
            "empty": False,
            "total": len(all_pois),
            "multi_city": len(cities) > 1,
        }
        return all_pois

    def get_accommodations(self, lat: float, lng: float, budget: str, number: int = 4):
        """Fetch accommodation options based on user budget."""
        return self.poi_manager.get_accommodations(lat, lng, budget, number)

    def estimate_duration(self, category, details):
        """
        Estimate the duration for a given category and details.
        Returns duration in hours.
        """
        category_duration = {
            'restaurant': 2,
            'museum': 2,
            'park': 2,
            'tourist_attraction': 2,
            'night_club': 3,
            'shopping_mall': 3,
            'zoo': 3,
            'amusement_park': 6
        }
        

        # Default duration if category is not found
        default_duration = 2
        
        # Get duration based on category
        duration = category_duration.get(category, default_duration)
        
        # Adjust duration based on rating
        rating = details.get('rating', 0)
        if rating > 4.5:
            duration *= 1.5
        elif rating < 3:
            duration *= 0.75
        
        return duration

    def _parse_location_for_osrm(self, location: str):
        if not isinstance(location, str):
            return None
        try:
            lat_str, lng_str = location.split(',')
            lat = float(lat_str.strip())
            lng = float(lng_str.strip())
            return f"{lng},{lat}"
        except ValueError:
            res = self.geocoder.geocode(location)
            if res:
                return f"{res.lng},{res.lat}"
            return None

    def plan_routes(self, origin: str, destination: str):
        """
        Route Planning (Simple A to B) using OSRM.
        """
        routes = []
        origin_coords = self._parse_location_for_osrm(origin)
        dest_coords = self._parse_location_for_osrm(destination)
        if not origin_coords or not dest_coords:
            return routes
            
        url = f"http://router.project-osrm.org/route/v1/driving/{origin_coords};{dest_coords}?overview=false"
        try:
            import urllib.request, json
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=self.OSRM_TIMEOUT_SECONDS) as res:
                data = json.loads(res.read())
                if data.get('code') == 'Ok':
                    route = data['routes'][0]
                    dist_m = route['distance']
                    dur_s = route['duration']
                    routes.append({
                        'mode': 'driving',
                        'distance': format_distance(dist_m),
                        'duration': format_duration(dur_s),
                        'distance_meters': dist_m,
                        'duration_seconds': dur_s,
                        'fare': None,
                        'routing_source': 'osrm_route',
                        'routing_status': 'ok',
                    })
                else:
                    print(f"[OSRM][WARN] plan_routes non-Ok code: {data.get('code')}")
        except Exception as e:
            print(f"[OSRM][WARN] plan_routes unavailable ({type(e).__name__}: {e}); "
                  "caller will use the local distance heuristic.")
            
        return routes

    def plan_with_waypoints(self, origin: str, destination: str, waypoints: list,
                                            mode: str = 'driving', departure_time=None):
        """
        Plans an optimized route visiting a list of waypoints using OSRM Trip API.
        """
        if not waypoints:
            print("Warning: No waypoints provided. Calling standard plan_routes for A-B.")
            simple_route_options = self.plan_routes(origin, destination)
            driving_route = next((r for r in simple_route_options if r['mode'] == 'driving'), None)
            if driving_route:
                 start_addr = origin if isinstance(origin, str) else f"Coord: {origin}"
                 end_addr = destination if isinstance(destination, str) else f"Coord: {destination}"
                 return {
                    'path_sequence': [start_addr, end_addr],
                    'waypoint_original_indices': [],
                    'total_duration_text': driving_route['duration'],
                    'total_duration_seconds': driving_route['duration_seconds'],
                    'total_duration_in_traffic_text': None,
                    'total_duration_in_traffic_seconds': None,
                    'total_distance_text': driving_route['distance'],
                    'total_distance_meters': driving_route['distance_meters'],
                    'fare': driving_route.get('fare'),
                    'routing_source': driving_route.get('routing_source', 'osrm_route'),
                    'routing_status': driving_route.get('routing_status', 'ok'),
                    'optimized': False,
                 }
            else:
                print(f"Could not find a driving route from {origin} to {destination} in fallback.")
                return None

        print(f"Planning optimized route: {origin} -> Waypoints -> {destination} using OSRM")

        all_coords = []
        parsed_origin = self._parse_location_for_osrm(origin)
        parsed_dest = self._parse_location_for_osrm(destination)
        if not parsed_origin or not parsed_dest:
            return None
        all_coords.append(parsed_origin)
        
        for wp in waypoints:
            p = self._parse_location_for_osrm(wp)
            if p:
                all_coords.append(p)
            else:
                return None
            
        all_coords.append(parsed_dest)
        
        coords_str = ";".join(all_coords)
        url = f"http://router.project-osrm.org/trip/v1/driving/{coords_str}?source=first&destination=last&roundtrip=false"
        try:
            import urllib.request, json
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=self.OSRM_TIMEOUT_SECONDS) as res:
                data = json.loads(res.read())
                if data.get('code') == 'Ok':
                    trip = data['trips'][0]
                    waypoints_res = data['waypoints']
                    
                    intermediate_wps = []
                    for i, wp in enumerate(waypoints_res[1:-1]):
                        intermediate_wps.append({
                            'original_idx': i,
                            'trip_idx': wp['waypoint_index']
                        })
                    
                    intermediate_wps.sort(key=lambda x: x['trip_idx'])
                    optimized_indices = [x['original_idx'] for x in intermediate_wps]
                    
                    dist_m = trip['distance']
                    dur_s = trip['duration']
                    
                    path_sequence = [origin] + [waypoints[i] for i in optimized_indices] + [destination]
                    
                    return {
                        'path_sequence': path_sequence,
                        'waypoint_original_indices': optimized_indices,
                        'total_duration_text': format_duration(dur_s),
                        'total_duration_seconds': dur_s,
                        'total_duration_in_traffic_text': None,
                        'total_duration_in_traffic_seconds': None,
                        'total_distance_text': format_distance(dist_m),
                        'total_distance_meters': dist_m,
                        'fare': None,
                        'routing_source': 'osrm_trip',
                        'routing_status': 'ok',
                        'optimized': True,
                    }
                else:
                    print(f"[OSRM][WARN] plan_with_waypoints non-Ok code: {data.get('code')}")
        except Exception as e:
            print(f"[OSRM][WARN] Optimized route unavailable "
                  f"({type(e).__name__}: {e}); caller will use the local distance heuristic.")
            
        return None

    def get_weather(self, lat: float, lng: float, start_date: str, duration: int, summary: bool = True):
        """
        Weather Forecast.
        
        Args:
            lat: Latitude
            lng: Longitude
            start_date: Start date (YYYY-MM-DD)
            duration: Number of days
            summary: Whether to include an LLM-generated summary.
            
        Returns:
            Dictionary containing detailed weather forecast and an optional summary.
            Example:
            {
                'detailed_forecast': [
                    {
                        "date": "2023-04-18",
                        "max_temp": "22 °C",
                        "min_temp": "15 °C",
                        "precipitation": "0 mm",
                        "wind_speed": "12 km/h",
                        "precipitation_probability": "5%",
                        "uv_index": "7"
                    },
                    ...
                ],
                'summary': "Concise weather summary..." # or None
            }
        """
        # Get detailed weather data first
        weather_data = self.weather_service.get_weather(lat, lng, start_date, duration)
        
        # If no weather data, return empty result
        if not weather_data:
            return {'detailed_forecast': [], 'summary': None}
        
        # Create result dictionary with detailed forecast
        result = {
            'summary': 'Weather overview unavailable.',
            'temperature_summary': '',
            'precipitation_summary': '',
            'wind_summary': '',
            'uv_summary': '',
            'recommendation': '',
            'daily_forecast': weather_data
        }
        
        # Generate summary if requested
        if summary:
            # Create a prompt for the summary writer
            weather_info = json.dumps(weather_data, indent=2)
            prompt = f"""
Analyze the following weather forecast and generate a comprehensive travel-oriented weather summary.
Output your response STRICTLY as a JSON object with the following keys:
- "summary": 2-4 sentences overviewing the weather conditions for the trip
- "temperature_summary": 1 sentence summarizing temperatures
- "precipitation_summary": 1 sentence summarizing rain/snow
- "wind_summary": 1 sentence summarizing wind conditions
- "uv_summary": 1 sentence summarizing UV index
- "recommendation": 1-2 sentences of packing/travel advice based on weather

Weather data:
{weather_info}
            """
            
            # Generate the summary
            messages = [
                SystemMessage(content="You are a helpful weather assistant that outputs strictly in JSON format without markdown code blocks."),
                HumanMessage(content=prompt)
            ]
            
            try:
                response = self.weather_summary_writer.invoke(messages)
                content = response.content if hasattr(response, 'content') else str(response)
                content = content.replace("```json", "").replace("```", "").strip()
                summary_json = json.loads(content)
                
                result['summary'] = summary_json.get('summary', result['summary'])
                result['temperature_summary'] = summary_json.get('temperature_summary', '')
                result['precipitation_summary'] = summary_json.get('precipitation_summary', '')
                result['wind_summary'] = summary_json.get('wind_summary', '')
                result['uv_summary'] = summary_json.get('uv_summary', '')
                result['recommendation'] = summary_json.get('recommendation', '')
            except Exception as e:
                print(f"[WARN] Failed to parse structured weather summary: {e}")
        
        return result
            
        
    def search_car_rentals(self, location: str, start_date: str, end_date: str,
                           driver_age: int = 30, min_price: float = None, 
                           max_price: float = None, top_n: int = 5):
        """
        Car Rental Search.
        
        Args:
            location: Location (city name)
            start_date: Pickup date (YYYY-MM-DD)
            end_date: Return date (YYYY-MM-DD)
            driver_age: Driver's age (default: 30)
            min_price: Minimum price (optional)
            max_price: Maximum price (optional)
            top_n: Number of results to return (default: 5)
            
        Returns:
            Top N car rental options, including car type, price, pickup/return locations, links, etc.
            Uses mock data if API is not configured or fails.
            Example:
            [
                {
                    "car_model": "Mitsubishi Mirage",
                    "car_group": "Economy",
                    "price": 332.29,
                    "currency": "USD",
                    "pickup_location_name": "Los Angeles International Airport",
                    "supplier_name": "Enterprise",
                    "image_url": "https://cdn.rcstatic.com/images/car_images/web/mitsubishi/mirage_lrg.png"
                },
                ...
            ]
        """
        try:
            # Get location coordinates
            location_data = self.city2geocode(location)
            if not location_data:
                return []
            
            # Parse dates
            pickup_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
            dropoff_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
            
            # Format dates and times for API
            pickup_date = pickup_date_obj.strftime("%Y-%m-%d")
            pickup_time = "10:00:00"  # Default pickup time
            dropoff_date = dropoff_date_obj.strftime("%Y-%m-%d")
            dropoff_time = "10:00:00"  # Default dropoff time
            
            # Call the car rental service
            cars = self.car_rental_service.find_available_cars(
                pickup_lat=location_data['lat'],
                pickup_lon=location_data['lng'],
                pickup_date=pickup_date,
                pickup_time=pickup_time,
                dropoff_lat=location_data['lat'],
                dropoff_lon=location_data['lng'],
                dropoff_date=dropoff_date,
                dropoff_time=dropoff_time,
                currency_code="USD",
                driver_age=driver_age,
            )
            
            # Filter by price if needed
            if cars and min_price is not None:
                cars = [c for c in cars if c.get('price', 0) >= min_price]
            if cars and max_price is not None:
                cars = [c for c in cars if c.get('price', 0) <= max_price]
                
            # Return top N results or mock data if API returned nothing
            return cars[:top_n] if cars else []
            
        except Exception as e:
            print(f"[PROVIDER][CAR_RENTAL][UNAVAILABLE] Error in search_car_rentals: {str(e)}")
            # Mark car rental unavailable on API error (e.g., 403)
            return []
            
            
    def _get_mock_car_data(self, top_n: int = 5):
        """Returns a list of mock car rental data."""
        mock_cars = [
            {
                "car_model": "Toyota Corolla",
                "car_group": "Economy",
                "price": 299.99,
                "currency": "USD",
                "pickup_location_name": "Sample Airport",
                "supplier_name": "Hertz",
                "image_url": "https://example.com/corolla.jpg"
            },
            {
                "car_model": "Honda Civic",
                "car_group": "Compact",
                "price": 349.99,
                "currency": "USD",
                "pickup_location_name": "Sample Airport",
                "supplier_name": "Avis",
                "image_url": "https://example.com/civic.jpg"
            },
            {
                "car_model": "Ford Mustang",
                "car_group": "Sports",
                "price": 599.99,
                "currency": "USD",
                "pickup_location_name": "Sample Airport",
                "supplier_name": "Enterprise",
                "image_url": "https://example.com/mustang.jpg"
            },
            {
                "car_model": "BMW 3 Series",
                "car_group": "Luxury",
                "price": 799.99,
                "currency": "USD",
                "pickup_location_name": "Sample Airport",
                "supplier_name": "Sixt",
                "image_url": "https://example.com/bmw.jpg"
            },
            {
                "car_model": "Mercedes-Benz C-Class",
                "car_group": "Premium",
                "price": 899.99,
                "currency": "USD",
                "pickup_location_name": "Sample Airport",
                "supplier_name": "Europcar",
                "image_url": "https://example.com/mercedes.jpg"
            }
        ]
        return mock_cars[:top_n]

    def search_nearby_places(self, lat: float, lng: float, radius: int = 500):
        """Search for nearby restaurants and food stalls using OSM Overpass API.

        Args:
            lat (float): Latitude of the attraction
            lng (float): Longitude of the attraction
            radius (int): Search radius in metres (default 500 m)

        Returns:
            dict: {'restaurants': [...]} — real food POIs from OSM.
                  Falls back to an empty list on failure (never raises).
        """
        import requests, time

        overpass_endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://lz4.overpass-api.de/api/interpreter",
            "https://z.overpass-api.de/api/interpreter",
        ]

        query = f"""
[out:json][timeout:15];
(
  node["amenity"~"restaurant|cafe|fast_food|food_court|bar|pub|biergarten|ice_cream"](around:{radius},{lat},{lng});
  way["amenity"~"restaurant|cafe|fast_food|food_court"](around:{radius},{lat},{lng});
);
out 15 tags center;
"""
        headers = {
            "User-Agent": "ExploreX-Travel-App/1.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        elements = []
        for endpoint in overpass_endpoints:
            try:
                resp = requests.post(endpoint, data={"data": query}, headers=headers, timeout=15)
                if resp.status_code == 429:
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                break
            except Exception as e:
                print(f"[INFO_AGENT][NearbyFood] Overpass endpoint {endpoint} failed: {e}")

        restaurants = []
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:en")
            if not name:
                continue

            amenity_type = tags.get("amenity", "restaurant")
            cuisine = tags.get("cuisine", "")
            address_parts = [
                tags.get("addr:housenumber", ""),
                tags.get("addr:street", ""),
                tags.get("addr:city", ""),
            ]
            address = " ".join(p for p in address_parts if p).strip() or None

            # OSM doesn't carry ratings — omit rather than invent
            entry = {
                "name": name,
                "type": amenity_type.replace("_", " ").title(),
            }
            if cuisine:
                entry["cuisine"] = cuisine.replace(";", ", ").title()
            if address:
                entry["address"] = address
            if tags.get("opening_hours"):
                entry["opening_hours"] = tags["opening_hours"]
            if tags.get("website"):
                entry["website"] = tags["website"]

            # Compute approximate distance from attraction coords
            el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
            el_lng = el.get("lon") or (el.get("center") or {}).get("lon")
            if el_lat and el_lng:
                import math
                dlat = math.radians(el_lat - lat)
                dlng = math.radians(el_lng - lng)
                a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat)) * math.cos(math.radians(el_lat)) * math.sin(dlng / 2) ** 2
                dist_m = int(6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))
                entry["distance_m"] = dist_m

            restaurants.append(entry)

        # Sort by distance if available
        restaurants.sort(key=lambda r: r.get("distance_m", 9999))

        print(f"[INFO_AGENT] Nearby food search at ({lat},{lng}) returned {len(restaurants)} results.")
        return {"restaurants": restaurants[:10]}

    
    def _get_restaurant_features(self, place):
        """Get restaurant features (cuisine types) from place types."""
        features = []
        if 'types' in place:
            if 'chinese_restaurant' in place['types']:
                features.append('Chinese')
            if 'japanese_restaurant' in place['types']:
                features.append('Japanese')
            if 'italian_restaurant' in place['types']:
                features.append('Italian')
            if 'french_restaurant' in place['types']:
                features.append('French')
        return ', '.join(features) if features else 'Cuisine'

    def get_fuel_price(self, location: str):
        """
        Get fuel prices for a specific location.
        
        Args:
            location (str): Location name (city).
        
        Returns:
            float: Fuel price in USD per gallon, or None if not found.
        """
        try:
            return get_gas_price(location)
        except Exception as e:
            print(f"Error getting fuel prices: {str(e)}")
            return None
    # Fields that must survive enrichment untouched.  The old implementation
    # rebuilt the dict and silently deleted the very evidence the interest
    # matcher needed (OSM tags, provider types, Wikidata links).
    _PRESERVED_POI_FIELDS = (
        "tags", "types", "osm_tags", "metadata", "wikidata_id", "wikipedia",
        "wikidata", "wikipedia_url", "osm_id", "osm_type", "place_id",
        "provider", "source", "sources", "country", "country_code", "city", "region",
        "state", "state_city", "city_name", "opening_hours", "entry_fee", "fee",
        "price_level", "currency", "rating_status", "price_status",
        "amenities", "amenities_status", "feature_type", "categories",
        "interest_match", "matched_interests", "interest_match_reasons",
        "score_components", "match_percentage", "verified",
    )

    @staticmethod
    def _coerce_coord(value):
        """Return a finite float or ``None`` — never 0.0 for a missing value."""
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number

    @staticmethod
    def _first_real_url(*values):
        """Return the first safely normalised image URL, or ``None``."""
        for value in values:
            url = normalize_image_url(value)
            if url:
                return url
        return None

    def enrich_attractions(self, pois, city_name):
        """Normalise POIs without inventing facts.

        What changed vs. the previous version:

        * an unreported ``rating`` stays ``None`` (it used to become ``4.0``);
        * an unreported ``review_count`` / ``price`` stays ``None``;
        * a missing image stays ``None`` — no random Unsplash photo is attached
          and presented as if it were the place;
        * a description is derived only from provider facts (name/category/city)
          and never claims the place is "popular" or "highly rated";
        * the address is never synthesised from name + city;
        * structured tags/types/metadata/Wikidata links are preserved;
        * missing coordinates stay ``None`` instead of ``(0.0, 0.0)``.
        """
        from concurrent.futures import ThreadPoolExecutor
        import hashlib

        def _enrich_single(poi):
            if not isinstance(poi, dict):
                return None
            raw_id = poi.get('place_id') or poi.get('id')
            name = str(poi.get('name') or '').strip() or 'Unknown Attraction'
            poi_id = str(raw_id) if raw_id else hashlib.md5(name.encode()).hexdigest()

            loc = poi.get('location') if isinstance(poi.get('location'), dict) else {}
            lat = self._coerce_coord(
                poi.get('latitude', poi.get('lat', loc.get('lat', loc.get('latitude'))))
            )
            lng = self._coerce_coord(
                poi.get('longitude', poi.get('lng', loc.get('lng', loc.get('longitude'))))
            )
            if lat is not None and not (-90 <= lat <= 90):
                lat = None
            if lng is not None and not (-180 <= lng <= 180):
                lng = None

            rating = poi.get('rating')
            try:
                rating = float(rating) if rating not in (None, "", False) else None
            except (TypeError, ValueError):
                rating = None
            if rating is not None and not (0 < rating <= 5):
                rating = None

            review_count = poi.get('review_count', poi.get('user_ratings_total'))
            try:
                review_count = int(review_count) if review_count not in (None, "", False) else None
            except (TypeError, ValueError):
                review_count = None
            if review_count is not None and review_count < 0:
                review_count = None

            metadata = poi.get('metadata') if isinstance(poi.get('metadata'), dict) else {}
            img_url = self._first_real_url(
                poi.get('image_url'), poi.get('image'), poi.get('photo'),
                metadata.get('image'), metadata.get('image_url'),
                metadata.get('thumbnail'),
            )

            description = str(poi.get('description') or '').strip()
            if len(description) < 20:
                cat = str(poi.get('category') or 'attraction').replace('_', ' ').strip()
                description = f"{cat.title()} in {city_name}." if city_name else f"{cat.title()}."

            duration = poi.get('estimated_duration') or poi.get('duration')
            if not duration:
                duration = self.estimate_duration(
                    poi.get('category', 'tourist_attraction'),
                    {'rating': rating} if rating is not None else {},
                )

            enriched = {
                "id": poi_id,
                "name": name,
                "description": description,
                # No synthetic address: the UI shows "Address not provided".
                "address": poi.get('address') or poi.get('formatted') or "",
                "latitude": lat,
                "longitude": lng,
                "rating": rating,
                "review_count": review_count,
                "price": poi.get('price_level') if poi.get('price_level') is not None else poi.get('price'),
                "duration": duration,
                "category": poi.get('category', 'tourist_attraction'),
                "image_url": img_url,
                "source": poi.get('source', 'unknown'),
                "match_percentage": poi.get('match_percentage', 100),
            }
            if lat is not None and lng is not None:
                enriched["location"] = {"lat": lat, "lng": lng}
            else:
                enriched["location"] = None

            for field in self._PRESERVED_POI_FIELDS:
                if field in poi and poi.get(field) is not None:
                    enriched.setdefault(field, poi[field])
            # Explicitly preserve structured evidence even when falsy-but-real.
            for field in ("tags", "types", "osm_tags", "metadata"):
                if field in poi and poi[field] not in (None, {}, []):
                    enriched[field] = poi[field]
            return enriched

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(pois or [])))) as executor:
            enriched = [item for item in executor.map(_enrich_single, pois or []) if item]

        # Verified images first: provider metadata, OSM tags, Wikipedia
        # titles/GeoSearch, Wikidata P18 and Commons are tried in batch.  The
        # per-place Wikipedia search is only a last resort.  The budget is
        # derived from the batch size so a 60-place state search is not cut off
        # after 24 photos.  The resolver's guarantee stage then fills every
        # remaining card with an explicitly representative image or a generated
        # travel card, so no displayed attraction has an empty image area.
        if enriched:
            try:
                budget = int(os.environ.get(
                    "ATTRACTION_IMAGE_MAX_LOOKUPS", str(max(24, min(120, len(enriched))))
                ))
            except (TypeError, ValueError):
                budget = max(24, min(120, len(enriched)))
            try:
                enrich_poi_images(enriched, city_name, max_lookups=budget)
                from services.attraction_images import get_image_resolver
                self.last_image_status = dict(get_image_resolver().last_status)
                print(
                    f"[ATTRACTION_IMAGES] city={city_name or 'unknown'} "
                    f"resolved={self.last_image_status.get('resolved', 0)} "
                    f"missing={self.last_image_status.get('missing', 0)} "
                    f"state={self.last_image_status.get('state', 'unknown')} "
                    f"sources={self.last_image_status.get('sources', {})}"
                )
            except Exception as exc:
                # Image enrichment is never allowed to fail an attraction
                # search; disclose the failure instead of hiding it.
                self.last_image_status = {"state": "error", "detail": type(exc).__name__}
                print(f"[ATTRACTION_IMAGES][WARN] {type(exc).__name__}: {exc}")

        return enriched

