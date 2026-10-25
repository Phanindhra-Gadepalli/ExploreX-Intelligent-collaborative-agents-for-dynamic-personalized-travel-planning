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
        # Meal lookups are cached by ~1 km grid cell so two stops in the same
        # neighbourhood do not trigger repeated restaurant provider calls.
        self._restaurant_cache = {}
        self._last_routing = {"source": "none", "optimized": False, "status": "not_attempted"}
        self.info_agent = None
        try:
            self.info_agent = InformationAgent()
        except Exception as e:
            print(f"Error initialising InformationAgent in RouteAgent: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Location resolution
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_location(self, spot_obj):
        """
        Attempt to find valid lat/lng coordinates for an attraction.
        Returns (lat, lng) or (None, None).
        Mutates spot_obj to ensure 'location' is a clean dict if found.
        """
        if not spot_obj:
            return None, None
            
        if spot_obj.get("_location_resolved"):
            loc = spot_obj.get("location")
            if loc: return loc.get("lat"), loc.get("lng")
            return None, None
            
        loc = spot_obj.get("location")
        lat, lng = None, None
        
        if isinstance(loc, dict):
            lat = loc.get("lat") or loc.get("latitude")
            lng = loc.get("lng") or loc.get("longitude")
        elif isinstance(loc, (list, tuple)) and len(loc) >= 2:
            try:
                lat = float(loc[0])
                lng = float(loc[1])
            except (ValueError, TypeError):
                pass
                
        # Fallback to flat keys
        if lat is None or lng is None:
            lat = spot_obj.get("lat") or spot_obj.get("latitude")
            lng = spot_obj.get("lng") or spot_obj.get("longitude")
            
        try:
            lat = float(lat) if lat is not None else None
            lng = float(lng) if lng is not None else None
        except (ValueError, TypeError):
            lat, lng = None, None
            
        if lat is not None and lng is not None:
            spot_obj["location"] = {"lat": lat, "lng": lng}
            spot_obj["_location_resolved"] = True
            return lat, lng
            
        # Geocoding fallback
        name = spot_obj.get("name")
        if not name:
            spot_obj["location"] = None
            spot_obj["_location_resolved"] = True
            return None, None
            
        if self.info_agent and hasattr(self.info_agent, "geocoder"):
            city = ""
            if hasattr(self.info_agent, "state") and isinstance(self.info_agent.state, dict):
                city = self.info_agent.state.get("city", "")
            
            # Never geocode a bare name without destination context: "Relaxation"
            # on its own once resolved to Paris, polluting the itinerary with
            # foreign coordinates. Only geocode "<name>, <city>".
            if city:
                query = f"{name}, {city}".strip(", ")
                print(f"[ROUTE_AGENT][WARN] Missing coordinates for '{name}'. Attempting geocode: '{query}'")
                try:
                    res = self.info_agent.geocoder.geocode(query)
                    if res and res.lat and res.lng:
                        print(f"[ROUTE_AGENT][INFO] Geocoded fallback for '{name}': {res.lat}, {res.lng}")
                        spot_obj["location"] = {"lat": float(res.lat), "lng": float(res.lng)}
                        spot_obj["_location_resolved"] = True
                        return float(res.lat), float(res.lng)
                except Exception as e:
                    print(f"[ROUTE_AGENT][ERROR] Geocode failed for '{name}': {e}")
                
        print(f"[ROUTE_AGENT][WARN] Unable to resolve coordinates for: {name}")
        spot_obj["location"] = None
        spot_obj["_location_resolved"] = True
        return None, None

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
        self._last_routing = {
            "source": "none", "optimized": False, "status": "not_attempted",
            "note": "No routing was required.",
        }
        if not attractions_for_day or len(attractions_for_day) <= 1:
            self._last_routing = {
                "source": "none", "optimized": False, "status": "not_required",
                "note": "One or zero stops: no travel order to optimise.",
            }
            return attractions_for_day  # No optimisation needed for 0 or 1 attraction

        # Resolve all locations and separate them
        valid_spots = []
        unresolved_spots = []
        for attr in attractions_for_day:
            lat, lng = self._resolve_location(attr)
            if lat is not None and lng is not None:
                valid_spots.append(attr)
            else:
                unresolved_spots.append(attr)

        if len(valid_spots) <= 1:
            # Not enough valid spots for routing
            self._last_routing = {
                "source": "none", "optimized": False, "status": "insufficient_locations",
                "note": "Fewer than two stops had verified coordinates; provider order kept.",
            }
            return valid_spots + unresolved_spots

        # Check if we can use the InformationAgent
        if not self.info_agent:
            print("InformationAgent not available. Using fallback TSP solution.")
            self._last_routing = {
                "source": "local_tsp", "optimized": True, "status": "fallback",
                "note": "Route order estimated with the local distance heuristic; OSRM was unavailable.",
            }
            return self.get_optimal_route(valid_spots) + unresolved_spots

        try:
            # The day starts at the first attraction and ENDS at the last one.
            # The previous implementation used the same point for origin and
            # destination ("loop back to start"), which made OSRM plan a route
            # that drove back to where the day began and reported a bogus
            # start-to-same-point journey.
            origin = valid_spots[0]
            destination = valid_spots[-1]

            # Everything in between is a waypoint
            waypoints = []
            for attraction in valid_spots[1:-1]:
                lat, lng = attraction["location"]["lat"], attraction["location"]["lng"]
                waypoints.append(f"{lat},{lng}")

            # Prepare origin and destination strings
            lat, lng = origin["location"]["lat"], origin["location"]["lng"]
            origin_location = f"{lat},{lng}"
            dest_lat, dest_lng = destination["location"]["lat"], destination["location"]["lng"]
            destination_location = f"{dest_lat},{dest_lng}"

            # Only two spots: nothing to optimise, keep the natural order.
            if not waypoints:
                self._last_routing = {
                    "source": "none", "optimized": False, "status": "two_stops",
                    "note": "Two stops: provider order kept (no intermediate waypoints).",
                }
                return list(valid_spots) + unresolved_spots

            # Call plan_with_waypoints
            optimized_route_data = None
            try:
                optimized_route_data = self.info_agent.plan_with_waypoints(
                    origin=origin_location,
                    destination=destination_location,
                    waypoints=waypoints,
                    mode="driving",
                )
            except Exception as e:
                print(f"[ROUTE_AGENT][ERROR] OSRM routing failed: {e}")

            if not optimized_route_data:
                print("[ROUTE_AGENT][FALLBACK] OSRM unavailable; using the local TSP "
                      "distance heuristic. The day's order is an estimate, not a live "
                      "traffic route.")
                self._last_routing = {
                    "source": "local_tsp", "optimized": True, "status": "fallback",
                    "note": "Route order estimated with the local distance heuristic; "
                            "OSRM did not return a route.",
                }
                return self.get_optimal_route(valid_spots) + unresolved_spots

            self._last_routing = {
                "source": optimized_route_data.get("routing_source", "osrm_trip"),
                "optimized": bool(optimized_route_data.get("optimized", True)),
                "status": optimized_route_data.get("routing_status", "ok"),
                "note": "Visit order optimised by OSRM.",
            }

            # Extract the optimised waypoint order (indices are relative to the
            # intermediate waypoints list, i.e. valid_spots[1:-1]).
            waypoint_indices = optimized_route_data.get("waypoint_original_indices", [])

            if not waypoint_indices and len(waypoints) > 0:
                waypoint_indices = list(range(len(waypoints)))

            # Create the optimised list of attractions
            optimized_attractions = [origin]  # Start with origin

            # Add waypoints in optimised order
            for idx in waypoint_indices:
                if 0 <= idx < len(waypoints):
                    optimized_attractions.append(valid_spots[idx + 1])

            # Close the day at the destination spot (never visited twice).
            if optimized_attractions and optimized_attractions[-1] is not destination:
                optimized_attractions.append(destination)

            return optimized_attractions + unresolved_spots

        except Exception as e:
            print(f"Error optimising daily route: {e}")
            # Fallback to internal TSP solver
            self._last_routing = {
                "source": "local_tsp", "optimized": True, "status": "fallback",
                "note": "Route order estimated with the local distance heuristic after "
                        "an unexpected routing error.",
            }
            return self.get_optimal_route(valid_spots) + unresolved_spots

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
        lat1, lon1 = self._resolve_location(spot1)
        lat2, lon2 = self._resolve_location(spot2)

        # Check if we have location data
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 1  # Default distance if no location data

        # Check cache first
        cache_key = f"{spot1.get('id', 'u1')}_{spot2.get('id', 'u2')}"
        if cache_key in self.distances_cache:
            return self.distances_cache[cache_key]

        R = 6371  # Earth radius in km

        # Convert coordinates to radians
        lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])

        # Haversine formula
        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
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

    def _meal_place(self, location, fallback_name, fallback_description):
        """Return a real nearby restaurant for a meal slot, cached per ~1 km cell.

        When no restaurant can be confirmed we keep the neutral "Lunch Break" /
        "Dinner" label and a generic description.  No restaurant name, rating or
        price is ever invented.
        """
        if not location or not self.info_agent or not hasattr(self.info_agent, "poi_manager"):
            return fallback_name, fallback_description
        try:
            lat = float(location.get("lat"))
            lng = float(location.get("lng"))
        except (TypeError, ValueError):
            return fallback_name, fallback_description

        key = (round(lat, 2), round(lng, 2), 2000)
        if key not in self._restaurant_cache:
            try:
                rests = self.info_agent.poi_manager.get_restaurants(
                    lat, lng, radius=2000, number=1
                ) or []
                first = rests[0] if rests and isinstance(rests[0], dict) else None
                self._restaurant_cache[key] = (
                    (first.get("name") or fallback_name,
                     first.get("description") or fallback_description)
                    if first else (fallback_name, fallback_description)
                )
            except Exception as e:
                print(f"[ROUTE_AGENT][ERROR] Restaurant lookup failed: {e}")
                self._restaurant_cache[key] = (fallback_name, fallback_description)
        return self._restaurant_cache[key]

    # Labels the AI is explicitly allowed to use for unscheduled/free time.
    # These are *not* POIs, so they may be rendered without provider data;
    # anything else that cannot be resolved is reported as unscheduled instead
    # of being invented (spec Phase 8 invariant: never fabricate a POI).
    SEMANTIC_LABELS = {
        "relaxation", "leisure", "free time", "rest", "rest day", "spare time",
        "lunch break", "lunch", "dinner", "breakfast", "acclimatization",
        "acclimatisation", "shopping", "local exploration", "walking tour",
    }

    def _lookup_spot(self, name, all_spots_object_map):
        """Resolve a planned name against the authoritative POI map.

        Tolerates the whitespace/case differences the LLM introduces and the
        ``"1. Name"`` numbering some models emit.  Returns the original provider
        object (so id/coordinates are preserved verbatim) or None.
        """
        if not isinstance(name, str) or not name.strip():
            return None
        stripped = name.strip()
        # "1. Kaziranga" / "2) Kaziranga" -> "Kaziranga"
        unnumbered = re.sub(r"^\s*\d+[).\-]\s*", "", stripped).strip()
        lowered = unnumbered.lower()

        for cand in (stripped, unnumbered, lowered):
            if cand in all_spots_object_map:
                return all_spots_object_map[cand]

        for key, obj in all_spots_object_map.items():
            if str(key).strip().lower() == lowered:
                return obj
            obj_name = str((obj or {}).get("name") or "").strip().lower()
            if obj_name and obj_name == lowered:
                return obj

        # Substring match only when it is unambiguous.
        hits = [
            obj for key, obj in all_spots_object_map.items()
            if len(lowered) > 6 and (
                lowered in str(key).strip().lower() or str(key).strip().lower() in lowered
            )
        ]
        if len(hits) == 1:
            return hits[0]
        return None

    def format_daily_plan_to_itinerary(self, daily_plan_name_dict, all_spots_object_map, start_date_str,
                                       retrieved_knowledge=None, accommodation=None, unscheduled_out=None):
        """Generate daily itinerary with TSP routing, meal breaks, and realistic timing.

        ``unscheduled_out`` (optional list) receives one entry per selected
        attraction that could NOT be placed, with a machine-readable ``reason``.
        Silently dropping or inventing them is what made travelers see a
        'selected attraction missing from plan' with no explanation.
        """
        itinerary = []
        unscheduled = unscheduled_out if isinstance(unscheduled_out, list) else []
        # A place may appear on only one day: a model that repeats a highlight to
        # "fill" the itinerary must not create a duplicate visit.
        seen_attraction_names = set()
        try:
            current_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            is_suggested = False
        except (ValueError, TypeError):
            print(f"[WARN] Invalid or flexible start_date_str: {start_date_str}. Using current date as a suggestion.")
            current_date = datetime.now()
            is_suggested = True

        # Sort day keys numerically.  Day keys arrive as "day1", "Day 2", "1"
        # or 3 depending on the model, so normalise to an integer day number.
        day_entries = []
        for raw_key in daily_plan_name_dict.keys():
            m = re.search(r"(\d+)", str(raw_key))
            if not m:
                print(f"[ROUTE_AGENT][WARN] Ignoring un-numbered day key: {raw_key!r}")
                continue
            day_entries.append((int(m.group(1)), raw_key))
        day_entries.sort(key=lambda t: t[0])

        for day_number, day_key in day_entries:
            spot_names_for_day = daily_plan_name_dict.get(day_key, []) or []

            current_day_spot_objects_raw = []
            day_unscheduled = []
            for name in spot_names_for_day:
                resolved = self._lookup_spot(name, all_spots_object_map)
                if resolved is not None:
                    resolved_name = re.sub(
                        r"\s+", " ", str(resolved.get("name") or name)).strip().lower()
                    if resolved_name in seen_attraction_names:
                        print(f"[ROUTE_AGENT][DUPLICATE] '{resolved.get('name')}' is already "
                              f"scheduled on an earlier day; skipping the repeat.")
                        # This is a planner repeat, not a place missing from the
                        # trip: it is disclosed on the day but must NOT appear in
                        # the global "selected but unscheduled" list, which drives
                        # the user-facing "not in the final itinerary" warning.
                        day_unscheduled.append({
                            "name": str(resolved.get("name") or name).strip(),
                            "id": str(resolved.get("id")) if resolved.get("id") is not None else None,
                            "day": day_number,
                            "reason": "duplicate_across_days",
                            "detail": "This place was already scheduled on another day; "
                                      "it is not visited twice.",
                        })
                        continue
                    seen_attraction_names.add(resolved_name)
                    current_day_spot_objects_raw.append(resolved)
                    continue
                label_key = re.sub(r"^\s*\d+[).\-]\s*", "", str(name)).strip().lower()
                if label_key in self.SEMANTIC_LABELS:
                    # Deliberate free-time label from the planner, not a POI.
                    current_day_spot_objects_raw.append({
                        "id": f"semantic_{label_key.replace(' ', '_')}",
                        "name": str(name).strip(),
                        "description": "Unstructured free time suggested by the planner.",
                        "estimated_duration": 2,
                        "location": None,
                        "is_semantic_label": True,
                        "entry_type": "free_time",
                        # Leisure labels ("Relaxation") are not real POIs — never geocode them.
                        "_location_resolved": True
                    })
                else:
                    print(f"[ROUTE_AGENT][UNSCHEDULED] '{name}' has no matching provider record; "
                          f"reported instead of fabricated.")
                    unscheduled.append({
                        "name": str(name).strip(),
                        "reason": "not_found_in_data_providers",
                        "detail": f"Planned on day {day_number} but no provider record matched the name.",
                        "day": day_number,
                    })

            # TSP Optimization
            # Ensure all locations are resolved early to prevent NoneType exceptions later
            for spot_obj in current_day_spot_objects_raw:
                self._resolve_location(spot_obj)

            routing_meta = {
                "source": "none", "optimized": False, "status": "not_required",
                "note": "No travel order to optimise for this day.",
            }
            if current_day_spot_objects_raw and len(current_day_spot_objects_raw) > 1:
                optimized_day_attractions = self.optimize_daily_route(current_day_spot_objects_raw)
                current_day_spot_objects_raw = optimized_day_attractions
                routing_meta = dict(self._last_routing)

            current_day_spots_timed = []
            # Start day at 9 AM
            current_time = datetime.strptime("09:00", "%H:%M")
            lunch_added = False
            dinner_added = False

            for spot_obj in current_day_spot_objects_raw:
                # Add Lunch Break if it's past 12:30 and not yet added
                if not lunch_added and current_time.hour >= 12 and current_time.hour <= 15:
                    lunch_end = current_time + timedelta(hours=1.5)
                    rest_name, rest_desc = self._meal_place(
                        spot_obj.get("location"),
                        "Lunch Break",
                        "Time for lunch at a local restaurant or cafe.",
                    )
                    current_day_spots_timed.append({
                        "id": f"lunch_day_{day_number}",
                        "name": rest_name,
                        "category": "Food",
                        "description": rest_desc,
                        "start_time": current_time.strftime("%H:%M"),
                        "end_time": lunch_end.strftime("%H:%M"),
                        "estimated_duration": 1.5,
                        "is_meal": True,
                        "entry_type": "meal",
                    })
                    current_time = lunch_end
                    lunch_added = True

                # Add Dinner Break if it's past 19:00 and not yet added
                if not dinner_added and current_time.hour >= 19:
                    dinner_end = current_time + timedelta(hours=1.5)
                    rest_name, rest_desc = self._meal_place(
                        spot_obj.get("location"),
                        "Dinner",
                        "Evening dinner to wrap up the day.",
                    )
                    current_day_spots_timed.append({
                        "id": f"dinner_day_{day_number}",
                        "name": rest_name,
                        "category": "Food",
                        "description": rest_desc,
                        "start_time": current_time.strftime("%H:%M"),
                        "end_time": dinner_end.strftime("%H:%M"),
                        "estimated_duration": 1.5,
                        "is_meal": True,
                        "entry_type": "meal",
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
                    if not spot_obj.get("is_semantic_label"):
                        reason = {
                            "name": spot_obj.get("name"),
                            "id": str(spot_obj.get("id")) if spot_obj.get("id") is not None else None,
                            "day": day_number,
                            "reason": "no_time_left_in_day",
                            "detail": f"Day {day_number} was already full at {current_time.strftime('%H:%M')}; the attraction was not scheduled.",
                        }
                        day_unscheduled.append(reason)
                        unscheduled.append(reason)
                    continue
                
                spot_with_time = spot_obj.copy()
                if not spot_with_time.get("entry_type"):
                    spot_with_time["entry_type"] = (
                        "free_time" if spot_with_time.get("is_semantic_label") else "attraction"
                    )
                activity_end = current_time + timedelta(hours=spot_duration)
                
                # Hard limit at midnight
                if activity_end.day != current_time.day:
                    activity_end = current_time.replace(hour=23, minute=59)
                
                spot_with_time["start_time"] = current_time.strftime("%H:%M")
                spot_with_time["end_time"] = activity_end.strftime("%H:%M")
                
                spot_with_time["time"] = f"{spot_with_time['start_time']} - {spot_with_time['end_time']}"
                
                # Retrieve the resolved location safely
                loc = spot_with_time.get("location")
                if loc and isinstance(loc, dict):
                    spot_with_time["latitude"] = loc.get("lat")
                    spot_with_time["longitude"] = loc.get("lng")
                else:
                    spot_with_time["latitude"] = None
                    spot_with_time["longitude"] = None

                # Validation: Ensure start_time < end_time
                if current_time < activity_end:
                    current_day_spots_timed.append(spot_with_time)
                else:
                    print(f"[WARN] Invalid time generated for {spot_obj.get('name')}. Skipping.")
                    day_unscheduled.append({
                        "name": spot_obj.get("name"),
                        "id": str(spot_obj.get("id")) if spot_obj.get("id") is not None else None,
                        "day": day_number,
                        "reason": "invalid_time_generated",
                        "detail": f"Could not assign a valid time slot on day {day_number}.",
                    })
                    unscheduled.append(day_unscheduled[-1])
                
                current_time = activity_end

            current_day_spots_timed = self._sort_spots_by_time(current_day_spots_timed)

            # Canonical per-day contract (spec Phase 3): every activity carries
            # its integer day, 1-based sequence, stable provider id and the
            # coordinates the map needs, so downstream filters never have to
            # guess from strings.
            for seq, act in enumerate(current_day_spots_timed, start=1):
                act["sequence"] = seq
                act["day"] = day_number
                if act.get("attraction_id") is None and act.get("id") is not None:
                    act["attraction_id"] = str(act["id"])
                if "latitude" not in act:
                    loc = act.get("location") or {}
                    act["latitude"] = loc.get("lat") if isinstance(loc, dict) else None
                    act["longitude"] = loc.get("lng") if isinstance(loc, dict) else None

            itinerary.append({
                "day": day_number,
                "date": (current_date + timedelta(days=day_number - 1)).strftime("%d %b %Y"),
                "is_suggested_date": is_suggested,
                "activities": current_day_spots_timed,
                "unscheduled": day_unscheduled,
                "transport": None,
                "food": None,
                "accommodation": None,
                "cost": None,
                # Provenance for the UI/email: was this day's order optimised by
                # OSRM, or estimated locally because the router was unreachable?
                "routing_source": routing_meta.get("source"),
                "routing_status": routing_meta.get("status"),
                "routing_optimized": bool(routing_meta.get("optimized")),
                "routing_note": routing_meta.get("note"),
            })

        return itinerary
