import sys, io
import logging
# Keep the root logger at INFO so the ExploreX agent trace stays readable,
# and silence third-party DEBUG chatter (HTTP clients, ChromaDB internals,
# sentence-transformers/HF hub metadata probes). The HF 404s for optional
# files such as 2_Normalize/config.json and additional_chat_templates are
# harmless metadata probes — the model still loads and embeds correctly.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
for _noisy in (
    "httpcore", "httpcore2", "httpx", "httpx2", "urllib3",
    "chromadb", "posthog", "sentence_transformers", "huggingface_hub",
    "filelock", "sentence_transformers.util.file_io",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
# The Google GenAI function-utils logger emits a per-field "Key 'title' is not
# supported in schema, ignoring" WARNING when converting Pydantic JSON schemas.
# This is cosmetic (only the human-readable title annotation is dropped; field
# names, types, descriptions, enums and validation are preserved), so quiet it
# without touching behaviour.
logging.getLogger("langchain_google_genai._function_utils").setLevel(logging.ERROR)
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
import re
from utils import extract_number
from services.email_service import send_trip_email
from services.budget_norm import normalize_budget
from services.transit_norm import normalize_transit_options

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


def normalize_poi(poi):
    """Guarantee id is a string and location is {lat, lng} when coordinates exist."""
    if not isinstance(poi, dict):
        return poi
    if poi.get('id') is not None:
        poi['id'] = str(poi['id'])
    loc = poi.get('location') if isinstance(poi.get('location'), dict) else {}
    lat = loc.get('lat', loc.get('latitude', poi.get('latitude', poi.get('lat'))))
    lng = loc.get('lng', loc.get('longitude', poi.get('longitude', poi.get('lng', poi.get('lon')))))
    try:
        if lat is not None and lng is not None:
            lat_f, lng_f = float(lat), float(lng)
            poi['location'] = {'lat': lat_f, 'lng': lng_f}
            poi['latitude'] = lat_f
            poi['longitude'] = lng_f
    except (TypeError, ValueError):
        pass
    return poi


# ---------------------------------------------------------------------------

# This is a simplified state graph manager since we're not using the actual langgraph library
class TravelGraph:
    def __init__(self, user_name=None, user_email=None):
        self.chat_agent = ChatAgent()
        self.info_agent = InformationAgent() # InfoAgent now handles LLM re-ranking
        self.retrieval_agent = RetrievalAgent()
        self.recommend_agent = RecommendAgent() # Still used for map_data, etc.
        self.strategy_agent = StrategyAgent()
        self.route_agent = RouteAgent()
        self.budget_agent = BudgetAgent()
        self.comm_agent = CommunicationAgent()
        self.transit_agent = TransitAgent()
        
        self.default_user_info = {}
        if user_name:
            self.default_user_info["name"] = user_name
        if user_email:
            self.default_user_info["email"] = user_email
            
        self.state = self._fresh_state()  # Default state for a new session
        self.session_states = {} # To store states for different sessions

    def _fresh_state(self):
        """Build a complete, clean trip state.

        One factory is used for the default state AND for every new session, so
        a fresh trip can never inherit a stale key from an earlier one, and no
        code path has to remember to initialise a field (Phase 9).
        """
        return {
            "user_info": self.default_user_info.copy(),
            "attractions": [],               # LLM-ranked POIs from InfoAgent
            "retrieved_knowledge": None,     # RAG context
            "weather_summary": None,
            "selected_attractions": [],       # authoritative traveller choice
            "additional_attractions": [],
            "planned_attractions": [],        # reconciled subset driving the plan
            "unscheduled_attractions": [],    # selected-but-not-scheduled, w/ reason
            "should_rent_car": False,
            "rental_decision_source": None,   # user_selection|agent_recommendation|default
            "car_rental_available": None,
            "car_rental_source": None,
            "itinerary": [],
            "budget": {},
            "budget_normalized": {},
            "ai_recommendation_generated": False,
            "transit_options": None,
            "accommodations": [],
            "restaurants": [],
            "selected_accommodation": None,
            "travel_tips": None,
            "provider_status": None,           # per-provider health for observability
            "email_sent": False,
            "current_step": "chat",
        }

    def get_session_state(self, session_id):
        """Return the isolated per-session trip state, creating it when new."""
        if session_id not in self.session_states:
            self.session_states[session_id] = self._fresh_state()
        return self.session_states[session_id]

    def new_trip_state(self, session_id=None):
        """Reset ONLY trip data; keep identity/session wiring (Phase 9)."""
        state = self._fresh_state()
        if session_id:
            self.session_states[session_id] = state
            self.state = state
        else:
            self.state = state
        return state

    def set_state_field(self, key, value):
        """Explicit single-field update on the live session state.

        ``main.py`` has always called this (e.g. to record ``email_sent``) but
        the method never existed, so those calls silently fell through to a
        generic mutation path.
        """
        self.state[key] = value
        return value

    def get_state_snapshot(self):
        """Shallow copy safe to hand to the API layer."""
        return dict(self.state)

    def process_step(self, step_name, session_id=None, **kwargs):
        # print(f"[DEBUG] Processing step {step_name} for session_id: {session_id}")
        # print(f"[DEBUG] Initial kwargs: {kwargs}")
        
        if session_id:
            self.state = self.get_session_state(session_id)
            # print(f"[DEBUG] Retrieved session state: {self.state}")
        else:
            import os
            session_id = os.urandom(16).hex()
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

        # Workflow progress is part of the persistent assistant context, so it
        # must be recorded on the server, not inferred by the browser.
        if not result.get("error"):
            self.state["current_step"] = result.get("next_step") or step_name
        if self.info_agent is not None and getattr(self.info_agent, "poi_manager", None) is not None:
            status = getattr(self.info_agent.poi_manager, "get_provider_status", None)
            if callable(status):
                try:
                    self.state["provider_status"] = status()
                except Exception:
                    pass

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
        
        missing_before = [f for f in self.chat_agent.required_fields if not self.chat_agent.is_valid_field(f, current_user_info.get(f))]
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
                new_state = chat_result["state"]
                old_city = current_user_info.get("city")
                new_city = new_state.get("city")
                
                # Invalidate destination-dependent data if the user changed the city mid-chat
                if old_city and new_city and old_city.strip().lower() != new_city.strip().lower():
                    print(f"[INFO] Destination changed from {old_city} to {new_city}. Invalidating dependent data.")
                    self.state["attractions"] = []
                    self.state["selected_attractions"] = []
                    self.state["additional_attractions"] = []
                    self.state["itinerary"] = []
                    self.state["retrieved_knowledge"] = None
                    self.state["weather_summary"] = None
                    self.state["transit_options"] = None
                    self.state["ai_recommendation_generated"] = False
                    self.state["information_processed"] = False
                    new_state["city_validated"] = False
                    
                self.state["user_info"].update(new_state)
                print(f"Extracted Entities / New State: {json.dumps(new_state)}")
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
            
        print(f"Reason for Transition: All required information collected successfully.")
        
        # Check if we are already past the chat stage
        if self.state.get("information_processed") and self.state.get("attractions"):
            if self.state.get("ai_recommendation_generated") and self.state.get("selected_attractions"):
                response_data["next_step"] = "strategy"
                print(f"Next State: strategy (shortcut)")
                return response_data
            
            response_data["next_step"] = "recommend"
            response_data["ui_action"] = "OPEN_EXPLORE"
            print(f"Next State: recommend (shortcut)")
            
            def shortcut_message():
                yield AIMessage(content="Taking you back to your recommendations!")
            response_data["stream"] = shortcut_message()
            return response_data
        
        # Mark chat as complete and transition to information step
        response_data["next_step"] = "information"
        print(f"Next State: information")
        
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

        if self.state.get("information_processed") and self.state.get("attractions"):
            print("[STATE_MACHINE][WARNING] Duplicate stage execution detected: _process_information. Reusing cached data.")
            def info_gen_message_cached():
                yield AIMessage(content="I have your personalized list ready. Let's proceed!")
            return {
                "next_step": "recommend",
                "ui_action": "OPEN_EXPLORE",
                "stream": info_gen_message_cached(),
                "recommended_attractions": self.state["attractions"],
                "accommodations": self.state.get("accommodations", []),
                "restaurants": self.state.get("restaurants", []),
                "map_data": self.recommend_agent.generate_map_data(self.state["attractions"]),
                "state": self.state.copy()
            }

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
                    weather_summary_str = weather_data_result['summary']
                    self.state["weather_summary"] = weather_summary_str
                    self.state["weather_forecast"] = weather_data_result
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
        # Discovery breadth is now scaled to trip duration rather than a
        # fixed 20, so a 10-day trip genuinely sees more than 10-14 choices.
        # Selection is still entirely the user's (Requirement 1C).
        # ================================================================
        try:
            _days_int = int(str(user_days_str or 5).split('-')[0])
        except (TypeError, ValueError):
            _days_int = 5
        discovery_target = max(30, min(120, _days_int * 6))
        if is_state_search:
            print(f"[STATE_SEARCH] Running state-wide search for {display_name}. target={discovery_target}")
            attractions_from_info_agent = self.info_agent.get_attractions_for_state(
                state_name=display_name,
                state_cities=state_cities,
                user_prefs=user_prefs,
                weather_summary=self.state.get("weather_summary"),
                total_number=discovery_target
            )
        else:
            print(f"[DEBUG] Calling info_agent.get_attractions for city '{city}'. target={discovery_target}")
            attractions_from_info_agent = self.info_agent.get_attractions(
                lat=city_coordinates["lat"],
                lng=city_coordinates["lng"],
                user_prefs=user_prefs,
                weather_summary=self.state.get("weather_summary"),
                number=discovery_target,
                poi_type="tourist_attraction"
            )
        
        self.state["attractions"] = [normalize_poi(p) for p in (attractions_from_info_agent or [])]
        self.state["information_processed"] = True
        print(f"[ATTRACTION_PIPELINE] completed total_pois={len(self.state['attractions'])}")
        print(f"[DEBUG] Attractions updated: {len(self.state['attractions'])} items.")
        # ---- Accommodations ----
        import concurrent.futures
        all_accommodations = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_to_tier = {
                    executor.submit(
                        self.info_agent.get_accommodations,
                        lat=city_coordinates["lat"],
                        lng=city_coordinates["lng"],
                        budget=tier,
                        number=4
                    ): tier for tier in ["budget", "medium", "luxury"]
                }
                for future in concurrent.futures.as_completed(future_to_tier):
                    tier = future_to_tier[future]
                    try:
                        accs = future.result()
                        if accs:
                            all_accommodations.extend(accs)
                    except Exception as exc:
                        print(f"[ERROR] Failed to fetch {tier} accommodations: {exc}")
                        
            self.state["accommodations"] = [normalize_poi(a) for a in all_accommodations]
            print(f"[DEBUG] Accommodations updated: {len(self.state.get('accommodations', []))} items across 3 tiers.")
        except Exception as e:
            print(f"[ERROR] ThreadPoolExecutor failed for accommodations: {e}")
            self.state["accommodations"] = []


        try:
            restaurants = self.info_agent.poi_manager.get_restaurants(
                lat=city_coordinates["lat"],
                lng=city_coordinates["lng"],
                radius=4000,
                number=30,
            )
            self.state["restaurants"] = [normalize_poi(r) for r in (restaurants or [])]
            print(f"[DEBUG] Restaurants updated: {len(self.state.get('restaurants', []))} items.")
        except Exception as e:
            print(f"[ERROR] Failed to fetch restaurants: {e}")
            self.state["restaurants"] = []

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
            "restaurants": self.state.get("restaurants", []),
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
        # Real provenance captured by the retrieval agent (ChromaDB metadata or
        # live web results). Empty when nothing was actually retrieved.
        self.state["rag_sources"] = getattr(self.retrieval_agent, "last_sources", []) or []
        
        # Attempt to extract POIs from RAG knowledge
        rag_pois = []
        if retrieved_knowledge and "No additional background knowledge" not in retrieved_knowledge:
            lat = user_prefs.get("_dest_lat", 0.0)
            lng = user_prefs.get("_dest_lng", 0.0)
            try:
                rag_pois = self.recommend_agent.extract_pois_from_text(retrieved_knowledge, city, lat, lng)
            except Exception as rag_err:
                print(f"[RETRIEVAL] POI extraction from RAG text failed (non-fatal): {rag_err}")
                rag_pois = []
            
        if rag_pois:
            existing_pois = self.state.get("attractions", [])
            existing_names = {p.get("name", "").lower().strip() for p in existing_pois if p.get("name")}
            
            new_rag_pois = []
            for rp in rag_pois:
                name_lower = rp.get("name", "").lower().strip()
                if not name_lower: continue
                
                is_duplicate = False
                for ex_name in existing_names:
                    if len(name_lower) > 5 and len(ex_name) > 5:
                        if name_lower in ex_name or ex_name in name_lower:
                            is_duplicate = True
                            break
                    elif name_lower == ex_name:
                        is_duplicate = True
                        break
                        
                if not is_duplicate:
                    img_url = rp.get("image_url")
                    if not img_url or not str(img_url).startswith("http"):
                        rp["image_url"] = None
                    
                    new_rag_pois.append(rp)
                    existing_names.add(name_lower)
            
            if new_rag_pois:
                print(f"[DEBUG] Adding {len(new_rag_pois)} RAG-extracted POIs to state['attractions']")
                self.state["attractions"] = existing_pois + new_rag_pois
                
        return {
            "next_step": "recommend",
            "retrieved_knowledge": retrieved_knowledge,
            "rag_sources": self.state.get("rag_sources", []),
            "state": self.state.copy()
        }
        
    def _process_recommend(self, selected_attraction_ids=None, **kwargs):
        """Process recommend agent step"""
        try:
            user_prefs = self.state["user_info"]
            attractions = self.state["attractions"]
            user_input = kwargs.get('user_input', '').strip().lower()
            
            # NLP mapping for confirmation phrases
            confirmation_phrases = ['go ahead', 'continue', 'proceed', 'build my plan', 'create itinerary', 'continue with these', 'yes, continue']
            is_confirmation = any(phrase in user_input for phrase in confirmation_phrases)

            # If user confirms but didn't select any specific attractions via UI
            if is_confirmation and not selected_attraction_ids:
                days = int(user_prefs.get('days', 3))
                required = days * 2
                # Auto-select top required attractions
                selected_attractions = attractions[:required] if attractions else []
                self.state["selected_attractions"] = selected_attractions
                
                # Fetch accommodation if selected
                if 'selected_accommodation_id' in kwargs and kwargs['selected_accommodation_id']:
                    accs = self.state.get("accommodations", [])
                    acc = next((a for a in accs if a["id"] == kwargs['selected_accommodation_id']), None)
                    if acc:
                        self.state["selected_accommodation"] = acc
                        
                def transition_generator():
                    yield AIMessage(content=f"You didn't select specific attractions, so I've automatically selected {len(selected_attractions)} highly recommended places for your trip. Planning your itinerary now...")
                    
                return {
                    "next_step": "strategy",
                    "stream": transition_generator(),
                    "selected_attractions": selected_attractions
                }
            
            # Check if we have selected_attraction_ids provided
            elif selected_attraction_ids or is_confirmation:
                
                # Fetch accommodation if selected
                if 'selected_accommodation_id' in kwargs and kwargs['selected_accommodation_id']:
                    accs = self.state.get("accommodations", [])
                    wanted = str(kwargs['selected_accommodation_id'])
                    acc = next((a for a in accs if str(a.get("id")) == wanted), None)
                    if acc:
                        self.state["selected_accommodation"] = acc

                wanted_ids = {str(i) for i in (selected_attraction_ids or []) if i is not None and str(i) != ''}
                selected_attractions = []
                if wanted_ids:
                    for a in attractions or []:
                        if not a:
                            continue
                        aid = str(a.get("id") or '')
                        aname = (a.get("name") or '').strip().lower()
                        if aid in wanted_ids or aname in {w.lower() for w in wanted_ids}:
                            selected_attractions.append(a)
                self.state["selected_attractions"] = selected_attractions
                
                # Validation check
                days = int(user_prefs.get('days', 3))
                required = days * 2
                
                # "force_continue" flag is sent by frontend if user wants to bypass warning
                force_continue = kwargs.get('force_continue', 'false').lower() == 'true'
                
                if len(selected_attractions) < required and not force_continue and not is_confirmation:
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
                
                # CRITICAL FIX: InformationAgent._rerank_attractions_with_llm() already:
                #   1. Calls _heuristic_interest_match() to set matched_interests
                #   2. Sets recommendation_type = 'interest_based' | 'popular'
                #   3. Sorts by _internal_score
                # Do NOT re-run RecommendAgent which uses a different opaque scorer
                # that discards the correct interest matching. Use state attractions directly.
                
                # Apply lightweight fallback classification for any POIs missing recommendation_type
                hobbies = user_prefs.get('hobbies', '').lower()
                for attr in attractions:
                    if 'recommendation_type' not in attr:
                        # Apply heuristic from InformationAgent
                        matched = self.info_agent._heuristic_interest_match(attr, user_prefs)
                        attr['matched_interests'] = matched
                        attr['recommendation_type'] = 'interest_based' if matched else 'popular'
                
                recommended = attractions  # Already ranked and labelled by InformationAgent
                
                # Add specific logging for recommendations
                interest_count = sum(1 for a in recommended if a.get('recommendation_type') == 'interest_based')
                popular_count = sum(1 for a in recommended if a.get('recommendation_type') == 'popular')
                print(f"[RECOMMENDATION DEBUG] destination={user_prefs.get('city')} interests={hobbies}")
                print(f"[RECOMMENDATION DEBUG] total recommendations = {len(recommended)}")
                print(f"[RECOMMENDATION DEBUG] interest based count = {interest_count}")
                print(f"[RECOMMENDATION DEBUG] popular count = {popular_count}")
                if interest_count == 0 and hobbies:
                    print(f"[RECOMMENDATION WARNING] 0 interest-based matches despite hobbies='{hobbies}'. Check keyword_map in InformationAgent._heuristic_interest_match().")
                
                # Create a generator that yields the recommendation message
                def recommendation_generator():
                    yield AIMessage(content="Here are some recommended attractions and accommodations for you.")
                
                return {
                    "next_step": "recommend",  # Stay on this step until user selects attractions
                    "ui_action": "OPEN_EXPLORE",
                    "stream": recommendation_generator(),
                    "recommended_attractions": recommended,
                    "accommodations": self.state.get("accommodations", []),
                    "restaurants": self.state.get("restaurants", []),
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
    
    # ------------------------------------------------------------------
    # Selection reconciliation (Phase 8 invariants 1, 2, 3, 12)
    # ------------------------------------------------------------------
    @staticmethod
    def _day_key_num(key):
        m = re.search(r"(\d+)", str(key))
        return int(m.group(1)) if m else None

    @staticmethod
    def _norm_name(name):
        return re.sub(r"\s+", " ", str(name or "")).strip().lower()

    def _reconcile_plan_with_selection(self, selected_attractions, daily_plan,
                                       planned_attractions, total_days):
        """Guarantee the user's selection drives the plan, or is reported.

        Returns ``(daily_plan, planned_attraction_objects, unscheduled)`` where
        *daily_plan* contains every selected attraction name that could be
        placed, *planned_attraction_objects* are the original provider records
        (ids and coordinates preserved verbatim), and *unscheduled* lists what
        could not be scheduled together with a machine-readable reason.
        """
        selected = [s for s in (selected_attractions or []) if isinstance(s, dict) and s.get("name")]
        planned_objs = [p for p in (planned_attractions or []) if isinstance(p, dict) and p.get("name")]
        plan = {}
        for key, names in (daily_plan or {}).items():
            if isinstance(names, list):
                plan[str(key)] = list(names)

        try:
            days_int = max(1, int(total_days or 1))
        except (TypeError, ValueError):
            days_int = 1
        existing_nums = {self._day_key_num(k) for k in plan}
        for d in range(1, days_int + 1):
            if d not in existing_nums:
                plan[f"day{d}"] = []
        if not plan:
            plan = {f"day{d}": [] for d in range(1, days_int + 1)}

        planned_names = set()
        for names in plan.values():
            planned_names.update(self._norm_name(n) for n in names)
        planned_names.update(self._norm_name(p.get("name")) for p in planned_objs)
        planned_names.discard("")

        def already_planned(name):
            n = self._norm_name(name)
            if not n:
                return False
            if n in planned_names:
                return True
            # Tolerate small wording differences from the LLM.
            for existing in planned_names:
                if len(n) > 6 and len(existing) > 6 and (n in existing or existing in n):
                    return True
            return False

        unscheduled = []
        missing = [s for s in selected if not already_planned(s.get("name"))]
        for spot in missing:
            # Put it on the currently lightest day so a full day cannot hide it.
            target = min(plan.keys(), key=lambda k: (len(plan[k]), self._day_key_num(k) or 999))
            if len(plan[target]) >= 8:
                unscheduled.append({
                    "name": spot.get("name"),
                    "id": str(spot.get("id")) if spot.get("id") is not None else None,
                    "day": self._day_key_num(target),
                    "reason": "all_days_already_full",
                    "detail": "Every day already holds the maximum 8 activities; "
                              "shorten the trip or select fewer places.",
                })
                print(f"[WORKFLOW][SELECTION] '{spot.get('name')}' could not be placed: all days full.")
                continue
            plan[target].append(spot.get("name"))
            planned_names.add(self._norm_name(spot.get("name")))
            print(f"[WORKFLOW][SELECTION] Re-inserted selected attraction "
                  f"'{spot.get('name')}' into {target} (the planner had dropped it).")

        # Resolve every planned name back to its authoritative provider record.
        obj_map = {}
        for p in planned_objs:
            obj_map.setdefault(self._norm_name(p.get("name")), p)
        for s in selected:  # selected records win: they carry ids/coords from the provider
            obj_map[self._norm_name(s.get("name"))] = s

        ordered_keys = sorted(plan.keys(), key=lambda k: (self._day_key_num(k) or 999, str(k)))
        reconciled_plan = {k: plan[k] for k in ordered_keys}
        merged_details = []
        seen = set()
        for key in ordered_keys:
            cleaned_names = []
            for name in plan[key]:
                n = self._norm_name(name)
                obj = obj_map.get(n)
                if obj is None:
                    # Free-time labels and unmatchable names: leave them in the
                    # plan for RouteAgent, which reports them as unscheduled.
                    cleaned_names.append(name)
                    continue
                cleaned_names.append(obj.get("name"))
                if n not in seen:
                    seen.add(n)
                    merged_details.append(obj)
            plan[key] = cleaned_names

        # Anything selected that never made it into the merged list at all.
        for s in selected:
            n = self._norm_name(s.get("name"))
            if n not in seen and not any(u.get("name") == s.get("name") for u in unscheduled):
                unscheduled.append({
                    "name": s.get("name"),
                    "id": str(s.get("id")) if s.get("id") is not None else None,
                    "day": None,
                    "reason": "dropped_by_planner",
                    "detail": "The daily planner did not include this selection on any day.",
                })

        return reconciled_plan, merged_details, unscheduled

    def _process_strategy(self, **kwargs):
        """Process strategy agent step"""
        # Check if this is a confirm selection request or a satisfaction confirmation
        user_input_lower = kwargs.get('user_input', '').lower()
        confirm_phrases = ['here are my selected attractions', 'plan my route', 'plan the route', 'continue']
        is_confirm_selection = any(phrase in user_input_lower for phrase in confirm_phrases)
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
            # (Moved state updates to the end to ensure idempotency if an exception occurs)
            
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
            
            self.state["daily_plan"] = strategy_result.get("daily_plan", {}) # Store the daily plan

            # ------------------------------------------------------------------
            # Phase 8 invariant 1/2/3: the traveller's SELECTION is authoritative.
            # ``plan_remaining_time`` returns the flat list of attractions the
            # planner actually placed, which is a subset of the selection (it can
            # drop items when a day fills up or a name does not match). Assigning
            # that subset straight to state["attractions"] used to silently
            # delete selected places from the trip, so they vanished from the
            # map, budget and email with no explanation. We now reconcile the
            # daily plan with the selection: anything missing is re-inserted on
            # the lightest day, and whatever still cannot be placed is reported
            # as unscheduled instead of disappearing.
            # ------------------------------------------------------------------
            reconciled_plan, planned_details, unscheduled = self._reconcile_plan_with_selection(
                selected_attractions=selected_attractions,
                daily_plan=self.state["daily_plan"],
                planned_attractions=strategy_result["additional_attractions"],
                total_days=total_days,
            )
            self.state["daily_plan"] = reconciled_plan
            self.state["attractions"] = planned_details
            self.state["additional_attractions"] = planned_details
            self.state["unscheduled_attractions"] = unscheduled
            if unscheduled:
                print(f"[WORKFLOW][SELECTION] {len(unscheduled)} selected attraction(s) could not be "
                      f"scheduled: {[u.get('name') for u in unscheduled]}")
            # Preserve the discovery list for the Explore/map pages; the planned
            # subset only drives the itinerary.
            self.state["planned_attractions"] = planned_details

            # ------------------------------------------------------------------
            # Car rental decision — ONE authoritative state flag (Phase 4D/8).
            # Priority: explicit user choice > planner/agent recommendation.
            # Provider availability NEVER overwrites this flag; it only controls
            # whether we have real quotes or must fall back to an estimate.
            # ------------------------------------------------------------------
            user_choice = self.state["user_info"].get("rental_choice")
            if user_choice is not None:
                self.state["should_rent_car"] = bool(user_choice)
                self.state["rental_decision_source"] = "user_selection"
            else:
                self.state["should_rent_car"] = False
                self.state["rental_decision_source"] = "default"
            print(f"[DEBUG] should_rent_car initialised to {self.state['should_rent_car']} "
                  f"(source={self.state['rental_decision_source']})")

            # Get AI recommendation about the overall plan
            # This will also analyze the recommendation and update should_rent_car in user_prefs
            ai_recommendation = self.strategy_agent.get_ai_recommendation(
                user_prefs=self.state["user_info"],
                selected_spots=selected_attractions,
                total_days=total_days,
            )

            if self.state["rental_decision_source"] != "user_selection":
                # Value written into user_prefs by extract_rental_recommendation()
                ai_should_rent_car = bool(self.state["user_info"].get("should_rent_car", False))
                if ai_should_rent_car != self.state["should_rent_car"]:
                    self.state["should_rent_car"] = ai_should_rent_car
                    self.state["rental_decision_source"] = "agent_recommendation"

            print(f"[DEBUG] should_rent_car after AI recommendation: {self.state['should_rent_car']} "
                  f"(source={self.state['rental_decision_source']})")
            
            # Create a copy of the state to return
            self.state["ai_recommendation_generated"] = True
            state_copy = self.state.copy()
            
            return {
                "next_step": "strategy",
                "stream": ai_recommendation,
                "remaining_hours": strategy_result["remaining_hours"],
                "additional_attractions": planned_details,
                "unscheduled_attractions": unscheduled,
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
                print("[DEBUG] Handling satisfaction confirmation without prior recommendation generation")
                # This means user sent satisfaction message before going through normal flow
                # We need to ensure should_rent_car is correctly set to false in this case
                self.state["should_rent_car"] = False # Ensure it's false
                print("[DEBUG] Set should_rent_car to False for satisfaction message without prior recommendation")
            
            # ALWAYS GO TO COMMUNICATION STEP
            next_step = "communication"
            print(f"[INFO] Decision point: Setting next_step to '{next_step}' to generate summaries and travel tips.")
            
            # Create a generator that yields the transition message
            def transition_generator():
                if next_step == "route":
                    yield AIMessage(content="Moving to route planning...")
                else:
                    yield AIMessage(content="Planning your final itinerary...")
            
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
            # Same-city trip (origin == destination): there is no intercity leg
            # to plan, and asking for "Guwahati -> Guwahati" produced nonsense
            # arrival/departure options that then leaked into the budget/email.
            def _city_key(value):
                return re.sub(r"[^a-z0-9 ]", "", str(value).strip().lower()).replace("  ", " ").strip()
            same_city = _city_key(origin) == _city_key(destination)
            if same_city:
                print(f"[TRANSIT][SKIP] Origin and destination are the same city ({origin}); "
                      f"no intercity transport needed.")
                transit_options = normalize_transit_options(
                    {
                        "origin": origin,
                        "destination": destination,
                        "estimation_note": "You are already travelling within your destination city, so no "
                                           "intercity journey was planned. Local transport is estimated below.",
                        "currency": "INR",
                        "intercity_transport": [],
                        "local_transport": [],
                        "recommended_mode": None,
                        "recommended_local_mode": None,
                    },
                    origin=origin, destination=destination,
                    num_people=self.state["user_info"].get("people") or 1,
                    same_city=True,
                )
            else:
                try:
                    print(f"[DEBUG] Fetching transit options from {origin} to {destination}...")
                    transit_options = self.transit_agent.get_transit_options(
                        origin=origin,
                        destination=destination,
                        start_date=start_date,
                        budget_level=budget_level,
                        is_international=self.state["user_info"].get("is_international", False),
                        num_people=self.state["user_info"].get("people") or 1
                    )
                    # Normalization is idempotent, so this only repairs payloads
                    # that never went through TransitAgent (mocked agents, older
                    # cached state) and guarantees the session's own origin and
                    # destination are the ones the Transport tab displays.
                    transit_options = normalize_transit_options(
                        transit_options,
                        origin=origin,
                        destination=destination,
                        num_people=self.state["user_info"].get("people") or 1,
                    )
                except Exception as te:
                    print(f"[WARN] Failed to get transit options: {te}")
                    transit_options = normalize_transit_options(
                        None, origin=origin, destination=destination,
                        num_people=self.state["user_info"].get("people") or 1,
                    )

        # ------------------------------------------------------------------
        # Car rental: provider availability is DATA, not a decision. A failed
        # rental API call must never flip should_rent_car back to False (that
        # silently erased the recommendation from the trip and its budget).
        # ------------------------------------------------------------------
        car_info = None
        fuel_price = None
        self.state.setdefault("rental_decision_source", "state")
        if self.state.get("should_rent_car", False):
            try:
                print(f"[DEBUG] Fetching local car rental options in {destination}...")
                car_info = self.info_agent.search_car_rentals(
                    self.state["user_info"].get("city", ""),
                    start_date,
                    end_date,
                    driver_age=self.state["user_info"].get("age", 30)
                )
            except Exception as car_e:
                print(f"[PROVIDER][CAR_RENTAL][UNAVAILABLE] Could not fetch car/fuel info: {car_e}")
                car_info = None
            if car_info:
                self.state["car_rental_available"] = True
                self.state["car_rental_source"] = "provider"
            else:
                self.state["car_rental_available"] = False
                self.state["car_rental_source"] = "fallback_estimate"
                print("[PROVIDER][CAR_RENTAL][UNAVAILABLE] Rental provider returned no options; "
                      "keeping the self-drive recommendation and using a fallback cost estimate.")
            try:
                fuel_price = self.info_agent.get_fuel_price(self.state["user_info"].get("city", ""))
            except Exception as fuel_e:
                print(f"[PROVIDER][FUEL][UNAVAILABLE] Fuel price lookup failed: {fuel_e}")
                fuel_price = None

        budget = self.budget_agent.estimate_budget(
            all_attractions_objects,
            self.state["user_info"],
            self.state.get("should_rent_car", False),
            car_info,
            fuel_price,
            transit_options
        )
        
        # Integrate selected accommodation into budget early.
        # Provider records use ``price_per_night``; the old ``"price" in`` check
        # meant this block almost never ran, so the chosen stay was excluded.
        selected_acc = self.state.get("selected_accommodation")
        if selected_acc:
            acc_price_raw = selected_acc.get("price_per_night", selected_acc.get("price"))
            price_val = None
            if isinstance(acc_price_raw, (int, float)):
                price_val = float(acc_price_raw)
            elif acc_price_raw not in (None, ""):
                price_val = extract_number(str(acc_price_raw))
            if price_val:
                nights = max(1, days - 1)
                people_count = int(self.state["user_info"].get("people") or 1)
                rooms = max(1, (people_count + 1) // 2)
                budget["accommodation"] = round(price_val * nights * rooms, 2)
                budget["accommodation_source"] = "selected_accommodation"
                budget["estimated_total"] = (
                    budget.get("activities", 0) +
                    budget.get("food", 0) +
                    budget.get("local_transport", 0) +
                    budget["accommodation"] +
                    budget.get("car_rental", 0) +
                    budget.get("fuel", 0) +
                    budget.get("transport", 0)
                )
                budget["miscellaneous"] = round(budget["estimated_total"] * 0.10, 2)
                budget["estimated_total"] += budget["miscellaneous"]
                budget["estimated_total"] = round(budget["estimated_total"], 2)

                # Recalculate normalized fields
                if budget.get("budget_limit"):
                    budget["remaining_budget"] = round(budget["budget_limit"] - budget["estimated_total"], 2)
                    budget["utilization_percentage"] = round((budget["estimated_total"] / budget["budget_limit"] * 100), 1)
                    budget["within_budget"] = budget["estimated_total"] <= budget["budget_limit"]

                    if not budget["within_budget"] and budget.get("budget_type") == "strict":
                        budget["budget_warning"] = (
                            f"Your requested trip cannot currently fit within ₹{budget['budget_limit']:,}. "
                            f"The closest feasible configuration is ₹{int(budget['estimated_total']):,}. "
                            "Consider reducing travel days, choosing fewer paid attractions, or selecting cheaper intercity transport to fit within your strict budget."
                        )
                        budget["budget_infeasible"] = True
            else:
                budget["accommodation_source"] = "destination_average"
                print("[BUDGET][ACCOMMODATION] Selected accommodation has no usable nightly price; "
                      "keeping the destination-average estimate.")

        # Store in state so route doesn't have to re-fetch. The normalized view
        # is the single breakdown shared by the budget page, itinerary payload,
        # API responses and the email (Phase 5/6).
        self.state["budget"] = budget
        self.state["budget_normalized"] = normalize_budget(budget)
        self.state["transit_options"] = transit_options

        # NOTE: A strict budget that cannot be fully met is surfaced as an
        # ADVISORY warning, never as a hard stop. Previously this step returned
        # next_step="complete" with an EMPTY itinerary and no transit_options the
        # moment the optimiser could not fit a strict budget, which skipped the
        # route step entirely and left the traveler staring at a "0-Day
        # Itinerary" and an empty Transport tab. We now always continue to the
        # route step (via the normal "communication" pause below) so a complete
        # day-by-day plan and the transit options are always produced; the
        # over-budget warning is carried in state["budget"] and re-surfaced by
        # _process_route alongside the finished itinerary.

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
            unscheduled = list(self.state.get("unscheduled_attractions") or [])
            if daily_plan_name_dict and isinstance(daily_plan_name_dict, dict) and all_attractions_objects:
                # Key the authoritative records by name, normalised name and id
                # so the planned names resolve back to the SAME provider objects
                # (stable ids/coordinates survive recommendation -> itinerary ->
                # map -> email).
                all_spots_map = {}
                for spot in all_attractions_objects:
                    if not spot or "name" not in spot:
                        continue
                    all_spots_map[spot["name"]] = spot
                    all_spots_map[self._norm_name(spot["name"])] = spot
                    if spot.get("id") is not None:
                        all_spots_map[str(spot["id"])] = spot
                itinerary = self.route_agent.format_daily_plan_to_itinerary(
                    daily_plan_name_dict,
                    all_spots_map,
                    start_date,
                    unscheduled_out=unscheduled,
                )
            else:
                print("[ERROR] Could not generate itinerary: daily_plan_name_dict or all_attractions_objects missing/invalid.")

            
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
                    for spot in day_plan_item.get("activities", []):
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
                acc_loc = selected_acc.get("location") if isinstance(selected_acc.get("location"), dict) else {}
                acc_lat = acc_loc.get("lat") or selected_acc.get("latitude")
                acc_lng = acc_loc.get("lng") or selected_acc.get("longitude")
                acc_spot = {
                    "id": f"acc_{selected_acc.get('id', 'hotel')}",
                    "attraction_id": str(selected_acc.get("id")) if selected_acc.get("id") is not None else None,
                    "name": selected_acc.get("name", "Hotel"),
                    "category": "Accommodation",
                    "description": f"Check-in to your accommodation: {selected_acc.get('name', '')}",
                    "location": {"lat": acc_lat, "lng": acc_lng},
                    "latitude": acc_lat,
                    "longitude": acc_lng,
                    "start_time": "14:00", # Standard check-in time
                    "end_time": "15:00",
                    "price_per_night": selected_acc.get("price_per_night", selected_acc.get("price")),
                    "currency": selected_acc.get("currency", "INR"),
                    "is_accommodation": True
                }
                # Insert accommodation then re-sort the whole day by start_time
                # so the displayed order is always chronological (e.g. 14:00
                # check-in appears after the 09:00–13:00 morning attractions).
                if "activities" in itinerary[0]:
                    itinerary[0]["activities"].append(acc_spot)
                    itinerary[0]["activities"] = self.route_agent._sort_spots_by_time(
                        itinerary[0]["activities"]
                    )
                # Assign to top-level canonical key
                itinerary[0]["accommodation"] = selected_acc

            # FINAL ITINERARY VALIDATION
            # Ensure no dates repeat and no hallucinatory attractions persist.
            seen_dates = set()
            valid_attractions = {s["name"] for s in all_attractions_objects if "name" in s}
            valid_attractions.update([selected_acc.get("name", "Hotel") if selected_acc else ""])
            valid_names_norm = {self._norm_name(v) for v in valid_attractions if v}
            allowed_labels = {"Relaxation", "Leisure", "Dinner", "Lunch Break", "Accommodation"}
            
            for day_idx, day_plan in enumerate(itinerary):
                # Date validation
                day_date = day_plan.get("date")
                if day_date in seen_dates:
                    print(f"[WARN] Repeating date {day_date} found. Overriding.")
                    # Fix the date if it's repeated
                    fixed_date = (start_date_dt + timedelta(days=day_idx)).strftime("%d %b %Y")
                    day_plan["date"] = fixed_date
                    seen_dates.add(fixed_date)
                else:
                    seen_dates.add(day_date)
                    
                # Attraction validation
                valid_acts = []
                for act in day_plan.get("activities", []):
                    name = act.get("name")
                    is_known = (
                        name in valid_attractions
                        or self._norm_name(name) in valid_names_norm
                        or name in allowed_labels
                        or act.get("is_meal")
                        or act.get("is_accommodation")
                        or act.get("is_semantic_label")
                    )
                    if is_known:
                        valid_acts.append(act)
                    else:
                        print(f"[WARN] Found hallucinated POI '{name}' in final itinerary. Removing.")
                        entry = {
                            "name": name,
                            "id": str(act.get("id")) if act.get("id") is not None else None,
                            "day": day_plan.get("day"),
                            "reason": "removed_unknown_poi",
                            "detail": "The planner referenced a place that no data provider confirmed; "
                                      "it was removed rather than invented.",
                        }
                        unscheduled.append(entry)
                        day_plan.setdefault("unscheduled", []).append(entry)
                
                day_plan["activities"] = valid_acts
                # Re-issue the sequence after filtering so it stays 1..N.
                for seq, act in enumerate(valid_acts, start=1):
                    act["sequence"] = seq
                    act["day"] = day_plan.get("day")
                    
            # Store in state
            self.state["itinerary"] = itinerary
            self.state["unscheduled_attractions"] = unscheduled
            self.state["budget"] = budget
            self.state["budget_normalized"] = normalize_budget(budget)
            self.state["transit_options"] = transit_options
            if unscheduled:
                print(f"[WORKFLOW][COMPLETE] {len(unscheduled)} selected/planned item(s) are NOT in the "
                      f"final itinerary: {[u.get('name') for u in unscheduled]}")
            
            # A strict budget that could not be fully met is surfaced as an
            # ADVISORY warning alongside a COMPLETE itinerary (returned below),
            # never as a hard stop that discards the plan. The budget_warning and
            # budget_infeasible flags are already included in the final return so
            # the UI can show "over budget" without hiding the day-by-day plan.
            if budget.get("budget_type") == "strict" and budget.get("budget_infeasible", False):
                print(f"[BUDGET][STRICT][ADVISORY] {budget.get('budget_warning', 'Trip exceeds strict budget.')} Delivering full itinerary with warning.")
                
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
            
            # Send email exactly once
            if not self.state.get("email_sent"):
                user_email = self.state["user_info"].get("email")
                if user_email:
                    try:
                        accs = self.state.get("accommodations") or []
                        chosen_acc = self.state.get("selected_accommodation") or (accs[0] if accs else None)
                        auto_ctx = {
                            "origin": self.state["user_info"].get("origin_city"),
                            "days": self.state["user_info"].get("days"),
                            "people": self.state["user_info"].get("people"),
                            "kids": self.state["user_info"].get("kids"),
                            "start_date": self.state["user_info"].get("start_date"),
                            # Single source of truth: the accommodation the
                            # traveller actually picked (recommendations are only
                            # a fallback when nothing was selected).
                            "accommodation": chosen_acc,
                            "accommodation_is_selection": bool(self.state.get("selected_accommodation")),
                            "food": self.state.get("restaurants") or [],
                            "transport": self.state.get("transit_options") or transit_options,
                            "tips": self.state.get("travel_tips") or self.state.get("tips"),
                            "unscheduled": unscheduled,
                        }
                        email_status = send_trip_email(
                            user_email,
                            self.state["user_info"].get("name", "Traveler"),
                            self.state["user_info"].get("city", "your destination"),
                            itinerary,
                            budget,
                            confirmation,
                            trip_context=auto_ctx,
                        )
                        if email_status:
                            self.state["email_sent"] = True
                            print(f"[INFO] Successfully sent trip email to {user_email}")
                    except Exception as e:
                        print(f"[ERROR] Failed to send trip email to {user_email}: {e}")
                        # Don't break the trip if email fails!
                else:
                    print("[EMAIL] No user email in state; skipping auto-send. "
                          "The traveler can send it on demand via the Email modal (/api/email).")

            normalized_budget = normalize_budget(budget)
            return {
                "next_step": "complete",
                "response": confirmation,
                "itinerary": itinerary,
                "budget": budget,
                # The normalized breakdown is what the budget page, itinerary
                # page, API responses and the email all render from (Phase 5/6).
                "budget_normalized": normalized_budget,
                "optimal_route": optimal_route,
                "transit_options": transit_options,
                "unscheduled_attractions": unscheduled,
                "provider_status": self.state.get("provider_status"),
                "rental_decision_source": self.state.get("rental_decision_source"),
                "car_rental_available": self.state.get("car_rental_available"),
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

    def get_assistant_context(self):
        """Read-only assistant panel context built from REAL session state.

        The persistent travel assistant on every page renders from this, so it
        never re-runs an LLM or re-asks for information on navigation, and it can
        never leak another session's trip (the state is session-scoped).
        """
        state = self.state
        user_info = state.get("user_info") or {}
        budget = normalize_budget(state.get("budget") or state.get("budget_normalized") or {})
        itinerary = state.get("itinerary") or []
        selected = state.get("selected_attractions") or []
        transit = state.get("transit_options") or {}
        unscheduled = state.get("unscheduled_attractions") or []

        days_progressed = []
        for day in itinerary:
            if not isinstance(day, dict):
                continue
            days_progressed.append({
                "day": day.get("day"),
                "date": day.get("date"),
                "activity_count": len(day.get("activities") or []),
            })

        warnings = []
        if budget.get("status") == "over" and budget.get("budget_limit"):
            warnings.append(
                f"Estimated total ₹{budget['total']:,.0f} exceeds your "
                f"₹{budget['budget_limit']:,.0f} limit."
            )
        elif budget.get("status") == "over":
            warnings.append("The trip is currently estimated over your budget.")
        if budget.get("status") == "incomplete":
            warnings.append("Some cost data was unavailable, so the budget may be understated.")
        if unscheduled:
            warnings.append(
                f"{len(unscheduled)} selected place(s) could not be scheduled: "
                + ", ".join(str(u.get("name")) for u in unscheduled[:5])
                + ("…" if len(unscheduled) > 5 else "")
            )
        if state.get("should_rent_car") and state.get("car_rental_available") is False:
            warnings.append("Live car-rental quotes were unavailable; self-drive costs are estimates.")

        return {
            "step": state.get("current_step") or "chat",
            "trip": {
                "destination": user_info.get("city"),
                "origin": user_info.get("origin_city"),
                "days": user_info.get("days"),
                "people": user_info.get("people"),
                "kids": user_info.get("kids"),
                "start_date": user_info.get("start_date"),
                "budget_category": user_info.get("budget"),
                "budget_amount": user_info.get("budget_amount"),
                "budget_strictness": user_info.get("budget_strictness"),
                "hobbies": user_info.get("hobbies"),
                "health": user_info.get("health"),
            },
            "missing_fields": [
                f for f in self.chat_agent.required_fields
                if not self.chat_agent.is_valid_field(f, user_info.get(f))
            ],
            "selected_attractions": [
                {"id": str(a.get("id")) if a.get("id") is not None else None, "name": a.get("name")}
                for a in selected if isinstance(a, dict)
            ],
            "accommodation": state.get("selected_accommodation"),
            "itinerary_days": days_progressed,
            "unscheduled_attractions": unscheduled,
            "transit": {
                "recommended_mode": transit.get("recommended_mode") if isinstance(transit, dict) else None,
                "recommended_local_mode": transit.get("recommended_local_mode") if isinstance(transit, dict) else None,
                "intercity_options": (transit.get("intercity_transport") or []) if isinstance(transit, dict) else [],
                "local_options": (transit.get("local_transport") or []) if isinstance(transit, dict) else [],
                "same_city": bool(transit.get("same_city")) if isinstance(transit, dict) else False,
            },
            "self_drive_recommended": bool(state.get("should_rent_car")),
            "rental_decision_source": state.get("rental_decision_source"),
            "budget": budget,
            "budget_warnings": warnings,
            "weather_summary": state.get("weather_summary"),
            "travel_tips": state.get("travel_tips"),
            "provider_status": state.get("provider_status"),
            "email_sent": bool(state.get("email_sent")),
            "conversation": self.get_conversation_history(),
        }

    def get_conversation_history(self, limit=40):
        """Return the real chat transcript persisted on the ChatAgent."""
        history = getattr(self.chat_agent, "conversation_history", []) or []
        turns = []
        for msg in history[-limit:]:
            role = "assistant"
            if hasattr(msg, "type"):
                role = "user" if msg.type == "human" else "assistant"
            content = getattr(msg, "content", msg)
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )
            content = str(content).strip()
            if content:
                turns.append({"role": role, "content": content})
        return turns