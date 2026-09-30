import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import itertools
import math
import json
from datetime import datetime, timedelta
import networkx as nx
import re
import sys
import os

# Add the parent directory to sys.path to allow imports from services
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.information_agent import InformationAgent


class RouteAgent:
    """
    Agent responsible exclusively for route planning and itinerary formatting.

    Responsibilities
    ----------------
    - Optimising the daily visit order of attractions using the InformationAgent
      (Google Maps waypoints) or a fallback TSP solver.
    - Computing the distance matrix between spots via the Haversine formula.
    - Converting a day-keyed attraction name plan into a timed itinerary.

    This agent does NOT perform any budget calculations.  All budget-related
    logic has been moved to BudgetAgent.
    """

    def __init__(self, api_key=None):
        """Initialise RouteAgent with optional API key for distance calculations."""
        self.api_key = api_key
        self.distances_cache = {}
        self.info_agent = None
        try:
            self.info_agent = InformationAgent()
        except Exception as e:
            print(f"Error initialising InformationAgent in RouteAgent: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Route optimisation
    # ─────────────────────────────────────────────────────────────────────────

    def optimize_daily_route(self, attractions_for_day):
        """
        Optimise the order of attractions for a single day using the
        InformationAgent's plan_with_waypoints.

        Args:
            attractions_for_day: List of attraction objects with location data

        Returns:
            List of the same attractions in optimal travel order
        """
        if not attractions_for_day or len(attractions_for_day) <= 1:
            return attractions_for_day  # No optimisation needed for 0 or 1 attraction

        # Check if we can use the InformationAgent
        if not self.info_agent:
            print("InformationAgent not available. Using fallback TSP solution.")
            return self.get_optimal_route(attractions_for_day)

        try:
            # Extract first attraction as starting point
            origin = attractions_for_day[0]

            # Extract last attraction as destination (complete the loop back to start)
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
                return attractions_for_day  # Can't optimise without location data

            # Call plan_with_waypoints
            optimized_route_data = self.info_agent.plan_with_waypoints(
                origin=origin_location,
                destination=destination_location,
                waypoints=waypoints,
                mode="driving",
            )

            if not optimized_route_data:
                print("Failed to get optimised route. Using fallback TSP solution.")
                return self.get_optimal_route(attractions_for_day)

            # Extract the optimised waypoint order
            waypoint_indices = optimized_route_data.get("waypoint_original_indices", [])

            if not waypoint_indices and len(waypoints) > 0:
                waypoint_indices = list(range(len(waypoints)))

            # Create the optimised list of attractions
            optimized_attractions = [origin]  # Start with origin

            # Add waypoints in optimised order
            for idx in waypoint_indices:
                optimized_attractions.append(attractions_for_day[idx + 1])  # +1 because origin excluded

            return optimized_attractions

        except Exception as e:
            print(f"Error optimising daily route: {e}")
            # Fallback to internal TSP solver
            return self.get_optimal_route(attractions_for_day)

    def get_optimal_route(self, spots, start_point=None):
        """Calculate optimal route between selected attractions."""
        print(f"[DEBUG] Getting optimal route for spots: {spots}")

        if not spots or len(spots) <= 1:
            print(f"[DEBUG] Not enough spots to calculate route. Returning spots as is: {spots}")
            return spots

        # Get distance matrix
        distance_matrix = self._get_distance_matrix(spots)
        print(f"[DEBUG] Distance matrix calculated: {distance_matrix}")

        # Solve TSP (Travelling Salesman Problem)
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

    # ─────────────────────────────────────────────────────────────────────────
    # Distance calculations
    # ─────────────────────────────────────────────────────────────────────────

    def _get_distance_matrix(self, spots):
        """Get distance matrix between all pairs of spots using Haversine formula."""
        n = len(spots)
        matrix = [[0 for _ in range(n)] for _ in range(n)]

        # Fill the matrix with distances
        for i in range(n):
            for j in range(i + 1, n):
                # Get distance between spot i and spot j
                distance = self._calculate_distance(spots[i], spots[j])
                matrix[i][j] = distance
                matrix[j][i] = distance  # Symmetric

        return matrix

    def _calculate_distance(self, spot1, spot2):
        """Calculate distance between two spots using coordinates (Haversine formula)."""
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
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        distance = R * c

        # Cache the result
        self.distances_cache[cache_key] = distance

        return distance

    # ─────────────────────────────────────────────────────────────────────────
    # Chronological sort helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _time_to_minutes(time_str):
        """
        Convert a time string to minutes from midnight for numeric comparison.

        Handles formats:
            "09:00"  → 540      (24-hour, zero-padded)
            "9:00"   → 540      (24-hour, no pad)
            "9:30"   → 570
            "2:00 PM"→ 840
            "10:00 AM"→ 600
            "12:00 PM"→ 720
            "12:00 AM"→ 0

        Returns 9999 (sorts to end) for any unparseable string.
        """
        if not time_str or not isinstance(time_str, str):
            return 9999
        t = time_str.strip().upper()
        try:
            # AM/PM form
            if 'AM' in t or 'PM' in t:
                for fmt in ("%I:%M %p", "%I:%M%p"):
                    try:
                        dt = datetime.strptime(t, fmt)
                        return dt.hour * 60 + dt.minute
                    except ValueError:
                        continue
                return 9999
            # 24-hour form  HH:MM or H:MM
            parts = t.split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            pass
        return 9999

    def _sort_spots_by_time(self, spots):
        """
        Return *spots* sorted ascending by start_time (minutes from midnight).

        This is the single, authoritative sort applied to every day's spot list
        before the itinerary is finalised.  Time-window order (chronological)
        always takes priority over TSP geographic order; TSP only decides the
        visit sequence when multiple spots share the same time slot.
        """
        return sorted(spots, key=lambda s: self._time_to_minutes(s.get("start_time", "")))


    # ─────────────────────────────────────────────────────────────────────────
    # TSP solvers
    # ─────────────────────────────────────────────────────────────────────────

    def _solve_tsp_brute_force(self, spots, distance_matrix):
        """Solve TSP by trying all permutations (only for small problems)."""
        n = len(spots)
        best_distance = float("inf")
        best_order = list(range(n))

        # Try all permutations
        for perm in itertools.permutations(range(n)):
            distance = sum(distance_matrix[perm[i]][perm[i + 1]] for i in range(n - 1))

            if distance < best_distance:
                best_distance = distance
                best_order = perm

        # Return spots in optimal order
        return [spots[i] for i in best_order]

    def _solve_tsp_approximate(self, spots, distance_matrix):
        """Solve TSP using approximation algorithm (nearest-neighbour / Christofides)."""
        # Create a complete graph
        G = nx.Graph()
        n = len(spots)

        # Add nodes and edges with distances
        for i in range(n):
            G.add_node(i)
            for j in range(i + 1, n):
                G.add_edge(i, j, weight=distance_matrix[i][j])

        # Find approximate TSP tour
        tour = nx.approximation.traveling_salesman_problem(G, cycle=True)

        # Remove the last node (it's the same as the first to complete the cycle)
        tour = tour[:-1]

        # Return spots in calculated order
        return [spots[i] for i in tour]

    # ─────────────────────────────────────────────────────────────────────────
    # Itinerary formatting
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_opening_hours(self, opening_hours_str):
        # Very simple check for common patterns, fallback to 09:00-18:00
        if not opening_hours_str: return (9, 18)
        if "24/7" in opening_hours_str: return (0, 24)
        return (9, 18) # Stub for advanced parsing

    def format_daily_plan_to_itinerary(self, daily_plan_name_dict, all_spots_object_map, start_date_str, retrieved_knowledge=None, accommodation=None):
        """Generate daily itinerary with TSP routing, meal breaks, and realistic timing."""
        itinerary = []
        try:
            current_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        except ValueError:
            print(f"[ERROR] Invalid start_date_str format: {start_date_str}. Expected YYYY-MM-DD.")
            current_date = datetime.now()

        # Sort day keys numerically
        day_keys = [k for k in daily_plan_name_dict.keys() if k.lower().startswith("day") and re.search(r"\d+", k)]
        sorted_day_keys = sorted(day_keys, key=lambda x: int(re.search(r"\d+", x).group()))

        for day_key in sorted_day_keys:
            day_number = int(re.search(r"\d+", day_key).group())
            spot_names_for_day = daily_plan_name_dict.get(day_key, [])

            current_day_spot_objects_raw = []
            for name in spot_names_for_day:
                if name in all_spots_object_map:
                    current_day_spot_objects_raw.append(all_spots_object_map[name])
                else:
                    current_day_spot_objects_raw.append({
                        "id": f"semantic_{name.replace(' ', '_').lower()}",
                        "name": name,
                        "description": "Leisure or semantic activity suggested by AI.",
                        "estimated_duration": 2,
                        "location": None
                    })

            # TSP Optimization
            if current_day_spot_objects_raw and len(current_day_spot_objects_raw) > 1:
                optimized_day_attractions = self.optimize_daily_route(current_day_spot_objects_raw)
                current_day_spot_objects_raw = optimized_day_attractions

            current_day_spots_timed = []
            
            # Start day at 9 AM
            current_time = datetime.strptime("09:00", "%H:%M")
            lunch_added = False
            dinner_added = False

            for spot_obj in current_day_spot_objects_raw:
                # Add Lunch Break if it's past 12:30 and not yet added
                if not lunch_added and current_time.hour >= 12 and current_time.hour <= 15:
                    lunch_end = current_time + timedelta(hours=1.5)
                    rest_name, rest_desc = "Lunch Break", "Time for lunch at a local restaurant or cafe."
                    if hasattr(self.info_agent, 'poi_manager') and spot_obj.get("location"):
                        try:
                            rests = self.info_agent.poi_manager.get_restaurants(spot_obj["location"].get("lat", 0), spot_obj["location"].get("lng", 0), radius=2000, number=1)
                            if rests:
                                rest_name = rests[0].get("name", "Lunch Break")
                                rest_desc = rests[0].get("description", "Time for lunch at a local restaurant or cafe.")
                        except Exception:
                            pass
                    current_day_spots_timed.append({
                        "id": f"lunch_day_{day_number}",
                        "name": rest_name,
                        "category": "Food",
                        "description": rest_desc,
                        "start_time": current_time.strftime("%H:%M"),
                        "end_time": lunch_end.strftime("%H:%M"),
                        "estimated_duration": 1.5,
                        "is_meal": True
                    })
                    current_time = lunch_end
                    lunch_added = True

                # Add Dinner Break if it's past 19:00 and not yet added
                if not dinner_added and current_time.hour >= 19:
                    dinner_end = current_time + timedelta(hours=1.5)
                    rest_name, rest_desc = "Dinner", "Evening dinner to wrap up the day."
                    if hasattr(self.info_agent, 'poi_manager') and spot_obj.get("location"):
                        try:
                            rests = self.info_agent.poi_manager.get_restaurants(spot_obj["location"].get("lat", 0), spot_obj["location"].get("lng", 0), radius=2000, number=1)
                            if rests:
                                rest_name = rests[0].get("name", "Dinner")
                                rest_desc = rests[0].get("description", "Evening dinner to wrap up the day.")
                        except Exception:
                            pass
                    current_day_spots_timed.append({
                        "id": f"dinner_day_{day_number}",
                        "name": rest_name,
                        "category": "Food",
                        "description": rest_desc,
                        "start_time": current_time.strftime("%H:%M"),
                        "end_time": dinner_end.strftime("%H:%M"),
                        "estimated_duration": 1.5,
                        "is_meal": True
                    })
                    current_time = dinner_end
                    dinner_added = True

                spot_duration = spot_obj.get("estimated_duration", 2)
                if spot_duration <= 0: spot_duration = 2
                
                # Assume 30 mins travel time between spots roughly
                if current_day_spots_timed and not current_day_spots_timed[-1].get("is_meal"):
                    current_time += timedelta(minutes=30)
                
                # Check opening hours roughly
                open_hour, close_hour = self._parse_opening_hours(spot_obj.get("opening_hours"))
                if current_time.hour < open_hour:
                    current_time = current_time.replace(hour=open_hour, minute=0)
                
                # Validation: Prevent scheduling past 22:00
                if current_time.hour >= 22:
                    print(f"[WARN] Skipping {spot_obj.get('name')} to prevent over-scheduling (time: {current_time.strftime('%H:%M')}).")
                    continue
                
                spot_with_time = spot_obj.copy()
                activity_end = current_time + timedelta(hours=spot_duration)
                
                # Hard limit at midnight
                if activity_end.day != current_time.day:
                    activity_end = current_time.replace(hour=23, minute=59)
                
                spot_with_time["start_time"] = current_time.strftime("%H:%M")
                spot_with_time["end_time"] = activity_end.strftime("%H:%M")
                
                # Validation: Ensure start_time < end_time
                if current_time < activity_end:
                    current_day_spots_timed.append(spot_with_time)
                else:
                    print(f"[WARN] Invalid time generated for {spot_obj.get('name')}. Skipping.")
                
                current_time = activity_end

            current_day_spots_timed = self._sort_spots_by_time(current_day_spots_timed)

            itinerary.append({
                "day": day_number,
                "date": current_date.strftime("%Y-%m-%d"),
                "spots": current_day_spots_timed,
            })
            current_date += timedelta(days=1)

        return itinerary
