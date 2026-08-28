import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from agents.chat_agent import ChatAgent
from agents.information_agent import InformationAgent
from agents.retrieval_agent import RetrievalAgent
from agents.recommend_agent import RecommendAgent
from agents.strategy_agent import StrategyAgent
from agents.route_agent import RouteAgent
from agents.budget_agent import BudgetAgent
from agents.communication_agent import CommunicationAgent
from agents.transit_agent import TransitAgent
from datetime import datetime, timedelta
from langchain_core.messages import AIMessage
import json
from utils import extract_number

import traceback

# ---------------------------------------------------------------------------
# Comprehensive mapping of Indian states / UTs to their top tourism cities.
# Keys are lowercase state names (and common aliases).
# 'display' is the properly formatted name shown to users.
# 'cities' are the top tourism hubs — limited to ≤5 for API efficiency.
# ---------------------------------------------------------------------------
INDIA_STATES_MAP = {
    # ── Major States ──────────────────────────────────────────────────────
    "rajasthan":          {"display": "Rajasthan",            "cities": ["Jaipur", "Udaipur", "Jodhpur", "Jaisalmer", "Pushkar"]},
    "kerala":             {"display": "Kerala",               "cities": ["Kochi", "Munnar", "Alleppey", "Thiruvananthapuram", "Wayanad"]},
    "goa":                {"display": "Goa",                  "cities": ["Panaji", "Calangute", "Vasco da Gama", "Margao", "Anjuna"]},
    "himachal pradesh":   {"display": "Himachal Pradesh",     "cities": ["Shimla", "Manali", "Dharamshala", "Dalhousie", "Kasauli"]},
    "himachal":           {"display": "Himachal Pradesh",     "cities": ["Shimla", "Manali", "Dharamshala", "Dalhousie", "Kasauli"]},
    "uttarakhand":        {"display": "Uttarakhand",          "cities": ["Rishikesh", "Haridwar", "Mussoorie", "Nainital", "Auli"]},
    "uttaranchal":        {"display": "Uttarakhand",          "cities": ["Rishikesh", "Haridwar", "Mussoorie", "Nainital", "Auli"]},
    "tamil nadu":         {"display": "Tamil Nadu",           "cities": ["Chennai", "Madurai", "Ooty", "Mahabalipuram", "Thanjavur"]},
    "tamilnadu":          {"display": "Tamil Nadu",           "cities": ["Chennai", "Madurai", "Ooty", "Mahabalipuram", "Thanjavur"]},
    "karnataka":          {"display": "Karnataka",            "cities": ["Bangalore", "Mysore", "Hampi", "Coorg", "Badami"]},
    "maharashtra":        {"display": "Maharashtra",          "cities": ["Mumbai", "Pune", "Aurangabad", "Nashik", "Mahabaleshwar"]},
    "uttar pradesh":      {"display": "Uttar Pradesh",        "cities": ["Agra", "Varanasi", "Lucknow", "Mathura", "Prayagraj"]},
    "up":                 {"display": "Uttar Pradesh",        "cities": ["Agra", "Varanasi", "Lucknow", "Mathura", "Prayagraj"]},
    "west bengal":        {"display": "West Bengal",          "cities": ["Kolkata", "Darjeeling", "Siliguri", "Shantiniketan", "Bishnupur"]},
    "bengal":             {"display": "West Bengal",          "cities": ["Kolkata", "Darjeeling", "Siliguri", "Shantiniketan", "Bishnupur"]},
    "gujarat":            {"display": "Gujarat",              "cities": ["Ahmedabad", "Vadodara", "Surat", "Somnath", "Dwarka"]},
    "punjab":             {"display": "Punjab",               "cities": ["Amritsar", "Chandigarh", "Ludhiana", "Anandpur Sahib", "Patiala"]},
    "madhya pradesh":     {"display": "Madhya Pradesh",       "cities": ["Bhopal", "Indore", "Gwalior", "Khajuraho", "Ujjain"]},
    "mp":                 {"display": "Madhya Pradesh",       "cities": ["Bhopal", "Indore", "Gwalior", "Khajuraho", "Ujjain"]},
    "andhra pradesh":     {"display": "Andhra Pradesh",       "cities": ["Visakhapatnam", "Tirupati", "Vijayawada", "Araku Valley", "Kakinada"]},
    "ap":                 {"display": "Andhra Pradesh",       "cities": ["Visakhapatnam", "Tirupati", "Vijayawada", "Araku Valley", "Kakinada"]},
    "telangana":          {"display": "Telangana",            "cities": ["Hyderabad", "Warangal", "Nizamabad", "Nagarjuna Sagar", "Karimnagar"]},
    "jammu and kashmir":  {"display": "Jammu & Kashmir",      "cities": ["Srinagar", "Gulmarg", "Pahalgam", "Sonamarg", "Jammu"]},
    "jammu kashmir":      {"display": "Jammu & Kashmir",      "cities": ["Srinagar", "Gulmarg", "Pahalgam", "Sonamarg", "Jammu"]},
    "kashmir":            {"display": "Kashmir",              "cities": ["Srinagar", "Gulmarg", "Pahalgam", "Sonamarg", "Kupwara"]},
    "j&k":                {"display": "Jammu & Kashmir",      "cities": ["Srinagar", "Gulmarg", "Pahalgam", "Sonamarg", "Jammu"]},
    "ladakh":             {"display": "Ladakh",               "cities": ["Leh", "Nubra Valley", "Pangong Lake", "Zanskar", "Kargil"]},
    "sikkim":             {"display": "Sikkim",               "cities": ["Gangtok", "Pelling", "Namchi", "Yuksom", "Ravangla"]},
    "meghalaya":          {"display": "Meghalaya",            "cities": ["Shillong", "Cherrapunji", "Mawlynnong", "Dawki", "Tura"]},
    "assam":              {"display": "Assam",                "cities": ["Guwahati", "Kaziranga", "Majuli", "Tezpur", "Sivasagar"]},
    "odisha":             {"display": "Odisha",               "cities": ["Bhubaneswar", "Puri", "Konark", "Cuttack", "Berhampur"]},
    "orissa":             {"display": "Odisha",               "cities": ["Bhubaneswar", "Puri", "Konark", "Cuttack", "Berhampur"]},
    "bihar":              {"display": "Bihar",                "cities": ["Patna", "Gaya", "Bodh Gaya", "Nalanda", "Rajgir"]},
    "jharkhand":          {"display": "Jharkhand",            "cities": ["Ranchi", "Jamshedpur", "Dhanbad", "Deoghar", "Netarhat"]},
    "chhattisgarh":       {"display": "Chhattisgarh",         "cities": ["Raipur", "Jagdalpur", "Chitrakote", "Tirathgarh", "Bilaspur"]},
    "manipur":            {"display": "Manipur",              "cities": ["Imphal", "Ukhrul", "Moreh", "Bishnupur", "Loktak Lake"]},
    "nagaland":           {"display": "Nagaland",             "cities": ["Kohima", "Dimapur", "Mokokchung", "Longleng", "Mon"]},
    "arunachal pradesh":  {"display": "Arunachal Pradesh",    "cities": ["Itanagar", "Tawang", "Ziro", "Bomdila", "Pasighat"]},
    "mizoram":            {"display": "Mizoram",              "cities": ["Aizawl", "Lunglei", "Champhai", "Serchhip", "Kolasib"]},
    "tripura":            {"display": "Tripura",              "cities": ["Agartala", "Udaipur", "Sepahijala", "Ambassa", "Dharmanagar"]},
    "haryana":            {"display": "Haryana",              "cities": ["Gurugram", "Faridabad", "Kurukshetra", "Panipat", "Ambala"]},
    "uttarakhand":        {"display": "Uttarakhand",          "cities": ["Rishikesh", "Haridwar", "Mussoorie", "Nainital", "Auli"]},
    # ── Union Territories ─────────────────────────────────────────────────
    "delhi":              {"display": "Delhi",                "cities": ["New Delhi", "Old Delhi", "Connaught Place", "Dwarka", "Rohini"]},
    "andaman and nicobar":{"display": "Andaman & Nicobar",   "cities": ["Port Blair", "Havelock Island", "Neil Island", "Baratang", "Diglipur"]},
    "andaman":            {"display": "Andaman & Nicobar",   "cities": ["Port Blair", "Havelock Island", "Neil Island", "Baratang", "Diglipur"]},
    "nicobar":            {"display": "Andaman & Nicobar",   "cities": ["Port Blair", "Havelock Island", "Neil Island", "Baratang", "Diglipur"]},
    "lakshadweep":        {"display": "Lakshadweep",          "cities": ["Kavaratti", "Agatti", "Bangaram", "Minicoy", "Andrott"]},
    "pondicherry":        {"display": "Puducherry",           "cities": ["Puducherry", "Auroville", "Karaikal", "Mahe", "Yanam"]},
    "puducherry":         {"display": "Puducherry",           "cities": ["Puducherry", "Auroville", "Karaikal", "Mahe", "Yanam"]},
    "chandigarh":         {"display": "Chandigarh",           "cities": ["Chandigarh", "Mohali", "Panchkula", "Zirakpur", "Baddi"]},
    "daman and diu":      {"display": "Daman & Diu",          "cities": ["Daman", "Diu", "Silvassa"]},
    "dadra and nagar haveli": {"display": "Dadra & Nagar Haveli", "cities": ["Silvassa", "Daman", "Diu"]},
    "jammu":              {"display": "Jammu",                "cities": ["Jammu", "Vaishno Devi", "Patnitop", "Bhaderwah", "Kishtwar"]},
}

# Convenience set for fast O(1) state-name lookup
INDIA_STATE_KEYS = set(INDIA_STATES_MAP.keys())

# Flat set of all city names from INDIA_STATES_MAP tourism lists.
# Used as a zero-network tiebreaker when ALL geocoding providers (including the
# static fallback table) are unavailable, preventing valid Indian cities such as
# Varanasi from being misclassified as international destinations.
_INDIA_LOCAL_CITY_SET = frozenset(
    c.lower() for v in INDIA_STATES_MAP.values() for c in v.get("cities", [])
)

# ---------------------------------------------------------------------------

# This is a simplified state graph manager since we're not using the actual langgraph library
class TravelGraph:
    def __init__(self):
        self.chat_agent = ChatAgent()
        self.info_agent = InformationAgent() # InfoAgent now handles LLM re-ranking
        self.retrieval_agent = RetrievalAgent()
        self.recommend_agent = RecommendAgent() # Still used for map_data, etc.
        self.strategy_agent = StrategyAgent()
        self.route_agent = RouteAgent()
        self.budget_agent = BudgetAgent()
        self.comm_agent = CommunicationAgent()
        self.transit_agent = TransitAgent()
        
        self.state = { # Default state for a new session
            "user_info": {},
            "attractions": [], # This will hold LLM-ranked attractions from InfoAgent
            "retrieved_knowledge": None, # Store RAG context
            "weather_summary": None, # To store weather summary string
            "selected_attractions": [],
            "additional_attractions": [],
            "should_rent_car": False, # Ensure this defaults to False
            # "rental_post": None, # Intentionally removed from state
            "itinerary": [],
            "budget": {},
            "ai_recommendation_generated": False, # Flag for strategy AI advice
            "transit_options": None,
        }
        self.session_states = {} # To store states for different sessions
    
    def get_session_state(self, session_id):
        if session_id not in self.session_states:
            # Create a new state by copying the default state structure
            self.session_states[session_id] = {
                "user_info": {}, "attractions": [], "retrieved_knowledge": None, "weather_summary": None,
                "selected_attractions": [], "additional_attractions": [],
                "should_rent_car": False, # Ensure this defaults to False
                # "rental_post": None, # Intentionally removed from state
                "itinerary": [], "budget": {},
                "ai_recommendation_generated": False,
                "transit_options": None,
            }
        return self.session_states[session_id]
    
    def process_step(self, step_name, session_id=None, **kwargs):
        # print(f"[DEBUG] Processing step {step_name} for session_id: {session_id}")
        # print(f"[DEBUG] Initial kwargs: {kwargs}")
        
        if session_id:
            self.state = self.get_session_state(session_id)
            # print(f"[DEBUG] Retrieved session state: {self.state}")
        else:
            session_id = str(id(self)) # Fallback if no session_id, though it should be provided
            self.state = self.get_session_state(session_id)
            print(f"[WARN] No session_id provided, created fallback session: {session_id}")
        
        if 'ai_recommendation_generated' in kwargs: # Ensure flag is a boolean
            self.state['ai_recommendation_generated'] = str(kwargs['ai_recommendation_generated']).lower() == 'true'
        
        # print(f"[DEBUG] State before processing {step_name}: {self.state}")
        
        result = {}
        if step_name == "chat":
            result = self._process_chat(**kwargs)
        elif step_name == "information":
            result = self._process_information(**kwargs) # This will now call the updated InfoAgent
        elif step_name == "retrieval":
            result = self._process_retrieval(**kwargs)
        elif step_name == "recommend":
            result = self._process_recommend(**kwargs) # This uses the already LLM-ranked list
        elif step_name == "strategy":
            result = self._process_strategy(**kwargs)
        elif step_name == "route":
            result = self._process_route(**kwargs)
        elif step_name == "communication":
            result = self._process_communication(**kwargs)
        else:
            result = {"error": f"Unknown step: {step_name}"}
        
        self.session_states[session_id] = self.state.copy() # Save a copy of the modified state
        result["session_id"] = session_id # Ensure session_id is always in the result
        # print(f"[DEBUG] State after processing {step_name}: {self.state}")
        # print(f"[DEBUG] Result for {step_name}: {result}")
        return result
    
    def _process_chat(self, user_input=None, **kwargs):
        print("\n" + "="*50)
        print(f"[DEBUG] Entered _process_chat")
        print(f"[DEBUG LOG] _process_chat Triggered")
        print(f"User Message: '{user_input}'")
        print(f"Current State: {json.dumps(self.state.get('user_info', {}))}")
        
        try:
            current_user_info = self.state.get("user_info", {}).copy()
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        
        missing_before = [f for f in self.chat_agent.required_fields if not current_user_info.get(f)]
        print(f"Missing Fields Before: {missing_before}")
        
        print(f"[DEBUG] Calling entity extraction (chat_agent.collect_info)")
        try:
            chat_result = self.chat_agent.collect_info(user_input or "", current_user_info)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        print(f"[DEBUG] Entity extraction completed. chat_result keys: {list(chat_result.keys())}")
        
        print(f"[DEBUG] Updating state")
        try:
            if chat_result.get("state"):
                self.state["user_info"].update(chat_result["state"])
                print(f"Extracted Entities / New State: {json.dumps(chat_result.get('state', {}))}")
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        print(f"[DEBUG] State updated")
            
        missing_after = chat_result.get("missing_fields", [])
        print(f"Missing Fields After: {missing_after}")
        
        # ---- Destination detection (India → RAG, International → Web) ----
        print(f"[DEBUG] Running destination detection")
        try:
            city = self.state["user_info"].get("city", "")
            city_validated = self.state["user_info"].get("city_validated", False)

            if city and not city_validated:
                city_lower = city.lower().strip()

                if city_lower in INDIA_STATE_KEYS:
                    # Known Indian state — mark as Indian, validated
                    is_indian = True
                    self.state["user_info"]["city_validated"] = True
                    self.state["user_info"]["is_international"] = False
                else:
                    print(f"[DEBUG] Calling validate_indian_location for city='{city}'")
                    validation = self.info_agent.validate_indian_location(city)
                    print(f"[DEBUG] validate_indian_location returned: {validation!r}")

                    if validation is True:
                        # Geocoding confirmed the destination is in India
                        self.state["user_info"]["city_validated"] = True
                        self.state["user_info"]["is_international"] = False
                        print(f"[INFO] Indian destination confirmed in chat: '{city}'")
                    elif validation is False:
                        # Geocoding confirmed the destination is outside India
                        self.state["user_info"]["city_validated"] = True
                        self.state["user_info"]["is_international"] = True
                        print(f"[INFO] International destination confirmed in chat: '{city}' → web retrieval")
                    else:
                        # None — all geocoding providers unavailable (API failure, not found, etc.)
                        # Use local city set as tiebreaker — do NOT default to international.
                        city_l = city.lower().strip()
                        is_local_indian = (city_l in _INDIA_LOCAL_CITY_SET
                                           or city_l in INDIA_STATE_KEYS)
                        self.state["user_info"]["city_validated"] = True
                        self.state["user_info"]["is_international"] = not is_local_indian
                        self.state["user_info"]["validation_uncertain"] = True
                        print(
                            f"[WARN] All geocoders unavailable for '{city}'. "
                            f"Local fallback classifies as "
                            f"{'Indian' if is_local_indian else 'international (unrecognised city)'}"
                        )
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        print(f"[DEBUG] Destination detection completed")
        # ---- end destination detection ----

        print(f"[DEBUG] Building response_data")
        try:
            response_data = {
                "state": self.state.copy(),
                "stream": chat_result.get("stream"),
                "missing_fields": chat_result.get("missing_fields", [])
            }
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        print(f"[DEBUG] response_data built. complete={chat_result.get('complete', False)}, stream_is_none={chat_result.get('stream') is None}")
        
        if not chat_result.get("complete", False): 
            response_data["next_step"] = "chat"
            print(f"Next State: chat")
            print(f"Reason for Transition: Still missing fields: {response_data['missing_fields']}")
            print(f"[DEBUG] Returning response from _process_chat (chat not complete)")
            return response_data
            
        print(f"Next State: information")
        print(f"Reason for Transition: All required information collected successfully.")
        
        # Mark chat as complete and transition to information step
        response_data["next_step"] = "information"
        
        def transition_message():
            yield AIMessage(content="I have all the information I need! Let me search for the best attractions for your trip...")
            
        response_data["stream"] = transition_message()
        return response_data
    
    def _process_information(self, **kwargs):
        user_prefs = self.state["user_info"]
        city = user_prefs.get("city")

        if not city:
            def error_gen(): yield AIMessage(content="Please tell me which city or destination you'd like to visit.")
            return {"next_step": "chat", "stream": error_gen(), "missing_fields": ["city"], "state": self.state.copy()}

        city_lower = city.lower().strip()

        # ── Destination classification (India vs International) ─────────────
        # Uses already-computed city_validated and is_international flags from
        # _process_chat(). Falls back to API check only if not yet classified.
        city_validated = user_prefs.get("city_validated", False)
        is_international = user_prefs.get("is_international", False)

        if not city_validated:
            if city_lower in INDIA_STATE_KEYS:
                is_international = False
            else:
                validation = self.info_agent.validate_indian_location(city)
                if validation is True:
                    is_international = False
                elif validation is False:
                    is_international = True
                else:
                    # None — all geocoders unavailable; use local city set as tiebreaker
                    is_local_indian = (city_lower in _INDIA_LOCAL_CITY_SET
                                       or city_lower in INDIA_STATE_KEYS)
                    is_international = not is_local_indian
                    print(
                        f"[WARN] Geocoders unavailable for '{city}' in _process_information. "
                        f"Local fallback: {'Indian' if not is_international else 'international (unrecognised)'}"
                    )
            self.state["user_info"]["city_validated"] = True
            self.state["user_info"]["is_international"] = is_international
            if is_international:
                print(f"[INFO] International destination in _process_information: '{city}'")
        # ── end destination classification ──────────────────────────────────

        # ================================================================
        # Detect whether input is an Indian STATE, Indian CITY, or
        # an International destination.
        # ================================================================
        is_state_search = (not is_international) and (city_lower in INDIA_STATE_KEYS)
        state_info = INDIA_STATES_MAP.get(city_lower) if is_state_search else None

        if is_state_search:
            display_name = state_info["display"]
            state_cities  = state_info["cities"]
            print(f"[STATE_SEARCH] '{city}' detected as Indian state '{display_name}'. Cities: {state_cities}")
            # Use the first (capital/main) city's coordinates for weather + map centre
            geocode_city = f"{state_cities[0]}, {display_name}, India"
            expected_country = "in"
        else:
            display_name = city
            expected_country = "in" if not is_international else None
            geocode_city = f"{city}, India" if not is_international else city

        print(f"[DEBUG _process_information] Calling city2geocode with geocode_city={repr(geocode_city)} (type: {type(geocode_city)})")
        city_coordinates = self.info_agent.city2geocode(geocode_city, expected_country=expected_country)
        print(f"[DEBUG _process_information] city2geocode returned {repr(city_coordinates)}")
        if not city_coordinates:
            if not is_international:
                # Indian destination confirmed but all geocoders returned None.
                # Use India geographic centroid so the pipeline continues rather
                # than sending the user back to an infinite chat loop.
                print(
                    f"[WARN] All geocoders failed for Indian destination '{display_name}'. "
                    f"Using India centroid (20.59°N, 78.96°E) to keep pipeline running."
                )
                city_coordinates = {"lat": 20.5937, "lng": 78.9629}
                self.state["user_info"]["coords_from_centroid"] = True
            else:
                # International destination with no coordinates — cannot proceed meaningfully.
                print(f"[WARN] All geocoders failed for international destination '{display_name}'.")
                def error_gen():
                    yield AIMessage(content=(
                        f"I couldn't retrieve location data for **{display_name}**. "
                        f"Please verify the destination name and try again, "
                        f"or try a nearby major city."
                    ))
                return {"next_step": "chat", "stream": error_gen(), "state": self.state.copy()}

        # Store coordinates in user_prefs so RetrievalAgent can use them for
        # lat/lng bounding box India detection without an extra API call.
        self.state["user_info"]["_dest_lat"] = city_coordinates["lat"]
        self.state["user_info"]["_dest_lng"] = city_coordinates["lng"]

        # ---- Weather (uses geocode_city for both city and state search) ----
        weather_summary_str = None
        user_start_date = user_prefs.get("start_date", "not decided")
        user_days_str   = user_prefs.get("days")

        is_valid_date = False
        if user_start_date not in ["not decided", "flexible", ""]:
            try:
                datetime.strptime(user_start_date, "%Y-%m-%d")
                is_valid_date = True
            except ValueError:
                print(f"[WARN] Invalid start_date '{user_start_date}', defaulting to 7 days from now.")
                is_valid_date = False

        if not is_valid_date:
            user_start_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            user_prefs["start_date"] = user_start_date

        if user_days_str:
            try:
                num_days = int(user_days_str)
                weather_data_result = self.info_agent.get_weather(
                    city_coordinates["lat"], city_coordinates["lng"],
                    user_start_date, num_days, summary=True
                )
                if weather_data_result and 'summary' in weather_data_result:
                    summary_val = weather_data_result['summary']
                    if hasattr(summary_val, 'content'):
                        weather_summary_str = summary_val.content
                    elif isinstance(summary_val, str):
                        weather_summary_str = summary_val
                    self.state["weather_summary"] = weather_summary_str
                    print(f"[DEBUG] Weather summary set: '{weather_summary_str}'")
            except ValueError:
                print(f"[ERROR] Invalid 'days' for weather: {user_days_str}")
            except Exception as e:
                print(f"[ERROR] Exception fetching weather summary: {e}")
                traceback.print_exc()
        else:
            print("[DEBUG] Weather info not fetched (no days).")

        # ================================================================
        # Fetch attractions — STATE path vs CITY path
        # ================================================================
        if self.state.get("information_processed") and self.state.get("attractions"):
            print("[STATE_MACHINE][WARNING] Duplicate stage execution detected: _process_information. Reusing cached attractions.")
            attractions_from_info_agent = self.state["attractions"]
        else:
            if is_state_search:
                print(f"[STATE_SEARCH] Running state-wide search for {display_name}.")
                attractions_from_info_agent = self.info_agent.get_attractions_for_state(
                    state_name=display_name,
                    state_cities=state_cities,
                    user_prefs=user_prefs,
                    weather_summary=self.state.get("weather_summary"),
                    total_number=20
                )
            else:
                print(f"[DEBUG] Calling info_agent.get_attractions for city '{city}'.")
                attractions_from_info_agent = self.info_agent.get_attractions(
                    lat=city_coordinates["lat"],
                    lng=city_coordinates["lng"],
                    user_prefs=user_prefs,
                    weather_summary=self.state.get("weather_summary"),
                    number=20,
                    poi_type="tourist_attraction"
                )
            
            self.state["attractions"] = attractions_from_info_agent if attractions_from_info_agent else []
            self.state["information_processed"] = True
            print(f"[ATTRACTION_PIPELINE] completed total_pois={len(self.state['attractions'])}")

        print(f"[DEBUG] Attractions updated: {len(self.state['attractions'])} items.")
        # ---- Accommodations ----
        budget = user_prefs.get("budget", "medium")
        try:
            accommodations = self.info_agent.get_accommodations(
                lat=city_coordinates["lat"],
                lng=city_coordinates["lng"],
                budget=budget,
                number=4
            )
            self.state["accommodations"] = accommodations
            print(f"[DEBUG] Accommodations updated: {len(self.state.get('accommodations', []))} items.")
        except Exception as e:
            print(f"[ERROR] Failed to fetch accommodations: {e}")
            self.state["accommodations"] = []

        # ---- User-facing response message ----
        def info_gen_message():
            if self.state["attractions"]:
                if is_state_search:
                    cities_shown = ", ".join(state_cities[:3]) + (f" and {len(state_cities)-3} more" if len(state_cities) > 3 else "")
                    yield AIMessage(content=(
                        f"🗺️ Great choice! I've searched across the entire state of **{display_name}** "
                        f"covering major destinations like **{cities_shown}**.\n\n"
                        f"Here are the **{len(self.state['attractions'])} best attractions** curated from across "
                        f"{display_name}, personalised to your preferences. Please browse and select your favourites!"
                    ))
                elif is_international:
                    yield AIMessage(content=(
                        f"🌍 Great choice! I've found **{len(self.state['attractions'])} attractions** "
                        f"in **{display_name}** for you, tailored to your preferences. "
                        f"Please browse and select your favorites!"
                    ))
                else:
                    yield AIMessage(content=(
                        f"I've prepared a personalized list of {len(self.state['attractions'])} attractions "
                        f"in **{display_name}** for you, considering your preferences and the weather. "
                        f"Please take a look and select your favorites!"
                    ))
            else:
                if is_state_search:
                    yield AIMessage(content=f"I couldn't find attractions across {display_name} right now. Try specifying a particular city within the state.")
                else:
                    yield AIMessage(content=f"I couldn't find attractions in {display_name} matching your preferences right now. Please try a different destination or broaden your interests.")

        return {
            "next_step": "retrieval",
            "stream": info_gen_message(),
            "attractions": self.state["attractions"],
            "accommodations": self.state.get("accommodations", []),
            "map_data": self.recommend_agent.generate_map_data(self.state["attractions"]),
            "state": self.state.copy()
        }

    def _process_retrieval(self, **kwargs):
        """Process retrieval agent step to fetch background knowledge from Vector DB"""
        user_prefs = self.state.get("user_info", {})
        city = user_prefs.get("city", "")
        
        # Retrieve contextual knowledge
        retrieved_knowledge = self.retrieval_agent.retrieve_context(user_prefs, city)
        self.state["retrieved_knowledge"] = retrieved_knowledge
        
        # Attempt to extract POIs from RAG knowledge
        rag_pois = []
        if retrieved_knowledge and "No additional background knowledge" not in retrieved_knowledge:
            lat = user_prefs.get("_dest_lat", 0.0)
            lng = user_prefs.get("_dest_lng", 0.0)
            rag_pois = self.recommend_agent.extract_pois_from_text(retrieved_knowledge, city, lat, lng)
            
        if rag_pois:
            existing_pois = self.state.get("attractions", [])
            existing_names = {p.get("name", "").lower().strip() for p in existing_pois if p.get("name")}
            
            new_rag_pois = []
            for rp in rag_pois:
                name_lower = rp.get("name", "").lower().strip()
                if name_lower and name_lower not in existing_names:
                    # Ensure valid image_url for RAG POIs
                    img_url = rp.get("image_url")
                    if not img_url or not str(img_url).startswith("http"):
                        rp["image_url"] = self.info_agent.poi_manager._fetch_pexels_image(rp.get("name", ""))
                    
                    new_rag_pois.append(rp)
                    existing_names.add(name_lower)
            
            if new_rag_pois:
                print(f"[DEBUG] Adding {len(new_rag_pois)} RAG-extracted POIs to state['attractions']")
                self.state["attractions"] = existing_pois + new_rag_pois
                
        return {
            "next_step": "recommend",
            "retrieved_knowledge": retrieved_knowledge,
            "state": self.state.copy()
        }
        
    def _process_recommend(self, selected_attraction_ids=None, **kwargs):
        """Process recommend agent step"""
        try:
            user_prefs = self.state["user_info"]
            attractions = self.state["attractions"]
            
            
            # Check if we have selected_attraction_ids provided
            if selected_attraction_ids:
                
                # Fetch accommodation if selected
                if 'selected_accommodation_id' in kwargs and kwargs['selected_accommodation_id']:
                    accs = self.state.get("accommodations", [])
                    acc = next((a for a in accs if a["id"] == kwargs['selected_accommodation_id']), None)
                    if acc:
                        self.state["selected_accommodation"] = acc

                # User has selected specific attractions
                selected_attractions = [
                    a for a in attractions 
                    if a and a.get("id") and a["id"] in selected_attraction_ids
                ]
                self.state["selected_attractions"] = selected_attractions
                
                # Validation check
                days = int(user_prefs.get('days', 3))
                required = days * 2
                
                # "force_continue" flag is sent by frontend if user wants to bypass warning
                force_continue = kwargs.get('force_continue', 'false').lower() == 'true'
                
                if len(selected_attractions) < required and not force_continue:
                    # Return validation warning to frontend
                    def validation_msg():
                        yield AIMessage(content=f"You've selected {len(selected_attractions)} attractions for a {days}-day trip. We recommend selecting at least {required} attractions.")
                    
                    return {
                        "next_step": "recommend",
                        "stream": validation_msg(),
                        "validation_warning": True,
                        "required_count": required,
                        "selected_count": len(selected_attractions)
                    }
                
                # Create a generator that yields a transition message
                def transition_generator():
                    yield AIMessage(content="Processing your selected attractions...")
                
                return {
                    "next_step": "strategy",
                    "stream": transition_generator(),
                    "selected_attractions": selected_attractions
                }
            else:
                # Recommend attractions to the user
                if not attractions:
                    def error_gen():
                        yield AIMessage(content="I couldn't find attractions for your destination right now. Please try specifying a different location or broaden your interests.")
                    return {
                        "next_step": "chat",
                        "stream": error_gen(),
                        "response": "No attractions available for recommendation.",
                        "recommended_attractions": [],
                        "accommodations": [],
                        "map_data": []
                    }
                
                # We already ranked and categorized them in InformationAgent, so we don't call recommend_core_attractions.
                # Just use them directly.
                recommended = attractions
                
                # Add specific logging for recommendations
                interest_count = sum(1 for a in recommended if a.get('recommendation_type') == 'interest_based')
                popular_count = sum(1 for a in recommended if a.get('recommendation_type') == 'popular')
                print(f"[RECOMMENDATION DEBUG] total recommendations = {len(recommended)}")
                print(f"[RECOMMENDATION DEBUG] interest based count = {interest_count}")
                print(f"[RECOMMENDATION DEBUG] popular count = {popular_count}")
                
                # Create a generator that yields the recommendation message
                def recommendation_generator():
                    yield AIMessage(content="Here are some recommended attractions and accommodations for you.")
                
                return {
                    "next_step": "recommend",  # Stay on this step until user selects attractions
                    "stream": recommendation_generator(),
                    "recommended_attractions": recommended,
                    "accommodations": self.state.get("accommodations", []),
                    "map_data": self.recommend_agent.generate_map_data(recommended)
                }
        except Exception as e:
            print(f"Error in _process_recommend: {str(e)}")
            return {
                "next_step": "error",
                "stream": None,
                "response": "An error occurred while processing recommendations.",
                "error": str(e)
            }
    
    def _process_strategy(self, **kwargs):
        """Process strategy agent step"""
        # Check if this is a confirm selection request or a satisfaction confirmation
        user_input_lower = kwargs.get('user_input', '').lower()
        is_confirm_selection = user_input_lower in ['here are my selected attractions', 'please plan the route for me', 'continue']
        is_satisfaction_confirmation = 'satisfied with your recommendation' in user_input_lower
        
        # Log what type of confirmation message we received
        if is_confirm_selection:
            print("[DEBUG] Received initial confirmation of selections")
        elif is_satisfaction_confirmation:
            print("[DEBUG] Received satisfaction confirmation message")
        else:
            print(f"[DEBUG] Received other input: '{user_input_lower}'")
        
        # Print the current state of should_rent_car for debugging
        if 'should_rent_car' in self.state:
            print(f"[DEBUG] Current should_rent_car value: {self.state['should_rent_car']}")
        else:
            print("[DEBUG] should_rent_car not yet set in state")
            
        # If recommendations haven't been generated yet and this is the initial confirm selection
        if not self.state['ai_recommendation_generated'] and is_confirm_selection:
            # Update state flags BEFORE generating recommendations
            self.state['ai_recommendation_generated'] = True
            self.state['user_input_processed'] = True
            
            selected_attractions = self.state["selected_attractions"]
            total_days = self.state["user_info"].get("days", 1)

            # Plan remaining time and suggest additional attractions
            strategy_result = self.strategy_agent.plan_remaining_time(
                selected_spots=selected_attractions, 
                total_days=total_days,
                all_attractions=self.state["attractions"],  ## This should be the full list of attractions
                user_prefs=self.state["user_info"],    # Pass user_prefs
                weather_summary=self.state.get("weather_summary"), # Pass weather_summary
                retrieved_knowledge=self.state.get("retrieved_knowledge") # Pass RAG context
            )
            
            self.state["attractions"] = strategy_result["additional_attractions"]  ## 现在这里的attractions 是经过筛选的,也是最终的attractions
            self.state["daily_plan"] = strategy_result.get("daily_plan", {}) # Store the daily plan

            # Initialize should_rent_car to False by default
            self.state["should_rent_car"] = False
            print("[DEBUG] Initialized should_rent_car to False")
            
            # Get AI recommendation about the overall plan
            # This will also analyze the recommendation and update should_rent_car in user_prefs
            ai_recommendation = self.strategy_agent.get_ai_recommendation(
                user_prefs=self.state["user_info"],
                selected_spots=selected_attractions,
                total_days=total_days,
            )
            
            # Get the should_rent_car value from user_prefs after AI recommendation analysis
            # This value is set by extract_rental_recommendation in strategy_agent.py
            ai_should_rent_car = self.state["user_info"].get("should_rent_car", False)
            self.state["should_rent_car"] = ai_should_rent_car
            
            print(f"[CRITICAL] AI rental recommendation set should_rent_car to: {ai_should_rent_car}")
            print(f"[DEBUG] Updated state should_rent_car value: {self.state['should_rent_car']}")
            
            # Create a copy of the state to return
            self.state["ai_recommendation_generated"] = True
            state_copy = self.state.copy()
            
            return {
                "next_step": "strategy",
                "stream": ai_recommendation,
                "remaining_hours": strategy_result["remaining_hours"],
                "additional_attractions": strategy_result["additional_attractions"],
                "should_rent_car": self.state["should_rent_car"],
                "state": state_copy,
                "ai_recommendation_generated": True,
                "user_input_processed": True
            }
        # Handle both cases: either we have already generated recommendations 
        # or this is a satisfaction confirmation message
        elif self.state['ai_recommendation_generated'] or is_satisfaction_confirmation:
            print("[DEBUG] Recommendations already generated or satisfaction confirmed, moving to next step")
            
            # Check if this is a satisfaction confirmation message and we need to process it specially
            if is_satisfaction_confirmation and not self.state['ai_recommendation_generated']:
                print("[CRITICAL] Handling satisfaction confirmation without prior recommendation generation")
                # This means user sent satisfaction message before going through normal flow
                # We need to ensure should_rent_car is correctly set to false in this case
                self.state["should_rent_car"] = False # Ensure it's false
                print("[DEBUG] Set should_rent_car to False for satisfaction message without prior recommendation")
            
            # ALWAYS GO TO COMMUNICATION STEP
            next_step = "communication"
            print(f"[CRITICAL] Decision point: Setting next_step to '{next_step}' to generate summaries and travel tips.")
            
            # Create a generator that yields the transition message
            def transition_generator():
                if next_step == "route":
                    yield AIMessage(content="Moving to route planning...")
                else:
                    yield AIMessage(content="Moving to car rental options...")
            
            # Create a copy of the state to return
            state_copy = self.state.copy()
            
            return {
                "next_step": next_step,
                "stream": transition_generator(),
                "state": state_copy,
                "ai_recommendation_generated": True,
                "user_input_processed": True
            }
        else:
            print("[DEBUG] Not a confirm selection request and recommendations not generated yet")
            # Create a generator that yields a message asking for confirmation
            def confirmation_generator():
                yield AIMessage(content="Please click the 'Confirm Selection' button to proceed with your travel plan.")
            
            # Create a copy of the state to return
            state_copy = self.state.copy()
            print(f"[DEBUG] State to be returned: {state_copy}")
            
            return {
                "next_step": "strategy",
                "stream": confirmation_generator(),
                "state": state_copy,
                "ai_recommendation_generated": False,
                "user_input_processed": False
            }
    
    # After strategy step + self.state.get("should_rent_car", False) == True
    def _process_communication(self, response_message=None, **kwargs):
        """Process communication agent step to generate summaries and travel tips"""
        user_input_lower = kwargs.get('user_input', '').lower()
        
        # If the user input is NOT 'continue', it means they have seen the tips and confirmed to proceed.
        if user_input_lower != "continue":
            print("[DEBUG] Received user confirmation to proceed to route planning.")
            def confirmation_generator():
                yield AIMessage(content="Great! I'm now generating your final itinerary and optimal route...")
            return {
                "next_step": "route",
                "stream": confirmation_generator(),
                "state": self.state.copy()
            }
            
        print("[DEBUG] Generating Budget, Transit, and Travel Tips for Communication Step...")
        
        start_date = self.state["user_info"].get("start_date") or datetime.now().strftime("%Y-%m-%d")
        days = int(self.state["user_info"].get("days", 1))
        
        if isinstance(start_date, str):
            try:
                start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
            except ValueError:
                start_date_dt = datetime.now()
        else:
            start_date_dt = start_date
        end_date = (start_date_dt + timedelta(days=days)).strftime("%Y-%m-%d")
        
        all_attractions_objects = self.state.get("attractions", [])
        selected_attractions = self.state.get("selected_attractions", [])
        
        # Get transit options
        origin = self.state["user_info"].get("origin_city")
        destination = self.state["user_info"].get("city")
        budget_level = self.state["user_info"].get("budget", "medium")
        
        transit_options = None
        if origin and destination:
            try:
                print(f"[DEBUG] Fetching transit options from {origin} to {destination}...")
                transit_options = self.transit_agent.get_transit_options(
                    origin=origin,
                    destination=destination,
                    start_date=start_date,
                    budget_level=budget_level
                )
            except Exception as te:
                print(f"[WARN] Failed to get transit options: {te}")
                
        # Estimate budget
        car_info = None
        fuel_price = None

        if self.state.get("should_rent_car", False):
            try:
                car_info = self.info_agent.search_car_rentals(
                    self.state["user_info"].get("city", ""),
                    start_date,
                    end_date,
                    driver_age=self.state["user_info"].get("age", 30)
                )
                fuel_price = self.info_agent.get_fuel_price(self.state["user_info"].get("city", ""))
            except Exception as car_e:
                print(f"[WARN] Could not fetch car/fuel info: {car_e}")

        budget = self.budget_agent.estimate_budget(
            all_attractions_objects,
            self.state["user_info"],
            self.state.get("should_rent_car", False),
            car_info,
            fuel_price,
            transit_options
        )
        
        # Integrate selected accommodation into budget early
        selected_acc = self.state.get("selected_accommodation")
        if selected_acc and "price" in selected_acc:
            price_val = extract_number(str(selected_acc["price"]))
            if price_val:
                nights = max(1, days - 1)
                budget["accommodation"] = price_val * nights
                budget["total"] = (
                    budget.get("attractions", 0) + 
                    budget.get("food", 0) + 
                    budget.get("transport", 0) + 
                    budget["accommodation"] +
                    budget.get("car_rental", 0) +
                    budget.get("fuel_cost", 0) +
                    budget.get("intercity_transport", 0)
                )
                budget["miscellaneous"] = round(budget["total"] * 0.10, 2)
                budget["total"] += budget["miscellaneous"]

        # Store in state so route doesn't have to re-fetch
        self.state["budget"] = budget
        self.state["transit_options"] = transit_options

        # Generate the communication text stream
        def tips_generator():
            tips_content = self.comm_agent.generate_travel_tips_and_summary(
                self.state["user_info"],
                selected_attractions,
                budget,
                transit_options
            )
            yield AIMessage(content=tips_content)
            
        return {
            "next_step": "communication",  # Pause here and wait for explicit confirmation!
            "stream": tips_generator(),
            "state": self.state.copy()
        }
    
    def _process_route(self, start_date=None, **kwargs):
        """Process route agent step"""
        try:
            # Get start date from user preferences, fallback to provided start_date, then to current date
            start_date = self.state["user_info"].get("start_date") or start_date or datetime.now().strftime("%Y-%m-%d")
            
            
            all_attractions_objects = self.state["attractions"] ## This is the flat list of all planned attraction objects
            daily_plan_name_dict = self.state.get("daily_plan") # This is {"day1": ["NameA"], ...}
            
            #print(f"[DEBUG] All attractions: {all_attractions}")
            
            if not all_attractions_objects:
                return {
                    "next_step": "complete",
                    "response": "No attractions selected for the trip.",
                    "itinerary": [],
                    "budget": {},
                    "optimal_route": []
                }
            
            # Generate itinerary first
            days = int(self.state["user_info"].get("days", 1))  # Ensure days is an integer

            itinerary = []
            if daily_plan_name_dict and isinstance(daily_plan_name_dict, dict) and all_attractions_objects:
                all_spots_map = {spot["name"]: spot for spot in all_attractions_objects if spot and "name" in spot}
                itinerary = self.route_agent.format_daily_plan_to_itinerary(
                    daily_plan_name_dict,
                    all_spots_map,
                    start_date 
                )
            else:
                print("[ERROR] Could not generate itinerary: daily_plan_name_dict or all_attractions_objects missing/invalid.")
                # Fallback: Potentially use the old generate_itinerary if it was kept and makes sense
                # For now, itinerary remains empty, leading to a response with no itinerary.
                # self.state["itinerary"] will be empty, and confirmation will reflect that.

            
            # Fix: convert start_date to datetime if it's a string
            if isinstance(start_date, str):
                try:
                    start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
                except ValueError:
                    print(f"[WARN] Invalid start_date format in process_route: '{start_date}'. Using current date.")
                    start_date_dt = datetime.now()
            else:
                start_date_dt = start_date
            end_date = (start_date_dt + timedelta(days=days)).strftime("%Y-%m-%d")
            # Extract the optimal route from the itinerary
            optimal_route = []
            if itinerary:
                for day_plan_item in itinerary: # Iterate through list of day plans
                    day_number = day_plan_item.get("day")
                    for spot in day_plan_item.get("spots", []):
                        spot_with_day = spot.copy() # Avoid modifying original spot in itinerary
                        spot_with_day["day"] = day_number
                        optimal_route.append(spot_with_day)
            # Use transit options and budget already generated in communication step
            transit_options = self.state.get("transit_options")
            budget = self.state.get("budget", {})

            # Integrate selected accommodation into itinerary
            selected_acc = self.state.get("selected_accommodation")
            if selected_acc and itinerary:
                # Add to first day's itinerary
                acc_spot = {
                    "id": f"acc_{selected_acc.get('id', 'hotel')}",
                    "name": selected_acc.get("name", "Hotel"),
                    "category": "Accommodation",
                    "description": f"Check-in to your accommodation: {selected_acc.get('name', '')}",
                    "location": {"lat": selected_acc.get("latitude"), "lng": selected_acc.get("longitude")},
                    "start_time": "14:00", # Standard check-in time
                    "end_time": "15:00",
                    "is_accommodation": True
                }
                # Insert at the beginning of day 1, or after the first activity if it starts before 14:00
                if "spots" in itinerary[0]:
                    itinerary[0]["spots"].insert(0, acc_spot)
                    
            # Store in state
            self.state["itinerary"] = itinerary
            self.state["budget"] = budget
            self.state["transit_options"] = transit_options
            
            # Generate confirmation message (with fallback if it fails)
            try:
                confirmation = self.comm_agent.generate_booking_confirmation(
                    itinerary,
                    budget,
                    self.state["should_rent_car"],
                    self.state["user_info"].get("name", "Traveler"),
                )
            except Exception as conf_e:
                import traceback
                print(f"[WARN] generate_booking_confirmation failed: {conf_e}")
                traceback.print_exc()
                name = self.state["user_info"].get("name", "Traveler")
                city = self.state["user_info"].get("city", "your destination")
                days = self.state["user_info"].get("days", "?")
                confirmation = f"Your {days}-day trip to {city} has been planned, {name}! Check your itinerary below."
            
            return {
                "next_step": "complete",
                "response": confirmation,
                "itinerary": itinerary,
                "budget": budget,
                "optimal_route": optimal_route,
                "transit_options": transit_options,
                "budget_warning": budget.get("budget_warning"),
                "budget_infeasible": budget.get("budget_infeasible", False),
            }
            
        except Exception as e:
            # Log the FULL error for debugging
            import traceback
            print(f"[ERROR] Error in process route: {str(e)}")
            traceback.print_exc()
            return {
                "next_step": "error",
                "response": "An error occurred while planning your route. Please try again.",
                "error": str(e)
            }
    
    def get_current_state(self):
        """Get the current state of the workflow"""
        return self.state