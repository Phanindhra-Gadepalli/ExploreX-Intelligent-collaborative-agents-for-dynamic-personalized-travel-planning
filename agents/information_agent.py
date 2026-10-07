import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import os
import sys
import json
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
        """Returns a list of matched keyword groups."""
        raw_hobbies = user_prefs.get('hobbies', '')
        hobbies = str(raw_hobbies).lower().strip()
        
        if not hobbies or hobbies == 'none':
            return []
            
        keyword_map = {
            'nature': ['nature', 'forest', 'wildlife', 'national park', 'mountain', 'hill', 'trek', 'hike', 'valley', 'meadow', 'peak', 'ridge', 'sanctuary', 'reserve', 'jungle', 'river', 'lake', 'waterfall', 'beach', 'eco park'],
            'history': ['historic', 'monument', 'fort', 'palace', 'tomb', 'archaeological', 'unesco', 'museum', 'castle', 'memorial', 'ruins', 'heritage', 'historical', 'ancient'],
            'architecture': ['architecture', 'fortress', 'bastion', 'haveli', 'mahal', 'qila', 'minaret', 'gateway', 'darwaza', 'arch', 'pillar', 'stupa', 'baoli', 'stepwell', 'mausoleum', 'sculpture'],
            'spiritual': ['temple', 'hindu temple', 'place of worship', 'religious', 'spiritual', 'mandir', 'shrine', 'mosque', 'church', 'gurudwara', 'masjid', 'dargah', 'buddhist temple', 'ashram', 'ghat'],
            'culture': ['culture', 'art gallery', 'gallery', 'exhibition', 'cultural center', 'art', 'traditional'],
            'food': ['food', 'restaurant', 'cafe', 'street food', 'dining', 'culinary', 'market', 'bazaar', 'chowk'],
            'photography': ['photography', 'viewpoint', 'observation', 'lookout', 'panorama', 'scenic', 'picturesque', 'sunset', 'sunrise'],
            'adventure': ['adventure', 'trekking', 'hiking', 'camping', 'safari', 'water sports', 'paragliding', 'climbing'],
            'shopping': ['shopping', 'mall', 'bazaar', 'market', 'souvenir', 'boutique', 'crafts'],
            'nightlife': ['nightlife', 'club', 'bar', 'pub', 'lounge', 'night market', 'evening'],
            'family': ['family', 'zoo', 'aquarium', 'theme park', 'amusement park', 'water park', 'planetarium', 'kids'],
            'offbeat': ['offbeat', 'hidden gem', 'unexplored', 'local secret', 'quiet', 'secluded']
        }
        
        hobbies_cleaned = hobbies.replace(',', ' ').replace('&', ' ').replace('/', ' ').replace('with', ' ').replace('plus', ' ')
        hobbies_parts = [p.strip() for p in hobbies_cleaned.split()]
        hobbies_rejoined = " ".join(hobbies_parts)
        
        user_groups = []
        for group_name, keywords in keyword_map.items():
            if any(k in hobbies_rejoined for k in keywords):
                user_groups.append(group_name)
                
        # Support for specific interest weights (e.g., 'photography:90')
        interest_weights = {}
        for part in hobbies_parts:
            if ':' in part:
                try:
                    category, weight = part.split(':')
                    interest_weights[category.lower()] = int(weight)
                except:
                    pass
                    
        a_name = str(attr.get('name', '')).lower()
        a_category = str(attr.get('category', '')).lower()
        a_desc = str(attr.get('description', '')).lower()
        a_type = str(attr.get('type', '')).lower()
        a_source = str(attr.get('source', '')).lower()
        a_tags_raw = attr.get('tags', [])
        a_osm_tags = attr.get('osm_tags', {})
        a_types = attr.get('types', [])
        
        attr_text_pool = f"{a_name} {a_category} {a_desc} {a_type} {a_source} {str(a_tags_raw).lower()} {str(a_osm_tags).lower()} {str(a_types).lower()}"
        
        matched_groups = []
        for group_name in user_groups:
            keywords = keyword_map[group_name]
            if any(k in attr_text_pool for k in keywords):
                matched_groups.append(group_name)
                
        # Include weight mapping
        attr['_interest_weights'] = interest_weights
                
        return matched_groups

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
        """Calculate normalized Match Percentage (0-100)."""
        score = 0
        max_possible_score = 100
        
        # 1. Quality (0-20 points)
        quality = self._evaluate_attraction_quality(attr)
        if quality == "low":
            return -1, [] # Discard
        
        quality_score = 10
        metadata = attr.get('metadata', {})
        if metadata.get("wikidata_id") or metadata.get("wikipedia") or attr.get('wikidata_id'):
            quality_score = 20
        if attr.get("description") and len(attr.get("description")) > 20:
            quality_score += 5
            
        score += min(20, quality_score)
            
        # 2. Interest Compatibility (0-40 points)
        matched_groups = self._heuristic_interest_match(attr, user_prefs)
        interest_score = 0
        weights = attr.get('_interest_weights', {})
        
        if matched_groups:
            for group in matched_groups:
                weight = weights.get(group, 50) # Default weight 50
                interest_score += (weight / 100) * 20
        else:
            interest_score = 5 # Baseline for generic attractions
            
        score += min(40, interest_score)
        
        # 3. Popularity & Rating (0-20 points)
        rating_score = 0
        rating = attr.get("rating")
        if rating and isinstance(rating, (int, float)):
            rating_score = (rating / 5.0) * 15
        
        if attr.get("source") == "rag":
            rating_score += 5 # RAG sources are highly curated
            
        score += min(20, rating_score)
            
        # 4. LLM AI Score (0-20 points)
        llm_normalized = (llm_score / 100.0) * 20 if isinstance(llm_score, (int, float)) else 0
        score += min(20, llm_normalized)
        
        # Normalize to 0-100
        match_percentage = min(100, max(0, int((score / max_possible_score) * 100)))
        attr['match_percentage'] = match_percentage
        
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
                    llm_output_content = response.content.strip()
                    if llm_output_content.startswith("```json"):
                        llm_output_content = llm_output_content[7:]
                        if llm_output_content.endswith("```"):
                            llm_output_content = llm_output_content[:-3]
                            
                    llm_parsed = json.loads(llm_output_content.strip())
                    self.llm_rerank_cache[cache_key] = llm_parsed
                except Exception as e:
                    print(f"[INFO_AGENT_LLM_ERROR] LLM parsing failed: {e}")
                    llm_parsed = []
                    
            if isinstance(llm_parsed, list):
                for item in llm_parsed:
                    if isinstance(item, dict) and "id" in item:
                        llm_results_map[item["id"]] = item

        # 2. Compute final deterministic scores
        scored_attractions = []
        for attr in attractions_list:
            llm_info = llm_results_map.get(attr.get("id"), {})
            llm_score = llm_info.get("relevance_score", 0)
            if isinstance(llm_score, str) and llm_score.isdigit(): llm_score = int(llm_score)
            if not isinstance(llm_score, (int, float)): llm_score = 0
            
            final_score, matched_groups = self._calculate_score(attr, user_prefs, llm_score)
            attr["_internal_score"] = final_score
            attr["matched_interests"] = matched_groups
            
            # Use LLM tourist_value if high
            if llm_info.get("tourist_value", "").lower() == "high":
                attr["_internal_score"] += 20
                
            attr["recommendation_type"] = "interest_based" if matched_groups else "popular"
            scored_attractions.append(attr)

        # 3. Sort by score
        scored_attractions.sort(key=lambda x: x["_internal_score"], reverse=True)
        
        # 4. Filter out negative scores (low quality)
        final_list = [a for a in scored_attractions if a["_internal_score"] >= 0]
        
        # If we filtered everything, fallback to original but limit damage
        if not final_list:
            final_list = scored_attractions
            
        # Limit to top 50 attractions as requested
        final_list = final_list[:50]
            
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
    
    def get_attractions(self, lat: float, lng: float, user_prefs: dict, weather_summary: str = None,
                        number: int = 20, 
                        poi_type: str = "tourist_attraction", 
                        sort_by: str = "rating", 
                        radius: int = 10000,
                        rerank_with_llm: bool = True):
        """Get a list of attractions for a given location, ranked by LLM based on user preferences and weather.
        
        Args:
            rerank_with_llm: If False, skip LLM re-ranking (used for multi-city state searches
                             where a single combined rerank is done after merging all cities).
        """
        hobbies = user_prefs.get('hobbies') if user_prefs else None
        
        initial_pois = self.poi_manager.get_attractions(lat, lng, radius, hobbies, poi_type)
        
        duration_days = int(user_prefs.get('duration_days', 3)) if user_prefs else 3
        required_pois = max(10, duration_days * 3)
        city = user_prefs.get('city') if user_prefs else None
        
        # RAG / Intelligent LLM Fallback if primary providers fail or return too few
        if len(initial_pois) < required_pois and city:
            deficit = required_pois - len(initial_pois)
            print(f"[INFO_AGENT] POI providers returned {len(initial_pois)} for '{city}'. Need {required_pois} (deficit: {deficit}).")
            
            # 1. Try RAG first (only if initial_pois is empty to avoid double-fetching if we just need generic top-ups)
            if not initial_pois:
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
                            Extract tourist attractions from the following text about {city}.
                            Return a valid JSON array of objects.
                            Each object must have exactly these keys:
                            - "name": string
                            - "category": string (e.g. "heritage", "nature", "museum", "temple", "sightseeing")
                            - "description": string (short description)
                            - "rating": float (approximate rating out of 5.0, e.g. 4.5)
                            - "price_level": int (1 to 4)
                            
                            Text:
                            {rag_context}
                            
                            Respond ONLY with the JSON array. Do not include any other text.
                            """
                            response = self.llm.invoke([HumanMessage(content=prompt)])
                            content = response.content.strip()
                            if content.startswith("```json"):
                                content = content[7:-3].strip()
                            elif content.startswith("```"):
                                content = content[3:-3].strip()
                            
                            try:
                                parsed_pois = json.loads(content)
                                if isinstance(parsed_pois, list) and len(parsed_pois) > 0:
                                    print(f"[INFO_AGENT] Successfully extracted {len(parsed_pois)} POIs from RAG.")
                                    for p in parsed_pois:
                                        p['source'] = 'rag_fallback'
                                        p['id'] = p.get('name')
                                        p['location'] = {'lat': lat, 'lng': lng} 
                                    initial_pois.extend(parsed_pois)
                            except json.JSONDecodeError as e:
                                print(f"[INFO_AGENT] LLM RAG extraction failed JSON parse: {e}")
                except Exception as e:
                    print(f"[INFO_AGENT] RAG Fallback failed: {e}")
            
            # 2. Intelligent LLM Fallback (to guarantee we meet `required_pois`)
            deficit = required_pois - len(initial_pois)
            if deficit > 0 and self.llm:
                print(f"[INFO_AGENT] Still need {deficit} POIs for '{city}'. Triggering Intelligent LLM Generation.")
                # We cap generation at 25 to avoid massive token usage/timeouts
                gen_count = min(25, deficit + 5) 
                llm_prompt = f"""
                You are a travel expert. Generate a list of exactly {gen_count} famous, real tourist attractions, landmarks, and highly-rated places to visit in {city}.
                Do not include places that are just "City Center" or fake names. Ensure they are geographically located in or very near {city}.
                
                Return a valid JSON array of objects.
                Each object must have exactly these keys:
                - "name": string (Real name of the place)
                - "category": string (e.g. "heritage", "nature", "museum", "temple", "sightseeing", "entertainment")
                - "description": string (A very short, engaging 1-sentence description)
                - "rating": float (approximate real-world rating out of 5.0, e.g. 4.7)
                - "price_level": int (1 to 4)
                
                Respond ONLY with the JSON array. Do not include any other text or markdown wrappers.
                """
                try:
                    response = self.llm.invoke([HumanMessage(content=llm_prompt)])
                    content = response.content.strip()
                    if content.startswith("```json"):
                        content = content[7:-3].strip()
                    elif content.startswith("```"):
                        content = content[3:-3].strip()
                    
                    parsed_pois = json.loads(content)
                    if isinstance(parsed_pois, list):
                        print(f"[INFO_AGENT] LLM generated {len(parsed_pois)} POIs.")
                        for p in parsed_pois:
                            p['source'] = 'llm_fallback'
                            p['id'] = f"llm_{p.get('name', '').replace(' ', '_')}"
                            p['location'] = {'lat': lat, 'lng': lng} 
                        initial_pois.extend(parsed_pois)
                except Exception as e:
                    print(f"[INFO_AGENT] LLM Generation failed: {e}")

            # De-duplicate by name in case RAG and LLM generated the same
            seen_names = set()
            deduped = []
            for p in initial_pois:
                n = p.get('name', '').lower().strip()
                if n not in seen_names:
                    seen_names.add(n)
                    deduped.append(p)
            initial_pois = deduped
            
            # Geographic Validation: Enforce backend-side geographic validation to prevent invalid POI results
            if initial_pois and city and self.llm:
                poi_names = [p.get('name') for p in initial_pois if p.get('source') in ('rag_fallback', 'llm_fallback', 'rag')]
                if poi_names:
                    print(f"[INFO_AGENT] Validating {len(poi_names)} LLM/RAG POIs geographically for '{city}'.")
                    val_prompt = f"""
                    You are a strict geographic validator. The target city is {city}.
                    Review the following list of tourist attractions. Identify any attractions that are definitely NOT located in or near {city} (e.g., they belong to a different city like Hyderabad, Delhi, etc.).
                    
                    Attractions: {json.dumps(poi_names)}
                    
                    Return a JSON array containing ONLY the names of the invalid attractions. If all are valid, return []. Do not output markdown or any other text.
                    """
                    try:
                        val_res = self.llm.invoke([HumanMessage(content=val_prompt)])
                        val_content = val_res.content.strip()
                        if val_content.startswith("```json"): val_content = val_content[7:-3].strip()
                        elif val_content.startswith("```"): val_content = val_content[3:-3].strip()
                        invalid_names = json.loads(val_content)
                        if invalid_names and isinstance(invalid_names, list):
                            invalid_lower = [str(n).lower() for n in invalid_names]
                            initial_pois = [p for p in initial_pois if p.get('name', '').lower() not in invalid_lower]
                            print(f"[INFO_AGENT] Geographic validation removed {len(invalid_names)} invalid POIs: {invalid_names}")
                    except Exception as e:
                        print(f"[INFO_AGENT] Geographic validation failed: {e}")
            
            # Absolute worst-case scenario static fallback (if LLM is down)
            if not initial_pois:
                print(f"[INFO_AGENT] All fallbacks failed for '{city}'. Triggering Static Fallback.")
                initial_pois = [
                    {
                        "id": f"{city}_center", "name": f"{city.title()} City Center", "category": "sightseeing", 
                        "description": f"The vibrant main center of {city.title()}.", "rating": 4.0, "price_level": 2,
                        "location": {'lat': lat, 'lng': lng}, "source": "static_fallback"
                    },
                    {
                        "id": f"{city}_heritage", "name": f"{city.title()} Historical District", "category": "heritage", 
                        "description": f"Explore the rich heritage and culture of {city.title()}.", "rating": 4.5, "price_level": 2,
                        "location": {'lat': lat, 'lng': lng}, "source": "static_fallback"
                    }
                ]
        
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

    def get_attractions_for_state(self, state_name: str, state_cities: list, user_prefs: dict,
                                  weather_summary: str = None, total_number: int = 20):
        """
        Fetch and rank attractions across an entire Indian state using POIManager.

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
                    radius=15000, hobbies=hobbies, poi_type="tourist_attraction"
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
                    poi['state_city'] = state_name
                    all_pois.append(poi)
                    seen_ids.add(pid)

        print(f"[ATTRACTION_PIPELINE] State search completed. Total cities attempted: {len(state_cities)}. Successful: {success_count}. Failed: {failed_count}. Raw POIs deduplicated: {len(all_pois)}")

        if not all_pois:
            return []

        # Enforce cap BEFORE enrichment to save time
        all_pois.sort(key=lambda x: (x.get('rating') is None, -(float(x.get('rating', 0) or 0))))
        all_pois = all_pois[:40]

        # Use centralized enrichment
        if all_pois:
            all_pois = self.enrich_attractions(all_pois, state_name)

        # Single LLM rerank across the entire state pool
        if user_prefs and self.llm and len(all_pois) > 0:
            print(f"[STATE_SEARCH] Running single LLM rerank on {len(all_pois)} combined state POIs.")
            all_pois = self._rerank_attractions_with_llm(all_pois, user_prefs, weather_summary)

        print(f"[STATE_SEARCH] Returning all {len(all_pois)} state-wide POIs.")
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
            with urllib.request.urlopen(req) as res:
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
                        'fare': None
                    })
        except Exception as e:
            print(f"OSRM plan_routes error: {e}")
            
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
                    'fare': driving_route.get('fare')
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
            with urllib.request.urlopen(req) as res:
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
                        'fare': None
                    }
        except Exception as e:
            print(f"An unexpected error occurred during OSRM optimized route planning: {e}")
            
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
            print(f"Error in search_car_rentals: {str(e)}")
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
    def enrich_attractions(self, pois, city_name):
        """ONE centralized attraction enrichment function."""
        from concurrent.futures import ThreadPoolExecutor
        import hashlib
        
        # We need a global image cache to persist across instances but keyed safely
        global _GLOBAL_IMAGE_CACHE
        if '_GLOBAL_IMAGE_CACHE' not in globals():
            globals()['_GLOBAL_IMAGE_CACHE'] = {}

        def _enrich_single(poi):
            raw_id = poi.get('place_id') or poi.get('id')
            name = poi.get('name', 'Unknown Attraction')
            poi_id = str(raw_id) if raw_id else hashlib.md5(name.encode()).hexdigest()
            
            lat = float(poi.get('latitude') or poi.get('location', {}).get('lat', 0))
            lng = float(poi.get('longitude') or poi.get('location', {}).get('lng', 0))
            rating = poi.get('rating')
            if rating is None: rating = 4.0
            else: rating = float(rating)
            
            # Fetch Image
            cache_key = f"{city_name.lower().strip()}_{name.lower().strip()}"
            img_url = poi.get('image_url')
            if not img_url or not str(img_url).startswith("http"):
                if cache_key in globals()['_GLOBAL_IMAGE_CACHE']:
                    img_url = globals()['_GLOBAL_IMAGE_CACHE'][cache_key]
                else:
                    try:
                        img_url = self.poi_manager._fetch_pexels_image(name)
                        if not img_url:
                            img_url = self.poi_manager._fetch_pexels_image(f"{name} {city_name}")
                    except Exception:
                        img_url = None
                    
                    if not img_url:
                        hash_val = int(hashlib.md5(name.encode()).hexdigest(), 16)
                        placeholders = [
                            "https://images.unsplash.com/photo-1502602898657-3e9076113801",
                            "https://images.unsplash.com/photo-1564507592333-c60657eea523",
                            "https://images.unsplash.com/photo-1540959733332-eab4deabeeaf",
                            "https://images.unsplash.com/photo-1537996194471-e657df975ab4",
                            "https://images.unsplash.com/photo-1512453979798-5ea266f8880c",
                            "https://images.unsplash.com/photo-1496442226666-8d4d0e62e6e9"
                        ]
                        img_url = placeholders[hash_val % len(placeholders)] + "?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80"
                    
                    globals()['_GLOBAL_IMAGE_CACHE'][cache_key] = img_url

            # Description Generation
            desc = poi.get('description', '').strip()
            if not desc or len(desc) < 20:
                cat = str(poi.get('category', 'tourist attraction')).replace('_', ' ')
                desc = f"A popular {cat} located in {city_name}, highly rated by visitors for its experience and atmosphere."
                
            duration = poi.get('estimated_duration')
            if not duration:
                duration = self.estimate_duration(poi.get('category', 'tourist_attraction'), {})

            return {
                "id": poi_id,
                "name": name,
                "description": desc,
                "address": poi.get('address') or f"{name}, {city_name}",
                "latitude": lat,
                "longitude": lng,
                "rating": rating,
                "review_count": int(poi.get('review_count') or poi.get('user_ratings_total', 0)),
                "price": poi.get('price_level', 0),
                "duration": duration,
                "category": poi.get('category', 'tourist_attraction'),
                "image_url": img_url,
                "source": poi.get('source', 'unknown'),
                "match_percentage": poi.get('match_percentage', 100)
            }
            
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(pois)))) as executor:
            enriched = list(executor.map(_enrich_single, pois))
            
        return enriched

