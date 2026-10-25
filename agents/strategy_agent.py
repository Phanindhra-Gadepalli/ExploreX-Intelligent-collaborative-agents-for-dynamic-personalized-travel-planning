import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import math
import random
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from typing import Generator
import utils
import json
import re
from pydantic import BaseModel, Field
from typing import Dict, List

class DailyPlanOutput(BaseModel):
    plan: Dict[str, List[str]] = Field(description="A dictionary mapping day names like 'day1', 'day2' to lists of attraction names.")

class StrategyAgent:
    # Free-time labels the planner may emit instead of a real POI.  They are
    # deliberately *not* attractions and are excluded from the flat attraction
    # list, the budget and the map.  The set mirrors RouteAgent.SEMANTIC_LABELS.
    FILLER_LABELS = {
        "relaxation", "leisure", "free time", "rest", "rest day", "spare time",
        "lunch break", "lunch", "dinner", "breakfast", "acclimatization",
        "acclimatisation", "shopping", "local exploration", "walking tour",
    }

    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize StrategyAgent with AI model for planning"""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7, streaming=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Deterministic daily-plan cleanup
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _day_number(key):
        match = re.search(r"(\d+)", str(key))
        return int(match.group(1)) if match else None

    @staticmethod
    def _norm_name(name):
        return re.sub(r"\s+", " ", str(name or "")).strip().lower()

    @staticmethod
    def _strip_numbering(name):
        return re.sub(r"^\s*\d+[).\-]\s*", "", str(name or "")).strip()

    def _resolve_selected(self, name, selected_by_norm, selected_order):
        """Resolve a planner name to one of the traveller's selected places.

        Exact and case/whitespace-insensitive matches are preferred.  A
        substring match is accepted only when it identifies exactly one place,
        so "Tiger" cannot silently become "Tiger Hill" and a different POI.
        """
        cleaned = self._strip_numbering(name)
        key = self._norm_name(cleaned)
        if not key:
            return None
        if key in selected_by_norm:
            return selected_by_norm[key]

        hits = [
            selected_by_norm[n]
            for n in selected_order
            if len(key) > 5 and (
                key in n or n in key
            )
        ]
        if len(hits) == 1:
            return hits[0]
        return None

    def _finalize_daily_plan(self, raw_plan, selected_spots, total_days):
        """Make a model-proposed plan safe, complete and deterministic.

        Guarantees:
          * only the traveller's selected places and explicit free-time labels
            survive — nothing invented reaches the itinerary;
          * a selected place appears at most once across the whole trip;
          * every selected place appears on exactly one day, even when the model
            omitted it or the model call failed completely;
          * every day 1..N exists, so the UI always renders N days;
          * filler labels are never returned as attractions.
        """
        try:
            days_int = max(1, int(total_days))
        except (TypeError, ValueError):
            days_int = 1

        selected = [
            spot for spot in (selected_spots or [])
            if isinstance(spot, dict) and str(spot.get("name") or "").strip()
        ]
        selected_by_norm = {}
        selected_order = []
        for spot in selected:
            norm = self._norm_name(spot.get("name"))
            if norm and norm not in selected_by_norm:
                selected_by_norm[norm] = spot
                selected_order.append(norm)

        plan = {f"day{d}": [] for d in range(1, days_int + 1)}
        placed = set()
        seen_labels = set()

        # Group raw entries by integer day.  Unknown/out-of-range day keys are
        # ignored; their names are re-inserted below if they were selected.
        raw_by_day = {}
        for raw_key, names in (raw_plan or {}).items():
            day_num = self._day_number(raw_key)
            if day_num is None or not (1 <= day_num <= days_int):
                continue
            if not isinstance(names, list):
                continue
            raw_by_day.setdefault(day_num, []).extend(names)

        for day_num in range(1, days_int + 1):
            day_key = f"day{day_num}"
            for raw_name in raw_by_day.get(day_num, []):
                if not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                label = self._norm_name(self._strip_numbering(raw_name))
                if label in self.FILLER_LABELS:
                    # Keep one instance of each free-time label; repeating
                    # "Relaxation" on every day is not useful itinerary data.
                    if label not in seen_labels:
                        seen_labels.add(label)
                        plan[day_key].append(self._strip_numbering(raw_name))
                    continue

                spot = self._resolve_selected(raw_name, selected_by_norm, selected_order)
                if spot is None:
                    print(f"[STRATEGY] Dropping planner name '{raw_name}': "
                          "not one of the selected attractions.")
                    continue
                norm = self._norm_name(spot.get("name"))
                if norm in placed:
                    print(f"[STRATEGY] Removing duplicate across days: '{spot.get('name')}'")
                    continue
                placed.add(norm)
                plan[day_key].append(spot.get("name"))

        # Place every selected attraction that the model missed.  The lightest
        # day (earliest on ties) always receives the next one, which balances a
        # 10-place selection over 3 days as 4/3/3 instead of dumping them on
        # day 1.  This is deterministic for identical inputs.
        missing = [
            selected_by_norm[norm]
            for norm in selected_order
            if norm not in placed
        ]
        for spot in missing:
            target = min(
                (f"day{d}" for d in range(1, days_int + 1)),
                key=lambda k: (len(plan[k]), self._day_number(k) or 999),
            )
            plan[target].append(spot.get("name"))
            placed.add(self._norm_name(spot.get("name")))
            print(f"[STRATEGY] Re-inserted selected attraction "
                  f"'{spot.get('name')}' into {target}.")

        # Flat attraction list in itinerary order.  Filler labels are excluded.
        ordered_details = []
        emitted = set()
        for day_num in range(1, days_int + 1):
            for name in plan[f"day{day_num}"]:
                norm = self._norm_name(name)
                spot = selected_by_norm.get(norm)
                if spot is None or norm in emitted:
                    continue
                emitted.add(norm)
                ordered_details.append(spot)

        return plan, ordered_details

    def plan_remaining_time(self, selected_spots, total_days, all_attractions, user_prefs, weather_summary, retrieved_knowledge=None):
        print("now in plan_remaining_time")
        try:
            """Calculate remaining time and suggest additional attractions"""
            total_available_hours = int(total_days) * 8 # This seems to be unused if we get a full plan
            selected_data = []
            for spot in selected_spots or []:
                loc = spot.get("location") if isinstance(spot.get("location"), dict) else {}
                lat = loc.get("lat", loc.get("latitude", spot.get("latitude", spot.get("lat"))))
                lng = loc.get("lng", loc.get("longitude", spot.get("longitude", spot.get("lng", spot.get("lon")))))
                location = None
                try:
                    if lat is not None and lng is not None:
                        location = {"lat": float(lat), "lng": float(lng)}
                except (TypeError, ValueError):
                    location = None
                selected_data.append({
                    "id": spot.get("id"),
                    "name": spot.get("name"),
                    "estimated_duration": spot.get("estimated_duration", 2),
                    "location": location
                })
            
            max_try = 5
            daily_plan_raw = {}

            user_prefs_str = json.dumps(user_prefs, indent=2, ensure_ascii=False)
            weather_str = weather_summary if weather_summary else "No specific weather summary provided."
            
            # Extract specific requirements if they exist
            specific_requirements = user_prefs.get('specificRequirements', '')
            specific_requirements_section = ""
            if specific_requirements:
                specific_requirements_section = f"""
                Additional specific requirements/constraints from user:
                {specific_requirements}
                
                Please consider these specific requirements when creating the itinerary.
                """
            
            rag_context_section = ""
            if retrieved_knowledge:
                rag_context_section = f"""
                Background Knowledge (Local rules, history, accessibility, indoor/outdoor suitability):
                {retrieved_knowledge}
                
                Please use this knowledge to ensure the itinerary is practical and personalized.
                """

            for i in range(max_try):
                is_international = user_prefs.get('is_international', False)
                destination_city = user_prefs.get('city', 'the destination')
                
                if is_international:
                    prompt = f"""
            You are a travel advisor helping with trip logistics for {destination_city}.
            The user is planning a {total_days}-day trip to {destination_city}.

            User Preferences:
            {user_prefs_str}
            
            {specific_requirements_section}

            Weather Summary for the trip period:
            {weather_str}
            
            {rag_context_section}

            Here are the attractions they have pre-selected:
            {selected_data}

            Please create an optimized daily itinerary for the {total_days} days using ONLY the pre-selected attractions.
            - Distribute ONLY the selected attractions across the days to minimize travel time.
            - Do NOT include any other attractions. Do NOT invent new attractions.
            - Consider estimated duration. Assume a travel day is about 8 hours.
            - Group nearby attractions on the same day.
            - Consider local factors for {destination_city}: midday weather, local transport availability, and cultural timings.
            - IMPORTANT: Do NOT duplicate the same attractions across multiple days just to fill the itinerary.
            - IMPORTANT: If the selected attractions do not fill all the days, leave some days empty or allocate them to "Relaxation" or "Leisure".
            
            User preference considerations:
            - Adjust for mobility/health conditions, families with children.
            - Match attraction types with hobbies (temples, museums, beaches, nature, food markets, etc.)
            - Consider budget (budget: street food/local transport; medium: mid-range; high: luxury experiences)
                
                Do not use bold font or any markdown.
                Return ONLY a valid JSON object where keys are "day1", "day2", ..., "dayN" and values are lists of attraction names.
                Example: {{"day1": ["Attraction A", "Attraction B"], "day2": ["Attraction C"]}}
                """
                else:
                    prompt = f"""
            You are an India travel advisor helping with trip logistics within India.
            The user is planning a {total_days}-day trip to an Indian destination.

            User Preferences:
            {user_prefs_str}
            
            {specific_requirements_section}

            Weather Summary for the trip period:
            {weather_str}
            
            {rag_context_section}

            Here are the attractions they have pre-selected:
            {selected_data}

            Please create an optimized daily itinerary for the {total_days} days using ONLY the pre-selected attractions.
            - Distribute ONLY the selected attractions across the days to minimize travel time.
            - Do NOT include any other attractions. Do NOT invent new attractions.
            - Consider estimated duration. Assume a travel day is about 8 hours.
            - Group nearby attractions on the same day.
            - Consider India-specific factors: midday heat (plan outdoor activities in morning/evening),
              local transport availability, religious site timings, and festival seasons.
            - IMPORTANT: Do NOT duplicate the same attractions across multiple days just to fill the itinerary.
            - IMPORTANT: If the selected attractions do not fill all the days, leave some days empty or allocate them to "Relaxation" or "Leisure".
            
            User preference considerations:
            - Adjust for mobility/health conditions, families with children.
            - Match attraction types with hobbies (temples, forts, beaches, nature, food markets, etc.)
            - Consider budget (budget: street food/local transport; medium: mid-range; high: luxury experiences)
                
                Do not use bold font or any markdown.
                Return ONLY a valid JSON object where keys are "day1", "day2", ..., "dayN" and values are lists of attraction names.
                Example: {{"day1": ["Attraction A", "Attraction B"], "day2": ["Attraction C"]}}
                """
                
                try:
                    structured_llm = self.model.with_structured_output(DailyPlanOutput)
                    response = structured_llm.invoke([HumanMessage(content=prompt)])
                    daily_plan_raw = response.plan
                except Exception as e:
                    print(f"Structured output failed on attempt {i+1}: {e}")
                    # Try once more with a stricter prompt if it failed
                    if i == 0:
                        continue
                    else:
                        daily_plan_raw = {}
                        
                if not daily_plan_raw:
                    continue


                # Reject an empty model response and retry; final cleanup below
                # handles partial, duplicate or invented entries.
                if isinstance(daily_plan_raw, dict) and any(
                    isinstance(v, list) and v for v in daily_plan_raw.values()
                ):
                    print(f"[STRATEGY] LLM proposed daily plan: {daily_plan_raw}")
                    break

            # ONE deterministic cleanup path, used whether the model succeeded,
            # partially succeeded or failed after every retry.  The selected
            # attractions are authoritative, so a planner outage still produces
            # a balanced itinerary instead of an empty one.
            if not isinstance(daily_plan_raw, dict) or not daily_plan_raw:
                print("[STRATEGY] No usable model plan; distributing the selection deterministically.")

            daily_plan_raw, additional_attractions_details = self._finalize_daily_plan(
                daily_plan_raw, selected_spots, total_days
            )
            if selected_spots and not additional_attractions_details:
                # Defensive: _finalize_daily_plan always inserts the selection,
                # but never return an empty trip due to an unexpected shape.
                additional_attractions_details = [
                    spot for spot in selected_spots
                    if isinstance(spot, dict) and spot.get("name")
                ]

            # The function needs to return "additional_attractions" which is used later as the primary list of attractions.
            # And "remaining_hours" which seems less critical if the AI does full daily planning.
            return {
                "remaining_hours": total_available_hours,
                "additional_attractions": additional_attractions_details,
                "daily_plan": daily_plan_raw,
            }
        except Exception as e:
            print(f"Error in plan_remaining_time: {e}")
            import traceback
            traceback.print_exc()
            raise    
    def _suggest_additional_attractions(self, selected_spots, all_attractions, remaining_hours):
        """Suggest additional attractions based on remaining time"""
        # Get IDs of already selected spots
        selected_ids = [spot["id"] for spot in selected_spots]
        
        # Filter out already selected attractions
        available_attractions = [a for a in all_attractions if a["id"] not in selected_ids]
        
        if not available_attractions:
            return []
        
        # Sort by proximity to selected spots (if location data available)
        # This is a simplification - in a real app, you'd use actual distances
        scored_attractions = []
        for attraction in available_attractions:
            # Score based on duration (prefer attractions that fit in remaining time)
            duration = attraction.get("estimated_duration", 2)
            if duration <= remaining_hours:
                scored_attractions.append((attraction, duration))
        
        # Sort by duration (ascending)
        scored_attractions.sort(key=lambda x: x[1])
        # Return attractions that fit within remaining time
        result = []
        total_duration = 0
        for attraction, duration in scored_attractions:
            if total_duration + duration <= remaining_hours:
                result.append(attraction)
                total_duration += duration  
        return result
    
    def extract_rental_recommendation(self, recommendation_text, explicit_decision=None):
        """Extract car rental recommendation from AI response.

        ``explicit_decision`` is the traveller's own choice (from the UI or
        chat).  When present it is authoritative: a free-form
        ``[car_rental:YES/NO]`` marker in the model output must never override
        it.
        """
        if explicit_decision is not None:
            explicit_bool = bool(explicit_decision)
            print(f"[DEBUG] Explicit traveller rental decision={explicit_bool}; "
                  "ignoring model marker.")
            return explicit_bool
        try:
            if not isinstance(recommendation_text, str):
                return False
            original_text = recommendation_text
            recommendation_text = recommendation_text.lower()
            
            print(f"[DEBUG] Analyzing rental recommendation from text: {recommendation_text[:200]}...")
            
            # First, try to find a structured car rental marker if present
            structured_marker = re.search(r'\[car_rental:(yes|no)\]', recommendation_text, re.IGNORECASE)
            if structured_marker:
                decision = structured_marker.group(1).lower()
                should_rent = decision == 'yes'
                print(f"[DEBUG] Found structured car rental marker: [{decision}], should_rent_car = {should_rent}")
                return should_rent
            
            # Look for rental recommendation section
            car_rental_section_match = re.search(r'car rental recommendation:(.+?)(?=\n\n|\Z)', recommendation_text, re.DOTALL | re.IGNORECASE)
            if car_rental_section_match:
                car_rental_section = car_rental_section_match.group(1).lower().strip()
                print(f"[DEBUG] Found car rental section: {car_rental_section}")
                
                # Look for decisive phrases first - these are the most reliable indicators
                if car_rental_section.startswith("yes") or "i recommend renting" in car_rental_section:
                    print("[DEBUG] Found explicit YES recommendation")
                    return True
                    
                if car_rental_section.startswith("no") or "i do not recommend" in car_rental_section or "i don't recommend" in car_rental_section:
                    print("[DEBUG] Found explicit NO recommendation")
                    return False
                
                # Clear positive indicators
                positive_indicators = [
                    "recommend renting a car",
                    "would recommend renting",
                    "recommend you rent",
                    "recommend renting",
                    "should rent a car",
                    "renting a car is recommended",
                    "car rental is recommended",
                    "i recommend a car rental",
                    "car rental would be beneficial",
                    "would benefit from renting",
                    "car would be helpful"
                ]
                
                # Clear negative indicators
                negative_indicators = [
                    "not recommend renting",
                    "don't recommend renting",
                    "do not recommend renting",
                    "wouldn't recommend renting",
                    "would not recommend renting",
                    "shouldn't rent a car",
                    "should not rent a car",
                    "no need to rent",
                    "no need for a car",
                    "renting a car is not recommended",
                    "car rental is not recommended",
                    "i do not recommend a car rental",
                    "without renting",
                    "without a car"
                ]
                
                for indicator in positive_indicators:
                    if indicator in car_rental_section:
                        print(f"[DEBUG] Found positive indicator '{indicator}' - should rent car: TRUE")
                        return True
                        
                for indicator in negative_indicators:
                    if indicator in car_rental_section:
                        print(f"[DEBUG] Found negative indicator '{indicator}' - should rent car: FALSE")
                        return False
            
            # Look for recommendation in the full text if section wasn't found or conclusive
            if "not recommend renting a car" in recommendation_text or "do not recommend renting a car" in recommendation_text:
                print("[DEBUG] Found negative recommendation in full text - should rent car: FALSE")
                return False
            elif "recommend renting a car" in recommendation_text:
                print("[DEBUG] Found positive recommendation in full text - should rent car: TRUE")
                return True
            
            print(f"[CRITICAL] Could not determine car rental recommendation from text. Full original text: {original_text}")
            print(f"[DEBUG] Defaulting to FALSE for safety")
            return False
            
        except Exception as e:
            print(f"[ERROR] Error analyzing recommendation: {e}")
            return False
    
    def get_ai_recommendation(self, user_prefs, selected_spots, total_days, user_name=None) -> Generator:
        """Get AI recommendation about the overall trip plan"""
        print(f"[DEBUG] Received user_prefs in get_ai_recommendation: {user_prefs}")  # Debug log
        
        # Create prompt for the LLM
        name = user_name if user_name else "Traveler"
        spot_names = [spot["name"] for spot in selected_spots]
        days = total_days

        # The traveller's own choice wins over any model recommendation.  Accept
        # both booleans and the yes/no strings the chat/UI layer can produce.
        explicit_decision = user_prefs.get("rental_choice")
        if isinstance(explicit_decision, str):
            explicit_decision = explicit_decision.strip().lower()
            if explicit_decision in ("yes", "true", "1", "y"):
                explicit_decision = True
            elif explicit_decision in ("no", "false", "0", "n"):
                explicit_decision = False
            else:
                explicit_decision = None
        
        # Extract specific preferences
        people = user_prefs.get('people', 1)
        kids_val = user_prefs.get('kids', 0)
        if isinstance(kids_val, str):
            has_kids = kids_val.lower() in ('yes', 'true')
        else:
            has_kids = bool(kids_val)
        health_prefs = user_prefs.get('health', 'good')
        budget = user_prefs.get('budget', 'medium')
        hobbies = user_prefs.get('hobbies', '')
        name = user_prefs.get('name', name)
        
        # Extract specific requirements if they exist
        specific_requirements = user_prefs.get('specificRequirements', '')
        specific_requirements_section = ""
        if specific_requirements:
            specific_requirements_section = f"""
        Special Requirements/Constraints:
        {specific_requirements}
            """
        
        is_international = user_prefs.get('is_international', False)
        destination_city = user_prefs.get('city', 'the destination')
        
        if is_international:
            prompt = f"""
        You are providing travel recommendations to {name} who is planning a {days}-day trip to {destination_city} with these attractions:
        {', '.join(spot_names)}
        
        Their specific preferences are:
        - Number of people: {people}
        - Traveling with children: {'Yes' if has_kids else 'No'}
        - Health/Dietary requirements: {health_prefs}
        - Budget level: {budget}
        - Interests/Hobbies: {hobbies}
        {specific_requirements_section}
        
        Based on these preferences, provide recommendations in the following format:

        ## Car Rental Recommendation:
        [car_rental:YES/NO] (Use YES or NO only)

        Provide your explanation for the car rental recommendation. In the context of {destination_city}, consider local transport options.
        Be decisive and clear about YES or NO.

        ## Trip Adjustments:
        Provide 2-3 specific suggestions to make the trip to {destination_city} more enjoyable based on the traveler's preferences.
        Mention things like best time to visit sites, local cuisine to try, etc.
        
        IMPORTANT: 
        1. Always use SECOND PERSON - speak directly TO {name}.
        2. Include the [car_rental:YES] or [car_rental:NO] marker (it will be hidden from user).
        3. Be clear: "I recommend you rent a car" or "I do not recommend you rent a car".
        4. Don't mention car rental if you don't recommend it.
        5. Keep language friendly and helpful.
        """
        else:
            prompt = f"""
        You are providing India travel recommendations to {name} who is planning a {days}-day trip within India with these attractions:
        {', '.join(spot_names)}
        
        Their specific preferences are:
        - Number of people: {people}
        - Traveling with children: {'Yes' if has_kids else 'No'}
        - Health/Dietary requirements: {health_prefs}
        - Budget level: {budget}
        - Interests/Hobbies: {hobbies}
        {specific_requirements_section}
        
        Based on these preferences, provide recommendations in the following format:

        ## Car Rental Recommendation:
        [car_rental:YES/NO] (Use YES or NO only)

        Provide your explanation for the car rental recommendation. In India context, consider:
        - Auto-rickshaws, taxis, and metro are good for city travel (often no car needed)
        - Self-drive rentals work well for hill stations (Manali, Shimla, Coorg, Ooty)
        - For rural/remote areas or multi-city road trips, car rental is very useful
        - Budget travelers may prefer trains/buses
        Be decisive and clear about YES or NO.

        ## Trip Adjustments:
        Provide 2-3 India-specific suggestions to make the trip more enjoyable based on the traveler's preferences.
        Mention things like best time to visit sites, local cuisine to try, dress code for religious places, etc.
        
        IMPORTANT: 
        1. Always use SECOND PERSON - speak directly TO {name}.
        2. Include the [car_rental:YES] or [car_rental:NO] marker (it will be hidden from user).
        3. Be clear: "I recommend you rent a car" or "I do not recommend you rent a car".
        4. Don't mention car rental if you don't recommend it.
        5. Keep language friendly, helpful and India-focused.
        """

        if explicit_decision is not None:
            decision_word = "YES" if explicit_decision else "NO"
            prompt += (
                f"\n\nThe traveller has already explicitly chosen rental_choice={decision_word}. "
                f"This is authoritative: use [car_rental:{decision_word}] and do not argue "
                "against their choice. You may still explain practical considerations."
            )

        messages = [
            SystemMessage(content=f"You are a travel advisor helping {name} plan their trip. Address them directly using second person (you/your). Format your response as requested with the car rental marker."),
            HumanMessage(content=prompt)
        ]
        
        try:
            # Get the full response first to analyze it
            response = self.model.invoke(messages)
            recommendation_text = response.content
            if isinstance(recommendation_text, list):
                recommendation_text = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in recommendation_text)
            
            # Print the raw recommendation for debugging
            print(f"[DEBUG] Raw AI recommendation text: {recommendation_text[:200]}...")
            
            # Analyze the recommendation to determine if car rental is recommended
            should_rent_car = self.extract_rental_recommendation(
                recommendation_text, explicit_decision=explicit_decision
            )
            
            # Update the user_prefs with the new should_rent_car value
            user_prefs['should_rent_car'] = should_rent_car
            
            print(f"[DEBUG] AI recommendation analyzed - should_rent_car: {should_rent_car}")
            
            # Remove the [car_rental:YES/NO] markers from the text before displaying to the user
            cleaned_text = re.sub(r'\[car_rental:(yes|no)\]', '', recommendation_text, flags=re.IGNORECASE)
            
            # Generate message chunks with the cleaned content for streaming
            def generate_chunks():
                yield AIMessage(content=cleaned_text)
                
            return generate_chunks()
        except Exception as e:
            print(f"Error in get_ai_recommendation: {e}")
            return None