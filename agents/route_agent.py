import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import itertools
import math
import json
from datetime import datetime, timedelta
import networkx as nx
from utils import ask_openai, extract_number
import re
import sys
import os

# Add the parent directory to sys.path to allow imports from services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.information_agent import InformationAgent

class RouteAgent:
    def __init__(self, api_key=None):
        """Initialize RouteAgent with optional API key for distance calculations"""
        self.api_key = api_key
        self.distances_cache = {}
        self.info_agent = None
        try:
            self.info_agent = InformationAgent()
        except Exception as e:
            print(f"Error initializing InformationAgent in RouteAgent: {e}")
    
    def optimize_daily_route(self, attractions_for_day):
        """
        Optimize the order of attractions for a single day using the InformationAgent's plan_with_waypoints.
        
        Args:
            attractions_for_day: List of attraction objects with location data
            
        Returns:
            List of the same attractions in optimal travel order
        """
        if not attractions_for_day or len(attractions_for_day) <= 1:
            return attractions_for_day  # No optimization needed for 0 or 1 attraction
        
        # Check if we can use the InformationAgent
        if not self.info_agent:
            print("InformationAgent not available. Using fallback TSP solution.")
            return self.get_optimal_route(attractions_for_day)
            
        try:
            # Extract first attraction as starting point
            origin = attractions_for_day[0]
            
            # Extract last attraction as destination (complete the loop back to start for simplicity)
            destination = attractions_for_day[0]
            
            # The rest are waypoints
            waypoints = []
            for attraction in attractions_for_day[1:]:
                if "location" in attraction and "lat" in attraction["location"] and "lng" in attraction["location"]:
                    waypoint_location = f"{attraction['location']['lat']},{attraction['location']['lng']}"
                    waypoints.append(waypoint_location)
                else:
                    print(f"Warning: Attraction {attraction.get('name', 'unknown')} missing location data")

            # Prepare origin and destination strings
            if "location" in origin and "lat" in origin["location"] and "lng" in origin["location"]:
                origin_location = f"{origin['location']['lat']},{origin['location']['lng']}"
                destination_location = origin_location  # Loop back to start
            else:
                print(f"Warning: Origin attraction {origin.get('name', 'unknown')} missing location data")
                return attractions_for_day  # Can't optimize without location data

            # Call plan_with_waypoints
            optimized_route_data = self.info_agent.plan_with_waypoints(
                origin=origin_location,
                destination=destination_location,
                waypoints=waypoints,
                mode='driving'
            )
            
            if not optimized_route_data:
                print("Failed to get optimized route. Using fallback TSP solution.")
                return self.get_optimal_route(attractions_for_day)
            
            # Extract the optimized waypoint order
            waypoint_indices = optimized_route_data.get('waypoint_original_indices', [])
            
            if not waypoint_indices and len(waypoints) > 0:
                waypoint_indices = list(range(len(waypoints)))
            
            # Create the optimized list of attractions
            optimized_attractions = [origin]  # Start with origin
            
            # Add waypoints in optimized order
            for idx in waypoint_indices:
                optimized_attractions.append(attractions_for_day[idx + 1])  # +1 because we excluded origin
                
            return optimized_attractions
        
        except Exception as e:
            print(f"Error optimizing daily route: {e}")
            # Fallback to internal TSP solver
            return self.get_optimal_route(attractions_for_day)
    
    def get_optimal_route(self, spots, start_point=None):
        """Calculate optimal route between selected attractions"""
        print(f"[DEBUG] Getting optimal route for spots: {spots}")
        
        if not spots or len(spots) <= 1:
            print(f"[DEBUG] Not enough spots to calculate route. Returning spots as is: {spots}")
            return spots
        
        # Get distance matrix
        distance_matrix = self._get_distance_matrix(spots)
        print(f"[DEBUG] Distance matrix calculated: {distance_matrix}")
        
        # Solve TSP (Traveling Salesman Problem)
        if len(spots) <= 5:
            # For small number of points, brute force is fine
            route = self._solve_tsp_brute_force(spots, distance_matrix)
            print(f"[DEBUG] Brute force TSP solution: {route}")
            return route
        else:
            # For larger problems, use approximate method
            route = self._solve_tsp_approximate(spots, distance_matrix)
            print(f"[DEBUG] Approximate TSP solution: {route}")
            return route
    
    def _get_distance_matrix(self, spots):
        """Get distance matrix between all pairs of spots using Haversine formula."""
        n = len(spots)
        matrix = [[0 for _ in range(n)] for _ in range(n)]
        
        # Fill the matrix with distances
        for i in range(n):
            for j in range(i+1, n):
                # Get distance between spot i and spot j
                distance = self._calculate_distance(spots[i], spots[j])
                matrix[i][j] = distance
                matrix[j][i] = distance  # Symmetric
        
        return matrix
    
    def _calculate_distance(self, spot1, spot2):
        """Calculate distance between two spots using coordinates"""
        # Check if we have location data
        if not spot1.get("location") or not spot2.get("location"):
            return 1  # Default distance if no location data
        
        # Check cache first
        cache_key = f"{spot1['id']}_{spot2['id']}"
        if cache_key in self.distances_cache:
            return self.distances_cache[cache_key]
        
        lat1, lon1 = spot1["location"].get("lat", 0), spot1["location"].get("lng", 0)
        lat2, lon2 = spot2["location"].get("lat", 0), spot2["location"].get("lng", 0)
        
        R = 6371  # Earth radius in km
        
        # Convert coordinates to radians
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        
        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        distance = R * c
        
        # Cache the result
        self.distances_cache[cache_key] = distance
        
        return distance
    
    def _solve_tsp_brute_force(self, spots, distance_matrix):
        """Solve TSP by trying all permutations (only for small problems)"""
        n = len(spots)
        best_distance = float('inf')
        best_order = list(range(n))
        
        # Try all permutations
        for perm in itertools.permutations(range(n)):
            distance = sum(distance_matrix[perm[i]][perm[i+1]] for i in range(n-1))
            
            if distance < best_distance:
                best_distance = distance
                best_order = perm
        
        # Return spots in optimal order
        return [spots[i] for i in best_order]
    
    def _solve_tsp_approximate(self, spots, distance_matrix):
        """Solve TSP using approximation algorithm (Christofides or nearest neighbor)"""
        # Create a complete graph
        G = nx.Graph()
        n = len(spots)
        
        # Add nodes and edges with distances
        for i in range(n):
            G.add_node(i)
            for j in range(i+1, n):
                G.add_edge(i, j, weight=distance_matrix[i][j])
        
        # Find approximate TSP tour
        tour = nx.approximation.traveling_salesman_problem(G, cycle=True)
        
        # Remove the last node (it's the same as the first to complete the cycle)
        tour = tour[:-1]
        
        # Return spots in calculated order
        return [spots[i] for i in tour]
    
    def format_daily_plan_to_itinerary(self, daily_plan_name_dict, all_spots_object_map, start_date_str, retrieved_knowledge=None):
        """Generate daily itinerary based on a pre-defined daily plan of attraction names."""
        itinerary = []
        try:
            current_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        except ValueError:
            print(f"[ERROR] Invalid start_date_str format: {start_date_str}. Expected YYYY-MM-DD.")
            # Fallback to today if date is invalid, or handle error as preferred
            current_date = datetime.now()
            print(f"[WARN] Using current date {current_date.strftime('%Y-%m-%d')} as fallback.")

        # Sort day keys numerically (e.g., "day1", "day2", ...)
        day_keys = []
        for key in daily_plan_name_dict.keys():
            if key.lower().startswith("day"):
                match = re.search(r'\d+', key)
                if match:
                    day_keys.append(key)
                    
        sorted_day_keys = sorted(
            day_keys,
            key=lambda x: int(re.search(r'\d+', x).group())
        )

        for day_key in sorted_day_keys:
            day_number = int(re.search(r'\d+', day_key).group())
            spot_names_for_day = daily_plan_name_dict.get(day_key, [])
            
            current_day_spot_objects_raw = []
            for name in spot_names_for_day:
                if name in all_spots_object_map:
                    current_day_spot_objects_raw.append(all_spots_object_map[name])
                else:
                    print(f"[WARN] Attraction name '{name}' from daily plan (day {day_number}) not found in all_spots_object_map.")
            
            # Optimize the route for this day's attractions
            if current_day_spot_objects_raw and len(current_day_spot_objects_raw) > 1:
                print(f"Optimizing route for day {day_number} with {len(current_day_spot_objects_raw)} attractions...")
                optimized_day_attractions = self.optimize_daily_route(current_day_spot_objects_raw)
                print(f"Route optimization complete for day {day_number}")
                current_day_spot_objects_raw = optimized_day_attractions
            
            current_day_spots_timed = []
            # Use 8 hours as a guideline for sequential timing within the day
            # The LLM was prompted to consider an 8-hour day, so the sum of durations should ideally be around that.
            start_offset_hours = 0 # Hours from 9 AM, e.g., 0 means 9 AM

            for spot_obj in current_day_spot_objects_raw:
                # Use estimated duration from the object, but this could be adjusted using retrieved_knowledge
                # e.g., if retrieved_knowledge mentions "Taj Mahal typically takes 3 hours"
                spot_duration = spot_obj.get("estimated_duration", 2) # Default to 2 hours if not specified
                
                spot_with_time = spot_obj.copy()
                
                # Calculate start and end times for the activity (e.g., from 9:00)
                activity_start_hour = 9 + start_offset_hours
                activity_end_hour = activity_start_hour + spot_duration
                
                spot_with_time["start_time"] = f"{int(activity_start_hour):02d}:00"
                spot_with_time["end_time"] = f"{int(activity_end_hour):02d}:00"
                current_day_spots_timed.append(spot_with_time)
                
                start_offset_hours += spot_duration # Next spot starts after this one

            itinerary.append({
                "day": day_number,
                "date": current_date.strftime("%Y-%m-%d"),
                "spots": current_day_spots_timed
            })
            current_date += timedelta(days=1)
        
        return itinerary

    def estimate_budget(self, spots, user_prefs, should_rent_car=False, car_info=None, fuel_price=None, transit_options=None):
        """Estimate budget supporting numerical/strict/flexible budgets and infeasibility detection.

        Budget Components:
          - accommodation    : acc_ppd (per room/night)  × rooms × num_days
          - food             : food_ppd (per person/day) × num_people × num_days
          - transport        : trans_ppd (per person/day) × num_people × num_days
                               (zeroed out when car is rented — fuel covers local travel instead)
          - attractions      : sum of per-spot entry fees × num_people
          - car_rental       : INR daily rate × num_days  (only when should_rent_car=True)
          - fuel_cost        : local_km ÷ 100 × consumption_L_per_100km × ₹110/L  (car rental only)
          - intercity_transport: one_way_price_inr × 2 × num_people  (round-trip, applied ONCE)
          - miscellaneous    : 10% of subtotal
        """
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import HumanMessage
        import json, re as _re

        # 1. Resolve budget level and optional numerical target
        raw_budget        = user_prefs.get("budget", "medium")
        budget_amount     = user_prefs.get("budget_amount")
        budget_strictness = user_prefs.get("budget_strictness")

        if budget_amount is not None:
            try:
                budget_amount = int(str(budget_amount).replace(",", "").replace("₹", "").strip())
            except (ValueError, TypeError):
                budget_amount = None

        if budget_amount is not None:
            if budget_amount <= 30000:
                budget_level = "low"
            elif budget_amount <= 80000:
                budget_level = "medium"
            else:
                budget_level = "high"
        elif isinstance(raw_budget, str):
            bl = raw_budget.lower()
            if any(k in bl for k in ("low", "cheap", "backpacker", "budget")):
                budget_level = "low"
            elif any(k in bl for k in ("high", "luxury", "expensive", "premium")):
                budget_level = "high"
            else:
                budget_level = "medium"
        else:
            budget_level = "medium"

        num_people = max(1, int(user_prefs.get("people", 1)))
        num_days   = max(1, int(user_prefs.get("days", 1)))
        rooms      = max(1, (num_people + 1) // 2)
        city_name  = user_prefs.get("city", "India")
        if not city_name or city_name.lower() == "any":
            city_name = spots[0].get("city", "India") if spots else "India"

        # 2. Dynamic per-day costs via LLM
        # Realistic INR bounds per budget level — used to clamp LLM output and prevent inflation.
        # accommodation: per ROOM per NIGHT
        # food:          per PERSON per DAY  (all meals)
        # transport:     per PERSON per DAY  (local city travel: auto/metro/cab within destination)
        _cost_caps = {
            "low":    {"acc_min": 500,  "acc_max": 1200,  "food_min": 250,  "food_max": 500,  "trans_min": 100, "trans_max": 300},
            "medium": {"acc_min": 1200, "acc_max": 4000,  "food_min": 500,  "food_max": 1200, "trans_min": 250, "trans_max": 700},
            "high":   {"acc_min": 4000, "acc_max": 12000, "food_min": 1200, "food_max": 3500, "trans_min": 600, "trans_max": 2000},
        }

        def _clamp(val, lo, hi):
            return max(lo, min(hi, val))

        def _get_dynamic_costs(blevel, city):
            """Ask the LLM for per-day costs; clamp results to realistic INR ranges."""
            try:
                llm = ChatGoogleGenerativeAI(model="gemini-flash-lite-latest", temperature=0.2)
                caps = _cost_caps.get(blevel, _cost_caps["medium"])
                prompt = (
                    f"Estimate realistic daily travel costs in INR for a {blevel} budget trip to {city}, India.\n"
                    "Return EXACTLY three values:\n"
                    f"  'accommodation': cost per ROOM per NIGHT (not per person). "
                    f"Typical range for {blevel} budget in {city}: INR {caps['acc_min']}–{caps['acc_max']}.\n"
                    f"  'food': cost per PERSON per DAY (all meals: breakfast+lunch+dinner). "
                    f"Typical range: INR {caps['food_min']}–{caps['food_max']}.\n"
                    f"  'transport': local city transport cost per PERSON per DAY "
                    "(auto-rickshaw/metro/cab rides within the destination city only — "
                    "NOT intercity or origin-to-destination travel). "
                    f"Typical range: INR {caps['trans_min']}–{caps['trans_max']}.\n"
                    'Return ONLY a JSON object (no markdown): {"accommodation": N, "food": N, "transport": N}'
                )
                resp = llm.invoke([HumanMessage(content=prompt)])
                c = resp.content
                if isinstance(c, list):
                    c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
                c = c.strip()
                m2 = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", c, _re.DOTALL | _re.IGNORECASE)
                c = m2.group(1) if m2 else c[c.find("{"):c.rfind("}")+1]
                d = json.loads(c)
                raw_acc   = float(d.get("accommodation", caps["acc_min"]))
                raw_food  = float(d.get("food",          caps["food_min"]))
                raw_trans = float(d.get("transport",     caps["trans_min"]))
                # Clamp to realistic ranges so the LLM cannot produce absurd values
                return (
                    _clamp(raw_acc,   caps["acc_min"],   caps["acc_max"]),
                    _clamp(raw_food,  caps["food_min"],  caps["food_max"]),
                    _clamp(raw_trans, caps["trans_min"], caps["trans_max"]),
                )
            except Exception as ex:
                print(f"[WARN] Dynamic cost estimation failed ({ex}), using static fallback")
                static = {
                    "low":    (_cost_caps["low"]["acc_min"],    _cost_caps["low"]["food_min"],    _cost_caps["low"]["trans_min"]),
                    "medium": (_cost_caps["medium"]["acc_min"], _cost_caps["medium"]["food_min"], _cost_caps["medium"]["trans_min"]),
                    "high":   (_cost_caps["high"]["acc_min"],   _cost_caps["high"]["food_min"],   _cost_caps["high"]["trans_min"]),
                }
                return static.get(blevel, static["medium"])

        acc_ppd, food_ppd, trans_ppd = _get_dynamic_costs(budget_level, city_name)

        def _compute_totals(a, f, t):
            """
            a = per-room-per-night  → multiply by rooms × num_days
            f = per-person-per-day  → multiply by num_people × num_days
            t = per-person-per-day  → multiply by num_people × num_days
            """
            return a * rooms * num_days, f * num_people * num_days, t * num_people * num_days

        total_acc, total_food, total_trans = _compute_totals(acc_ppd, food_ppd, trans_ppd)

        # 3. Attraction entry-fee costs
        cost_map = {0: 0, 1: 150, 2: 350, 3: 600, 4: 1000}
        attraction_cost = sum(cost_map.get(s.get("price_level"), 300) * num_people for s in spots)

        # 4. Car rental and fuel costs
        # FIX: All prices are in INR. We no longer use the USD-based fuel_price parameter
        car_rental_cost = 0
        fuel_cost = 0

        if should_rent_car:
            # ── Car Rental ──────────────────────────────────────────────────────────
            # Realistic Indian market daily rates in INR by budget level
            _rental_daily_inr = {"low": 1200, "medium": 2200, "high": 4500}
            _rental_daily_default = _rental_daily_inr.get(budget_level, 2200)

            if car_info and len(car_info) > 0:
                _usd_to_inr = 84.0
                ai_response = ask_openai(
                    prompt=(
                        f"Car info: {car_info}, budget: {budget_level}. "
                        "Select most suitable car. Return only the index number."
                    )
                )
                idx = 0
                if ai_response and "answer" in ai_response:
                    extracted = extract_number(ai_response.get("answer", ""))
                    if extracted is not None:
                        recommend_car = int(extracted)
                        idx = recommend_car - 1 if recommend_car in range(1, len(car_info) + 1) else 0

                raw_price = car_info[idx].get("price", 0)
                currency  = str(car_info[idx].get("currency", "USD")).upper().strip()

                if raw_price and float(raw_price) > 0:
                    price_inr = float(raw_price)
                    if currency not in ("INR", "RS", "RS.", "₹"):
                        price_inr = float(raw_price) * _usd_to_inr
                    if price_inr < 500:
                        car_rental_cost = price_inr * num_days
                    else:
                        car_rental_cost = price_inr
                else:
                    car_rental_cost = _rental_daily_default * num_days
            else:
                car_rental_cost = _rental_daily_default * num_days

            # ── Fuel Cost ───────────────────────────────────────────────────────────
            _petrol_inr_per_litre    = 110.0
            _consumption_l_per_100km = {"low": 6.0, "medium": 8.0, "high": 12.0}.get(budget_level, 8.0)

            route = self.get_optimal_route(spots)
            total_local_dist_km = 0.0
            if route and len(route) > 1:
                for i in range(len(route) - 1):
                    total_local_dist_km += self._calculate_distance(route[i], route[i + 1])

            _base_daily_km = 40.0
            total_local_dist_km = max(total_local_dist_km, _base_daily_km * num_days)

            litres_consumed = total_local_dist_km * _consumption_l_per_100km / 100.0
            fuel_cost = round(litres_consumed * _petrol_inr_per_litre, 2)
            total_trans = 0

        # 4.5  Intercity transport
        intercity_transport_cost = 0
        if transit_options:
            recommended_mode = transit_options.get("recommended_mode", "").lower()
            options = []
            if "flight" in recommended_mode and transit_options.get("flights"):
                options = transit_options["flights"]
            elif "train" in recommended_mode and transit_options.get("trains"):
                options = transit_options["trains"]
            elif "bus" in recommended_mode and transit_options.get("buses"):
                options = transit_options["buses"]

            if not options:
                if budget_level == "low":
                    options = transit_options.get("trains", []) + transit_options.get("buses", []) + transit_options.get("flights", [])
                elif budget_level == "medium":
                    options = transit_options.get("trains", []) + transit_options.get("flights", []) + transit_options.get("buses", [])
                else:
                    options = transit_options.get("flights", []) + transit_options.get("trains", []) + transit_options.get("buses", [])

            if options:
                first_option = options[0]
                one_way_inr  = float(first_option.get("price_inr", 0))
                intercity_transport_cost = one_way_inr * 2 * num_people

        total = (total_acc + total_food + total_trans + attraction_cost
                 + car_rental_cost + fuel_cost + intercity_transport_cost)

        # 5. Budget optimization + infeasibility check
        budget_warning    = None
        budget_infeasible = False

        if budget_amount is not None:
            _min_caps = _cost_caps["low"]
            min_viable = (
                _min_caps["acc_min"]   * rooms      * num_days +
                _min_caps["food_min"]  * num_people * num_days +
                _min_caps["trans_min"] * num_people * num_days +
                intercity_transport_cost
            )

            if budget_amount < min_viable * 0.7:
                budget_infeasible = True
                budget_warning = (
                    f"Your budget of ₹{budget_amount:,} appears too low for a {num_days}-day trip "
                    f"for {num_people} person(s). Minimum realistic cost: ₹{int(min_viable):,}. "
                    "Consider increasing your budget, reducing trip duration, or choosing fewer paid attractions."
                )

            if not budget_infeasible and total > budget_amount:
                sa, sf, st = acc_ppd, food_ppd, trans_ppd
                for iteration in range(5):
                    sa = max(sa * 0.80, _cost_caps["low"]["acc_min"])
                    sf = max(sf * 0.85, _cost_caps["low"]["food_min"])
                    st = max(st * 0.85, _cost_caps["low"]["trans_min"])
                    new_acc, new_food, new_trans = _compute_totals(sa, sf, st)
                    new_trans_used = 0 if should_rent_car else new_trans
                    total = (new_acc + new_food + new_trans_used + attraction_cost
                             + car_rental_cost + fuel_cost + intercity_transport_cost)
                    if total <= budget_amount:
                        total_acc   = new_acc
                        total_food  = new_food
                        total_trans = new_trans_used
                        break

                if total > budget_amount:
                    if budget_strictness == "strict":
                        budget_warning = (
                            f"Even after optimization the estimated cost (₹{int(total):,}) slightly exceeds "
                            f"your strict budget of ₹{budget_amount:,}. "
                            "Consider reducing travel days or choosing fewer paid attractions."
                        )
                    else:
                        budget_warning = (
                            f"Estimated trip cost (₹{int(total):,}) is slightly above your budget of "
                            f"₹{budget_amount:,}. We've minimized costs where possible."
                        )
                else:
                    budget_warning = f"Itinerary optimized to fit within your budget of ₹{budget_amount:,}."

        # 6. Miscellaneous (10% of subtotal) and final total
        miscellaneous_cost = round(total * 0.10, 2)
        total += miscellaneous_cost

        remaining = round(budget_amount - total, 2) if budget_amount is not None else None

        return {
            "total":               round(total, 2),
            "accommodation":       round(total_acc, 2),
            "food":                round(total_food, 2),
            "transport":           round(total_trans, 2),
            "intercity_transport": round(intercity_transport_cost, 2),
            "miscellaneous":       miscellaneous_cost,
            "attractions":         round(attraction_cost, 2),
            "car_rental":          round(car_rental_cost, 2),
            "fuel_cost":           round(fuel_cost, 2),
            "budget_level":        budget_level,
            "budget_amount":       budget_amount,
            "budget_strictness":   budget_strictness,
            "budget_warning":      budget_warning,
            "budget_infeasible":   budget_infeasible,
            "remaining":           remaining,
            "rooms":               rooms,
            "days":                num_days,
            "people":              num_people,
        }
