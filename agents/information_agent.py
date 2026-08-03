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

from services.maps_api import POIApi
from services.weather_api import WeatherService
from services.car_rental_api import CarRentalService
from services.fuel_price_api import get_gas_price

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
    def __init__(self, maps_api_key=None, car_api_key=None, llm_model_name="gemini-flash-lite-latest"):
        """Initialize the InformationAgent with API keys and LLM model name."""
        self.maps_api_key = maps_api_key or os.getenv("MAPS_API_KEY")
        self.rapidapi_key = car_api_key or os.getenv("RAPIDAPI_KEY")
        
        if not self.maps_api_key:
            raise ValueError("MAPS_API_KEY is required for InformationAgent.")

        self.gmaps = googlemaps.Client(key=self.maps_api_key)
        self.poi_api = POIApi(self.maps_api_key)
        self.weather_service = WeatherService()
        self.car_rental_service = None
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
        You are an expert India travel recommender. Your task is to rank the provided list of attractions
        based on the user's preferences, attraction details, and current weather summary.
        Only consider attractions within India.

        User Preferences:
        {user_prefs_str}

        Weather Summary for the trip period:
        {weather_str}

        Attractions List (with details including 'id', 'name', 'category', 'estimated_duration', 'price_level', 'rating', and 'description'):
        {attractions_str}

        Please rank attractions considering:
        1.  **User Hobbies & Interests**: Match with hobbies (e.g., '{user_prefs.get('hobbies', 'general sightseeing')}').
            India-specific: temples/ghats for spirituality, forts/palaces for history, beaches for leisure, etc.
        2.  **User Health & Accessibility**: Consider health ('{user_prefs.get('health', 'good')}') and accessibility.
        3.  **Suitability for Children**: If traveling with kids (Kids: '{user_prefs.get('kids', 'no')}'), prioritize family-friendly.
        4.  **Budget Constraints**: Align with budget '{user_prefs.get('budget', 'medium')}'.
        5.  **Weather Impact**: Prioritize indoor/outdoor activities based on weather.
        6.  CRITICAL: Completely avoid and remove any "travel agencies", "tour operators", "food courts", "restaurants", or "booking services" from the recommendations! Only recommend actual tourist attractions, landmarks, parks, museums, viewpoints, temples, beaches, forts, etc.

        You must categorize the attractions into two lists:
        1. "interest_based": Attractions that directly match the user's hobbies and interests. Rank them from MOST to LEAST recommended.
        2. "popular_fallback": If there are not enough attractions matching the user's specific interests, select the city's most famous and highly rated tourist attractions to include here as additional suggestions.

        Return a JSON dictionary containing these two lists of attraction IDs.
        Output MUST be a valid JSON object. Example:
        {{
            "interest_based": ["id1", "id2"],
            "popular_fallback": ["id3", "id4"]
        }}

        Only return the JSON object. No other text.
        """
        return prompt

    def _rerank_attractions_with_llm(self, attractions_list: list, user_prefs: dict, weather_summary: str = None):
        """Re-rank attractions using an LLM based on user preferences and weather."""
        if not self.llm:
            print("LLM not available for re-ranking. Returning original list.")
            return attractions_list
        if not attractions_list:
            return []
        if not user_prefs:
             print("User preferences not provided for LLM re-ranking. Returning original list.")
             return attractions_list

        attractions_for_llm = []
        for attr in attractions_list:
            attractions_for_llm.append({
                "id": attr.get("id"), "name": attr.get("name"), "category": attr.get("category"),
                "description": attr.get("description", attr.get("name","No description available.")),
                "estimated_duration": attr.get("estimated_duration"),
                "price_level": attr.get("price_level"), "rating": attr.get("rating"),
            })
        
        attraction_ids_tuple = tuple(sorted([attr.get('id', '') for attr in attractions_for_llm]))
        cache_key = self._get_rerank_cache_key(user_prefs, attraction_ids_tuple, weather_summary)

        if cache_key in self.llm_rerank_cache:
            print(f"Returning cached LLM re-ranking for key: {cache_key}")
            ranked_ids = self.llm_rerank_cache[cache_key]
        else:
            prompt_str = self._create_llm_rerank_prompt(user_prefs, attractions_for_llm, weather_summary)
            messages = [
                SystemMessage(content="You are an expert travel recommender. Your goal is to rank attractions based on user preferences, attraction details, and weather conditions. Ensure a good balance of attraction categories if appropriate."),
                HumanMessage(content=prompt_str)
            ]
            try:
                print(f"[INFO_AGENT_LLM] Requesting LLM re-ranking for {len(attractions_for_llm)} items. Cache key: {cache_key}")
                response = self.llm.invoke(messages)
                llm_output_content = response.content
                
                ranked_ids_data = {}
                try:
                    if isinstance(llm_output_content, list):
                        llm_output_content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in llm_output_content)
                    if llm_output_content.strip().startswith("```json"):
                        llm_output_content = llm_output_content.strip()[7:]
                        if llm_output_content.strip().endswith("```"):
                            llm_output_content = llm_output_content.strip()[:-3]
                    
                    parsed_data = json.loads(llm_output_content.strip())
                    if isinstance(parsed_data, dict):
                        ranked_ids_data = parsed_data
                    elif isinstance(parsed_data, list):
                        # Fallback if LLM ignores instructions and returns a list
                        ranked_ids_data = {"interest_based": parsed_data, "popular_fallback": []}
                    else:
                        print(f"[INFO_AGENT_LLM_ERROR] LLM output was not a dict: {parsed_data}")
                        raise ValueError("LLM output not in expected dictionary format.")

                except (json.JSONDecodeError, ValueError) as e:
                    print(f"[INFO_AGENT_LLM_ERROR] Parsing LLM re-ranking response: {e}. LLM Raw Output: '{llm_output_content}'")
                    return attractions_list 
                
                self.llm_rerank_cache[cache_key] = ranked_ids_data
                print(f"[INFO_AGENT_LLM] Cached LLM re-ranking for key: {cache_key}")

            except Exception as e:
                print(f"[INFO_AGENT_LLM_ERROR] Calling LLM for re-ranking: {e}")
                return attractions_list

        id_to_attraction_map = {attr['id']: attr for attr in attractions_list}
        ordered_attractions = []
        seen_ids = set()
        
        # Process interest-based first
        for id_ in ranked_ids_data.get("interest_based", []):
            if id_ in id_to_attraction_map and id_ not in seen_ids:
                attr = id_to_attraction_map[id_]
                attr["recommendation_type"] = "interest_based"
                ordered_attractions.append(attr)
                seen_ids.add(id_)
                
        # Then process popular fallback
        for id_ in ranked_ids_data.get("popular_fallback", []):
            if id_ in id_to_attraction_map and id_ not in seen_ids:
                attr = id_to_attraction_map[id_]
                attr["recommendation_type"] = "popular"
                ordered_attractions.append(attr)
                seen_ids.add(id_)
        
        # Add any remaining unranked attractions as popular
        for attr in attractions_list:
            if attr.get('id') not in seen_ids: 
                attr["recommendation_type"] = "popular"
                ordered_attractions.append(attr)
        
        print(f"[INFO_AGENT_LLM] Re-ranked list size: {len(ordered_attractions)}")
        return ordered_attractions

    def validate_indian_location(self, location_name: str) -> bool:
        """
        Uses Google Maps Geocoding API to check if the given location is in India.
        This provides robust validation for all Indian cities, towns, and villages.
        Handles disputed territories (like Kashmir/Arunachal) where Google omits the country component.
        """
        try:
            results = self.gmaps.geocode(location_name)
            
            # If no results from geocode, fallback to Places API (good for small tourist spots like Auli)
            if not results:
                places_result = self.gmaps.places(query=location_name)
                if not places_result or not places_result.get('results'):
                    return False
                first_place = places_result['results'][0]
                lat = first_place['geometry']['location']['lat']
                lng = first_place['geometry']['location']['lng']
                # Check bounding box of India
                return 6.4 <= lat <= 36.0 and 68.0 <= lng <= 97.5
                
            first_result = results[0]
            
            # Check address components for country
            has_country = False
            is_india = False
            address_components = first_result.get('address_components', [])
            for component in address_components:
                if 'country' in component.get('types', []):
                    has_country = True
                    short_name = component.get('short_name', '').upper()
                    long_name = component.get('long_name', '').lower()
                    if short_name == 'IN' or long_name == 'india':
                        is_india = True
                    break
            
            if has_country:
                return is_india
                
            # If no country component is returned (common for disputed regions like Leh, Srinagar, Ziro)
            # Check if the coordinates fall within the approximate bounding box of India
            lat = first_result['geometry']['location']['lat']
            lng = first_result['geometry']['location']['lng']
            if 6.4 <= lat <= 36.0 and 68.0 <= lng <= 97.5:
                return True
                
            return False
        except Exception as e:
            print(f"[ERROR] Exception in validate_indian_location for '{location_name}': {e}")
            try:
                places_result = self.gmaps.places(query=location_name)
                if places_result and places_result.get('results'):
                    first_place = places_result['results'][0]
                    lat = first_place['geometry']['location']['lat']
                    lng = first_place['geometry']['location']['lng']
                    return 6.4 <= lat <= 36.0 and 68.0 <= lng <= 97.5
            except Exception as inner_e:
                print(f"[ERROR] Places API fallback failed in validate_indian_location for '{location_name}': {inner_e}")
            return False

    def city2geocode(self, city: str):
        """Convert city name to geographic coordinates (latitude and longitude)."""
        try:
            print(f"[DEBUG city2geocode] Geocoding city: '{city}'")
            coordinates = self.gmaps.geocode(city)
            print(f"[DEBUG city2geocode] Result: {coordinates}")
            
            if not coordinates:
                print(f"[DEBUG city2geocode] Geocode failed, falling back to Places API for '{city}'")
                places_result = self.gmaps.places(query=city)
                if not places_result or not places_result.get('results'):
                    print(f"[DEBUG city2geocode] Places API fallback failed for '{city}'")
                    return None
                first_place = places_result['results'][0]
                return first_place['geometry']['location']
                
            return coordinates[0]['geometry']['location']
        except Exception as e:
            import traceback
            print(f"[ERROR city2geocode] Error for '{city}': {e}")
            traceback.print_exc()
            try:
                places_result = self.gmaps.places(query=city)
                if places_result and places_result.get('results'):
                    first_place = places_result['results'][0]
                    return first_place['geometry']['location']
            except Exception as inner_e:
                print(f"[ERROR city2geocode] Places API fallback failed for '{city}': {inner_e}")
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
        location = (lat, lng)
        initial_fetch_limit = 30 # Fetch more initially to allow for better LLM ranking
        
        results = []
        
        # 1. Targeted Fetching based on hobbies
        hobbies = user_prefs.get('hobbies') if user_prefs else None
        if hobbies:
            try:
                print(f"[INFO_AGENT] Fetching specific places for hobbies: '{hobbies}'")
                hobby_results = self.gmaps.places_nearby(
                    location=location, radius=radius, keyword=hobbies, language='en'
                ).get('results', [])
                results.extend(hobby_results)
            except Exception as e:
                print(f"Error fetching places_nearby with hobbies: {e}")

        # 2. Fallback / Padding with generic popular spots and shopping malls
        if len(results) < initial_fetch_limit:
            try:
                print(f"[INFO_AGENT] Fetching generic '{poi_type}' to pad results.")
                generic_results = self.gmaps.places_nearby(
                    location=location, radius=radius, type=poi_type, language='en'
                ).get('results', [])
                
                # Fetch popular shopping malls and famous places
                popular_results = self.gmaps.places_nearby(
                    location=location, radius=radius, keyword="famous popular sightseeing shopping malls", language='en'
                ).get('results', [])
                
                generic_results.extend(popular_results)
                
                # Filter out undesired places like travel agencies, restaurants, etc.
                undesired_types = {
                    "travel_agency", "tour_operator", "restaurant", "food", 
                    "cafe", "meal_takeaway", "meal_delivery", "lodging",
                    "real_estate_agency", "car_rental", "atm", "bank", "convenience_store"
                }
                
                generic_results = [r for r in generic_results if not undesired_types.intersection(set(r.get("types", [])))]
                results = [r for r in results if not undesired_types.intersection(set(r.get("types", [])))]
                
                # Append while avoiding duplicates by place_id
                seen_ids = {r.get('place_id') for r in results if r.get('place_id')}
                for gr in generic_results:
                    pid = gr.get('place_id')
                    if pid and pid not in seen_ids:
                        results.append(gr)
                        seen_ids.add(pid)
            except Exception as e:
                print(f"Error fetching places_nearby generic: {e}")

        initial_pois = []
        print(f"[INFO_AGENT] Fetched {len(results)} total raw places. Processing up to {initial_fetch_limit} for details.")
        
        # Define the fields to request from Place Details API.
        # 'types' and 'photos' are not valid for Place Details 'fields' parameter.
        # 'types' are available from the places_nearby result.
        # 'photos' (photo_references) are available from places_nearby result.
        place_details_fields = [
            'name', 'rating', 'price_level', 'opening_hours', 'formatted_address', 
            'geometry/location', # Basic geometry is sufficient
            'place_id', # Essential
            'user_ratings_total', 'website', 'editorial_summary', 
            'international_phone_number', 'permanently_closed', 'business_status'
            # Valid photo field is 'photo', but it returns an array of photo objects.
            # It's often better to get photo_references from nearby_search and construct URLs.
        ]


        for place in results[:initial_fetch_limit]: 
            pid = place.get('place_id')
            if not pid: continue
            try:
                # Get types directly from the 'place' object from nearby search
                place_types_list = place.get('types', ["unknown"])
                primary_category_from_place = place_types_list[0] if place_types_list else "unknown"

                # Get photo references directly from the 'place' object
                photo_references_from_place = []
                if place.get('photos'):
                    for photo_info_nearby in place['photos'][:1]: # Get first photo reference
                         if photo_info_nearby.get('photo_reference'):
                            photo_references_from_place.append(photo_info_nearby['photo_reference'])
                
                # Fetch details, excluding 'types' and 'photos' from fields
                details_response = self.poi_api.get_poi_details(
                    place_id=pid,
                    fields=place_details_fields 
                )
                details = details_response.get('result', {})
                if not details: 
                    print(f"[WARN] No details found for place_id {pid}. Skipping.")
                    continue

                # Ensure location_data is an object with lat/lng, even if values are None
                raw_location = details.get('geometry', {}).get('location', {})
                location_data = {
                    'lat': raw_location.get('lat'),
                    'lng': raw_location.get('lng')
                }
                if not isinstance(location_data['lat'], (int, float)):
                    print(f"[WARN] Invalid or missing lat for place_id {pid}. Name: {details.get('name')}. Setting to None.")
                    location_data['lat'] = None
                if not isinstance(location_data['lng'], (int, float)):
                    print(f"[WARN] Invalid or missing lng for place_id {pid}. Name: {details.get('name')}. Setting to None.")
                    location_data['lng'] = None
                
                description = details.get('editorial_summary', {}).get('overview', '')
                if not description: description = details.get('name', 'No description available.')
                
                # Construct image URL from photo reference, default to None
                image_url = None
                if photo_references_from_place and self.maps_api_key and photo_references_from_place[0]:
                    image_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=400&photoreference={photo_references_from_place[0]}&key={self.maps_api_key}"
                elif not photo_references_from_place or not photo_references_from_place[0]:
                    print(f"[WARN] No photo reference for place_id {pid}. Name: {details.get('name')}. Image URL will be None.")

                initial_pois.append({
                    'id': pid, 
                    'name': details.get('name'), 
                    'rating': details.get('rating'), 
                    'user_ratings_total': details.get('user_ratings_total'), 
                    'price_level': details.get('price_level'), 
                    'opening_hours': details.get('opening_hours', {}).get('weekday_text'), 
                    'address': details.get('formatted_address'), 
                    'location': location_data, 
                    'category': primary_category_from_place,
                    'types': place_types_list,
                    'estimated_duration': self.estimate_duration(primary_category_from_place, details),
                    'website': details.get('website'), 
                    'description': description,
                    'photo_references': photo_references_from_place,
                    'image_url': image_url 
                })
            except Exception as e:
                print(f"[ERROR] Exception during processing of place_id {pid} in get_attractions: {e}")
                continue
        
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

    def get_attractions_for_state(self, state_name: str, user_prefs: dict,
                                  weather_summary: str = None, total_number: int = 20):
        """
        Fetch and rank attractions across an entire Indian state using Text Search.
        """
        all_pois = []
        seen_ids = set()

        hobbies = user_prefs.get('hobbies') if user_prefs else None
        
        queries = [
            f"top tourist attractions in {state_name}",
            f"famous forts and historical places in {state_name}",
            f"famous temples and spiritual places in {state_name}"
        ]
        
        if hobbies:
            queries.append(f"top {hobbies} places in {state_name}")
            
        for query in queries:
            print(f"[STATE_SEARCH] Running text search query: '{query}'")
            try:
                results = self.gmaps.places(query=query).get('results', [])
                for place in results:
                    pid = place.get('place_id')
                    if pid and pid not in seen_ids:
                        place['state_city'] = state_name
                        
                        # Filter out undesired types just in case
                        undesired_types = {
                            "travel_agency", "tour_operator", "restaurant", "food", 
                            "cafe", "meal_takeaway", "meal_delivery", "lodging",
                            "real_estate_agency", "car_rental", "atm", "bank", "convenience_store"
                        }
                        if not undesired_types.intersection(set(place.get("types", []))):
                            all_pois.append(place)
                            seen_ids.add(pid)
            except Exception as e:
                print(f"[STATE_SEARCH_ERROR] Query '{query}' failed: {e}")
                
        print(f"[STATE_SEARCH] Found {len(all_pois)} raw POIs across state {state_name}.")
        
        if not all_pois:
            return []
            
        # Add basic info to POIs from their first search result structure
        for poi in all_pois:
            poi['id'] = poi.get('place_id')
            if 'geometry' in poi and 'location' in poi['geometry']:
                poi['location'] = poi['geometry']['location']

        # Sort combined pool by rating before LLM rerank
        all_pois.sort(key=lambda x: (x.get('rating') is None, -(float(x.get('rating', 0) or 0))))
        
        # Take top 40 for LLM to rank
        all_pois = all_pois[:40]

        # Single LLM rerank across the entire state pool
        if user_prefs and self.llm and len(all_pois) > 0:
            print(f"[STATE_SEARCH] Running single LLM rerank on {len(all_pois)} combined state POIs.")
            all_pois = self._rerank_attractions_with_llm(all_pois, user_prefs, weather_summary)

        print(f"[STATE_SEARCH] Returning all {len(all_pois)} state-wide POIs.")
        return all_pois

    def get_accommodations(self, lat: float, lng: float, budget: str, number: int = 4):
        """Fetch accommodation options based on user budget."""
        location = (lat, lng)
        if budget and budget.lower() == 'low':
            keyword = "hostel cheap budget hotel guest house"
        elif budget and budget.lower() == 'high':
            keyword = "luxury hotel 5 star resort"
        else:
            keyword = "hotel"
            
        print(f"[INFO_AGENT] Fetching accommodations with keyword: '{keyword}'")
        try:
            results = self.gmaps.places_nearby(
                location=location, radius=15000, keyword=keyword, type="lodging", language='en'
            ).get('results', [])
        except Exception as e:
            print(f"Error fetching accommodations: {e}")
            return []
            
        accommodations = []
        for place in results[:10]:
            pid = place.get('place_id')
            if not pid: continue
            try:
                details_response = self.poi_api.get_poi_details(
                    place_id=pid,
                    fields=['name', 'rating', 'user_ratings_total', 'price_level', 'formatted_address', 'website']
                )
                details = details_response.get('result', {})
                if not details: continue
                
                # Extract one photo if available
                image_url = None
                if place.get('photos') and self.maps_api_key:
                    photo_ref = place['photos'][0].get('photo_reference')
                    if photo_ref:
                        image_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=400&photoreference={photo_ref}&key={self.maps_api_key}"

                accommodations.append({
                    'id': pid,
                    'name': details.get('name'),
                    'rating': details.get('rating'),
                    'user_ratings_total': details.get('user_ratings_total'),
                    'price_level': details.get('price_level'),
                    'address': details.get('formatted_address'),
                    'website': details.get('website'),
                    'image_url': image_url,
                    'type': 'accommodation'
                })
            except Exception as e:
                print(f"[ERROR] Exception processing accommodation {pid}: {e}")
                
        # Sort by rating
        accommodations.sort(key=lambda x: (x.get('rating') is None, -(float(x.get('rating', 0.0) or 0.0))))
        return accommodations[:number]

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

    def plan_routes(self, origin: str, destination: str):
        """
        Route Planning (Simple A to B for multiple modes).

        Args:
            origin: Starting point (address, place name, or lat/lng tuple/dict)
            destination: End point (address, place name, or lat/lng tuple/dict)

        Returns:
            List of dictionaries, each representing a travel mode, or an empty list.
            Example format:
            [
                {
                    'mode': str,                # e.g., 'driving', 'transit'
                    'distance': str,            # Formatted distance text (e.g., "10.2 miles")
                    'duration': str,            # Formatted duration text (e.g., "25 mins")
                    'distance_meters': int,     # Raw distance in meters
                    'duration_seconds': int,    # Raw duration in seconds
                    'fare': str | None          # Estimated fare text (mostly for transit)
                },
                ...
            ]
        """
        modes = ['driving', 'walking', 'bicycling', 'transit']
        routes = []
        for mode in modes:
            try:
                # Using 'en' for consistent address resolution and international compatibility
                directions = self.gmaps.directions(
                    origin, destination, mode=mode, language='en'
                )
                if not directions:
                    continue

                # Ensure legs exist and are not empty
                if not directions[0].get('legs'):
                    print(f"Warning: Route for mode '{mode}' from '{origin}' to '{destination}' lacks 'legs' data.")
                    continue
                leg = directions[0]['legs'][0]

                # Ensure distance and duration exist in the leg
                if 'distance' not in leg or 'duration' not in leg:
                     print(f"Warning: Leg for mode '{mode}' from '{origin}' to '{destination}' lacks distance or duration data.")
                     continue

                info = {
                    'mode': mode,
                    'distance': leg['distance']['text'],
                    'duration': leg['duration']['text'],
                    'distance_meters': leg['distance']['value'], # Raw distance in meters
                    'duration_seconds': leg['duration']['value']  # Raw duration in seconds
                }
                # Add fare info if available
                if 'fare' in directions[0]:
                    info['fare'] = directions[0]['fare'].get('text')
                routes.append(info)
            except googlemaps.exceptions.ApiError as e:
                 print(f"Error planning route for mode '{mode}' from '{origin}' to '{destination}': {e}")
            except IndexError:
                 print(f"Index error processing route result for mode '{mode}' from '{origin}' to '{destination}' (likely missing 'legs').")
            except KeyError as e:
                 print(f"Key error processing route result for mode '{mode}' from '{origin}' to '{destination}': {e} (likely missing 'distance' or 'duration').")
            except Exception as e:
                 print(f"An unexpected error occurred during route planning for mode '{mode}': {e}")
        return routes

    def plan_with_waypoints(self, origin: str, destination: str, waypoints: list,
                                            mode: str = 'driving', departure_time: datetime = None):
        """
        Plans an optimized route visiting a list of waypoints between an origin and destination.
        Uses the Google Maps Directions API with waypoint optimization (`optimize_waypoints=True`).

        Args:
            origin: Starting point (address, place name, or lat/lng tuple/dict)
            destination: End point (address, place name, or lat/lng tuple/dict)
            waypoints: List of intermediate points (list of strings, lat/lng tuples/dicts)
            mode: Travel mode (default: 'driving'). Optimization works best for 'driving'.
            departure_time: Optional datetime object (default: now) for traffic estimation.

        Returns:
            Dictionary with optimized route details, or None if no route is found.
            Example format:
            {
                'path_sequence': List[str],         # List of addresses in optimized order (Origin, WptX, WptY,..., Dest)
                'waypoint_original_indices': List[int], # Order original waypoints were visited (0-based index)
                'total_duration_text': str,         # Formatted total duration (e.g., "2 hours 30 mins")
                'total_duration_seconds': int,      # Raw total duration in seconds
                'total_duration_in_traffic_text': str | None, # Formatted duration with traffic (if available)
                'total_duration_in_traffic_seconds': int | None, # Raw duration with traffic (if available)
                'total_distance_text': str,         # Formatted total distance (e.g., "150.5 km / 93.5 miles")
                'total_distance_meters': int,       # Raw total distance in meters
                'fare': str | None                  # Estimated fare text (rare for driving)
            }
        """
        # Handle empty waypoints list by falling back to simple A-B route planning
        if not waypoints:
            print("Warning: No waypoints provided. Calling standard plan_routes for A-B.")
            simple_route_options = self.plan_routes(origin, destination)
            # Find the driving route from the simple options
            driving_route = next((r for r in simple_route_options if r['mode'] == 'driving'), None)
            if driving_route:
                 # Addresses from API are resolved; use original input if unavailable in fallback
                 start_addr = origin if isinstance(origin, str) else f"Coord: {origin}"
                 end_addr = destination if isinstance(destination, str) else f"Coord: {destination}"
                 return {
                    'path_sequence': [start_addr, end_addr], # Simplified path
                    'waypoint_original_indices': [],
                    'total_duration_text': driving_route['duration'],
                    'total_duration_seconds': driving_route['duration_seconds'],
                    'total_duration_in_traffic_text': None, # Not available from simple plan_routes call here
                    'total_duration_in_traffic_seconds': None,
                    'total_distance_text': driving_route['distance'],
                    'total_distance_meters': driving_route['distance_meters'],
                    'fare': driving_route.get('fare')
                 }
            else:
                print(f"Could not find a driving route from {origin} to {destination} in fallback.")
                return None

        # Set departure time to now if not specified
        if departure_time is None:
            departure_time = datetime.now()

        print(f"Planning optimized route: {origin} -> Waypoints -> {destination} for mode '{mode}'")

        try:
            # Call Google Maps Directions API
            # language='en' affects instruction text, addresses usually resolve globally
            directions_result = self.gmaps.directions(
                origin,
                destination,
                waypoints=waypoints,
                optimize_waypoints=True, # Key parameter for optimization
                mode=mode,
                departure_time=departure_time,
                language='en'
            )

            # Check if API returned a valid result
            if not directions_result:
                print("No route found for the given points and mode.")
                return None

            # Get the first recommended route
            route = directions_result[0]
            # 'legs' are the segments between points (origin->wpt1, wpt1->wpt2, ..., wptN->dest)
            legs = route['legs']

            # Calculate total duration and distance by summing up values from each leg
            total_duration_sec = sum(leg['duration']['value'] for leg in legs)
            total_distance_m = sum(leg['distance']['value'] for leg in legs)

            # Calculate duration with traffic if available for all legs
            total_duration_traffic_sec = None
            if all('duration_in_traffic' in leg for leg in legs):
                 total_duration_traffic_sec = sum(leg['duration_in_traffic']['value'] for leg in legs)

            # Reconstruct the path sequence using resolved addresses from the API response
            # Start address is from the first leg; end addresses are from each leg
            path_sequence = [legs[0]['start_address']] + [leg['end_address'] for leg in legs]

            # Get the optimized order of the *original* waypoints list (0-based indices)
            optimized_indices = route.get('waypoint_order', [])

            # Prepare the result dictionary
            result = {
                'path_sequence': path_sequence,
                'waypoint_original_indices': optimized_indices,
                'total_duration_text': format_duration(total_duration_sec),
                'total_duration_seconds': total_duration_sec,
                'total_distance_text': format_distance(total_distance_m),
                'total_distance_meters': total_distance_m,
                'fare': route.get('fare', {}).get('text') # Extract fare text if present
            }

            # Add traffic duration details if calculated
            if total_duration_traffic_sec is not None:
                result['total_duration_in_traffic_text'] = format_duration(total_duration_traffic_sec)
                result['total_duration_in_traffic_seconds'] = total_duration_traffic_sec
            else:
                 result['total_duration_in_traffic_text'] = None
                 result['total_duration_in_traffic_seconds'] = None

            return result

        # Handle potential API errors or other exceptions
        except googlemaps.exceptions.ApiError as e:
            print(f"Error planning optimized route: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred during optimized route planning: {e}")
            # Optionally re-raise or log the full traceback for debugging
            # import traceback
            # traceback.print_exc()
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
            'detailed_forecast': weather_data,
            'summary': None
        }
        
        # Generate summary if requested
        if summary:
            # Create a prompt for the summary writer
            weather_info = json.dumps(weather_data, indent=2)
            prompt = f"""
            Summarize the following weather forecast in a concise paragraph (max 100 words).
            Include key information about temperature ranges, precipitation, and any notable weather conditions.
            Also mention any precautions travelers should take based on the forecast.
            
            Weather data:
            {weather_info}
            """
            
            # Generate the summary
            messages = [
                SystemMessage(content="You are a helpful weather assistant that provides concise summaries of weather forecasts for travelers."),
                HumanMessage(content=prompt)
            ]
            
            # Add the summary to the result
            result['summary'] = self.weather_summary_writer.invoke(messages)
        
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
                return self._get_mock_car_data(top_n)
            
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
            return cars[:top_n] if cars else self._get_mock_car_data(top_n)
            
        except Exception as e:
            print(f"Error in search_car_rentals: {str(e)}")
            return self._get_mock_car_data()
            
            
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
        """Search for nearby restaurants and provide their details.
        
        Args:
            lat (float): Latitude
            lng (float): Longitude
            radius (int): Search radius (meters)
        
        Returns:
            dict: Dictionary containing information about nearby restaurants (top 3 by rating).
                  Returns mock data if API calls fail.
        """
        try:
            # Check if POI API is available
            if not self.poi_api:
                raise Exception("POI API is not initialized")

            # Search for nearby restaurants
            restaurants_result = self.poi_api.get_nearby_places(
                location=(lat, lng),
                type='restaurant',
                radius=radius
            )
            
            # Process restaurant information
            processed_restaurants = []
            # Sort all fetched restaurants by rating (descending) before further processing
            # Handle cases where rating might be missing by defaulting to 0 for sorting
            all_fetched_restaurants = restaurants_result.get('results', [])
            all_fetched_restaurants.sort(key=lambda p: p.get('rating', 0), reverse=True)

            for place in all_fetched_restaurants[:3]:  # Only take the top 3 after sorting
                try:
                    # Get detailed information
                    place_details = self.poi_api.get_poi_details(
                        place_id=place['place_id'],
                        fields=['name', 'rating', 'price_level', 'formatted_address', 'photo', 'type', 'geometry']
                    )
                    
                    if not place_details or 'result' not in place_details:
                        continue
                        
                    place_details = place_details['result']
                    
                    # Get photos
                    photos = []
                    if 'photos' in place:  # Get photo info from the original search result
                        for photo in place['photos'][:3]:  # Up to 3 photos
                            photo_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={photo['photo_reference']}&key={self.maps_api_key}"
                            photos.append({
                                'url': photo_url,
                                'width': photo.get('width', 800),
                                'height': photo.get('height', 600)
                            })
                    
                    restaurant = {
                        'name': place_details.get('name', 'Unknown Restaurant'),
                        'type': 'restaurant',
                        'rating': place_details.get('rating', 0),
                        'price_level': place_details.get('price_level', 0),
                        'address': place_details.get('formatted_address', 'Unknown address'),
                        'photos': photos,
                        'features': self._get_restaurant_features(place)  # Use type info from the original search result
                    }
                    processed_restaurants.append(restaurant)
                except Exception as e:
                    print(f"Error processing restaurant info: {str(e)}")
                    continue
            
            return {
                'restaurants': processed_restaurants
            }
            
        except Exception as e:
            print(f"Error searching nearby places: {str(e)}")
            # Return mock data
            return {
                'restaurants': [
                    {
                        'name': 'Sample Restaurant',
                        'type': 'restaurant',
                        'rating': 4.5,
                        'price_level': 2,
                        'address': 'Sample Address',
                        'photos': [
                            {
                                'url': 'https://example.com/photo1.jpg',
                                'width': 800,
                                'height': 600
                            }
                        ],
                        'features': 'Cuisine: Chinese, Western'
                    }
                ]
            }
    
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
        




