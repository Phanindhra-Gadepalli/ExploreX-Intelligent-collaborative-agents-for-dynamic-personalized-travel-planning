"""
budget_agent.py  (v2 — Realistic INR Estimation)
==================================================
Key improvements over v1:
  1. Reduced, realistic cost caps — eliminates luxury defaults for non-luxury budgets
  2. City-tier aware pricing — Shimla uses different rates than Mumbai
  3. Static destination-aware defaults as the PRIMARY cost source
     (LLM used only as optional refinement, with tighter clamping)
  4. Car rental always computed as  daily_rate × days  with hard market-rate caps;
     API raw prices are normalised to a per-day rate before use
  5. Fuel calculation uses a capped per-day local distance (30–60 km/day);
     the haversine sum is now capped so inter-city spans are not counted twice
  6. Calculation breakdown included in every estimate for full transparency
  7. Budget optimisation is more aggressive (8 passes, deeper cuts) before
     declaring infeasibility
  8. Attraction entry fees tuned to realistic Indian museum/park gate prices
"""

import sys
import io
import math
import json
import re

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


class BudgetAgent:
    """
    Dedicated agent responsible exclusively for all budget estimation logic.

    Public API
    ----------
    estimate_budget(spots, user_prefs, should_rent_car, car_info,
                    fuel_price, transit_options) -> dict

    The returned dict contains:
        total, accommodation, food, transport, intercity_transport,
        miscellaneous, attractions, car_rental, fuel_cost,
        budget_level, budget_amount, budget_strictness, budget_warning,
        budget_infeasible, remaining, rooms, days, people,
        breakdown  ← new: human-readable calculation strings per component
    """

    # ── City tier lookup ──────────────────────────────────────────────────────
    # Tier 1 = Metro / expensive cities (baseline pricing)
    # Tier 2 = Major / mid-sized cities  (0.80× baseline)
    # Tier 3 = Hill stations / smaller towns (0.65× baseline)
    _CITY_TIERS: dict = {
        # Tier 1 — Metro
        "mumbai": 1, "delhi": 1, "new delhi": 1, "bangalore": 1, "bengaluru": 1,
        "hyderabad": 1, "chennai": 1, "kolkata": 1, "pune": 1, "gurgaon": 1,
        "gurugram": 1, "noida": 1, "navi mumbai": 1,
        # Tier 2 — Major cities / popular tourist destinations
        "jaipur": 2, "kochi": 2, "ahmedabad": 2, "surat": 2, "chandigarh": 2,
        "lucknow": 2, "indore": 2, "bhopal": 2, "agra": 2, "varanasi": 2,
        "amritsar": 2, "udaipur": 2, "jodhpur": 2, "jaisalmer": 2,
        "goa": 2, "panaji": 2, "madgaon": 2,
        "mysore": 2, "mysuru": 2, "coimbatore": 2,
        "visakhapatnam": 2, "vizag": 2, "bhubaneswar": 2,
        "thiruvananthapuram": 2, "trivandrum": 2, "kozhikode": 2,
        "nagpur": 2, "nashik": 2, "aurangabad": 2,
        # Tier 3 — Hill stations / heritage towns / smaller destinations
        "shimla": 3, "manali": 3, "dharamshala": 3, "mcleodganj": 3,
        "mussoorie": 3, "nainital": 3, "rishikesh": 3, "haridwar": 3,
        "ooty": 3, "munnar": 3, "coorg": 3, "kodagu": 3, "wayanad": 3,
        "hampi": 3, "alleppey": 3, "alappuzha": 3,
        "darjeeling": 3, "gangtok": 3, "leh": 3, "ladakh": 3,
        "shillong": 3, "puri": 3, "bodh gaya": 3, "kasol": 3,
        "pushkar": 3, "ranthambore": 3, "jim corbett": 3, "kaziranga": 3,
    }

    _TIER_MULTIPLIER: dict = {1: 1.0, 2: 0.80, 3: 0.65}

    # ── Realistic cost caps (INR) — Tier 1 reference ──────────────────────────
    # accommodation : per ROOM  per NIGHT
    # food          : per PERSON per DAY  (all meals)
    # transport     : per PERSON per DAY  (local city only — NOT intercity)
    #
    # These caps reflect actual 2024-25 Indian market rates:
    #   low    = hostels / budget guesthouses / dhabas / shared transport
    #   medium = 3-star hotels / mid-range restaurants / app-cabs
    #   high   = 4-star hotels / good restaurants / private cabs
    #            (luxury 5-star is NOT assumed unless explicitly requested)
    _COST_CAPS: dict = {
        "low": {
            "acc_min": 600,   "acc_max": 1_500,
            "food_min": 300,  "food_max": 600,
            "trans_min": 80,  "trans_max": 200,
        },
        "medium": {
            "acc_min": 1_500, "acc_max": 4_000,
            "food_min": 600,  "food_max": 1_200,
            "trans_min": 200, "trans_max": 500,
        },
        "high": {
            "acc_min": 3_500, "acc_max": 7_000,
            "food_min": 1_000, "food_max": 2_000,
            "trans_min": 300, "trans_max": 800,
        },
    }

    # ── Static destination-aware base costs (Tier 1 reference, INR) ──────────
    # Used as the PRIMARY cost estimate; LLM is used only as a refinement.
    # Values represent realistic averages (not minimums, not maximums).
    _BASE_COSTS: dict = {
        "low":    {"acc": 950,   "food": 420,   "trans": 130},
        "medium": {"acc": 2_500, "food": 800,   "trans": 320},
        "high":   {"acc": 5_000, "food": 1_400, "trans": 550},
    }

    # ── Car rental realistic INR daily rates ──────────────────────────────────
    # Default  = typical market rate for the budget level
    # Max cap  = hard ceiling even if API returns higher values
    # Vehicle  = hatchback (low) / sedan (medium) / SUV (high)
    _RENTAL_DAILY_INR: dict = {"low": 1_500, "medium": 2_500, "high": 4_000}
    _RENTAL_DAILY_MAX: dict = {"low": 2_000, "medium": 3_500, "high": 5_500}

    # ── Fuel constants ────────────────────────────────────────────────────────
    _PETROL_INR_PER_LITRE: float = 110.0   # update to current average as needed

    # Fuel efficiency: LOW = small hatchback (~6L/100km), MEDIUM = sedan (~8L),
    # HIGH = SUV (~11L).  Lower is more efficient.
    _CONSUMPTION_L_PER_100KM: dict = {"low": 6.0, "medium": 8.0, "high": 11.0}

    # Local driving per day within the destination city (for fuel estimation).
    # Intercity travel is already captured in intercity_transport_cost, so this
    # covers sightseeing drives within the city only.
    _BASE_DAILY_KM: float = 30.0   # conservative tourist day
    _MAX_DAILY_KM:  float = 60.0   # hard cap — beyond this, intercity overlap is assumed

    # ── Currency / entry fees ─────────────────────────────────────────────────
    _USD_TO_INR: float = 84.0

    # Static, documented assumptions used only when a provider quote is in a
    # foreign currency.  They are surfaced in ``currency_assumptions`` so a user
    # can see that the INR figure is a conversion, not a live FX quote.
    _CURRENCY_TO_INR: dict = {
        "USD": 84.0, "EUR": 91.0, "GBP": 107.0, "JPY": 0.56,
        "AED": 22.9, "SGD": 63.0, "THB": 2.40, "KRW": 0.060,
        "AUD": 55.0, "CAD": 61.0, "CHF": 95.0, "NZD": 51.0,
        "MYR": 19.0, "IDR": 0.0053, "VND": 0.0033, "LKR": 0.29,
        "NPR": 0.62, "TRY": 2.40, "EGP": 1.70,
    }
    _CURRENCY_ASSUMPTION_NOTE = (
        "Foreign-currency quotes are converted with a static reference rate "
        "bundled with ExploreX; they are indicative, not a live FX quote."
    )

    # ── International cost index ──────────────────────────────────────────────
    # Multiplier applied to the Indian Tier-1 baseline (accommodation, food,
    # local transport and entry fees).  Values are destination *indices*, not
    # prices: the actual daily figures are still derived from the auditable
    # Indian baseline and disclosed in the breakdown.
    _COUNTRY_COST_INDEX: dict = {
        "japan": 3.2, "south korea": 2.6, "singapore": 3.0, "uae": 2.4,
        "united arab emirates": 2.4, "united kingdom": 3.0, "france": 2.9,
        "italy": 2.7, "spain": 2.5, "germany": 2.7, "netherlands": 2.8,
        "switzerland": 3.8, "portugal": 2.3, "greece": 2.2, "turkey": 1.3,
        "egypt": 1.0, "usa": 3.1, "united states": 3.1,
        "united states of america": 3.1, "canada": 2.7, "australia": 2.7,
        "new zealand": 2.5, "thailand": 1.2, "indonesia": 1.0,
        "malaysia": 1.1, "vietnam": 0.9, "sri lanka": 0.85, "nepal": 0.8,
    }
    _INTERNATIONAL_DEFAULT_INDEX: float = 2.2

    # International entry fees are higher than Indian monument tickets.  The
    # multiplier is applied to the Indian tier map and shown in the breakdown.
    _INTERNATIONAL_ENTRY_MULTIPLIER: float = 2.6

    # Petrol/diesel reference used for self-drive fuel estimates (INR/litre).
    _INTERNATIONAL_FUEL_INR_PER_LITRE: float = 115.0

    # price_level 0 = free, 1 = budget spot, 2 = moderate, 3 = significant,
    #             4 = premium (international-grade tourist site)
    # Fees are per person in INR (realistic Indian ticket prices 2024)
    _ENTRY_FEE_MAP: dict = {0: 0, 1: 50, 2: 200, 3: 400, 4: 750}
    _ENTRY_FEE_DEFAULT: int = 150   # used when price_level is missing

    # ── Constructor ───────────────────────────────────────────────────────────

    def __init__(self) -> None:
        """Initialise BudgetAgent (no external dependencies required at init time)."""
        pass

    # ═════════════════════════════════════════════════════════════════════════
    # PUBLIC INTERFACE
    # ═════════════════════════════════════════════════════════════════════════

    def estimate_budget(
        self,
        spots: list,
        user_prefs: dict,
        should_rent_car: bool = False,
        car_info: list | None = None,
        fuel_price=None,          # kept for API compatibility; internal constants used
        transit_options: dict | None = None,
    ) -> dict:
        """
        Produce a complete, itemised budget estimate for the trip.

        Parameters
        ----------
        spots            : list of attraction objects (with ``price_level``,
                           ``location`` → ``lat``/``lng``, etc.)
        user_prefs       : dict with keys: budget, budget_amount,
                           budget_strictness, people, days, city, origin_city
        should_rent_car  : whether a car rental has been recommended
        car_info         : car-rental API options (optional)
        fuel_price       : ignored — internal INR constants are used
        transit_options  : TransitAgent output with flights / trains / buses

        Returns
        -------
        dict with keys:
            total, accommodation, food, transport, intercity_transport,
            miscellaneous, attractions, car_rental, fuel_cost,
            budget_level, budget_amount, budget_strictness, budget_warning,
            budget_infeasible, remaining, rooms, days, people, breakdown
        """
        # ── 1. Destination profile + budget metadata ─────────────────────────
        self._last_min_viable = 0.0
        city_name_pre = str((user_prefs or {}).get("city") or "")
        if not city_name_pre or city_name_pre.lower() == "any":
            city_name_pre = (spots[0].get("city", "") if spots else "") or "India"
        profile = self._destination_profile(user_prefs or {}, city_name_pre, spots)
        budget_level, budget_amount, budget_strictness = self._resolve_budget_level(
            user_prefs or {}, profile["cost_index"]
        )

        # ── 2. Trip dimensions ────────────────────────────────────────────────
        num_people = max(1, int(user_prefs.get("people", 1)))
        num_days   = max(1, int(user_prefs.get("days", 1)))
        rooms      = max(1, (num_people + 1) // 2)   # 2 pax per room, rounded up
        city_name  = city_name_pre

        # ── 3. Destination-aware per-day costs (static-primary, LLM-optional) ─
        acc_ppd, food_ppd, trans_ppd = self._get_per_day_costs(budget_level, city_name, profile)

        # ── 4. Calculate each budget component ────────────────────────────────
        total_acc   = self._calculate_accommodation_cost(acc_ppd, rooms, num_days)
        total_food  = self._calculate_food_cost(food_ppd, num_people, num_days)
        # A rental recommendation stays in the plan even when the rental API is
        # unavailable (403 / no key): we then price it from a transparent
        # fallback estimate instead of silently dropping the cost to zero.
        rental_recommended = bool(should_rent_car)
        actual_rent_car = rental_recommended and bool(car_info)
        driving_mode = rental_recommended          # covers both real + fallback pricing
        total_trans = self._calculate_local_transport_cost(trans_ppd, num_people, num_days, driving_mode)

        attraction_cost = self._calculate_attraction_cost(spots, num_people, profile)

        car_rental_cost = 0.0
        fuel_cost       = 0.0
        fuel_km         = 0.0
        car_rental_source = None                   # None | "provider" | "fallback_estimate"
        rental_daily_estimate = 0.0
        if driving_mode:
            fuel_cost, fuel_km = self._calculate_fuel_cost(
                spots, budget_level, num_days,
                fuel_inr_per_litre=profile["fuel_inr_per_litre"],
            )
            if actual_rent_car:
                car_rental_cost, daily_car_rate = self._calculate_car_rental_cost(
                    car_info, budget_level, num_days, profile=profile
                )
                car_rental_source = "provider"
            else:
                # Provider returned nothing: estimate per-vehicle per-day rate.
                default_daily = float(self._RENTAL_DAILY_INR.get(budget_level, 2_500))
                if profile.get("is_international"):
                    default_daily *= float(profile.get("cost_index", 1.0))
                rental_daily_estimate = default_daily
                car_rental_cost = round(default_daily * num_days, 2)
                car_rental_source = "fallback_estimate"
                print(f"[BUDGET][CAR_RENTAL][FALLBACK] Rental provider unavailable — "
                      f"estimated ₹{car_rental_cost:,.0f} ({int(num_days)} × ₹{default_daily:,.0f}/day).")
            # local transport replaced by fuel when driving

        subtotal_without_transit = (
            total_acc + total_food + total_trans
            + attraction_cost + car_rental_cost + fuel_cost
        )

        intercity_transport_cost, intercity_mode = self._calculate_intercity_transport_cost(
            transit_options, budget_level, num_people, budget_amount, subtotal_without_transit,
            user_prefs=user_prefs,
        )

        subtotal = subtotal_without_transit + intercity_transport_cost

        # ── 5. Budget feasibility check and optimisation ──────────────────────
        budget_warning    = None
        budget_infeasible = False

        if budget_amount is not None:
            budget_infeasible, budget_warning, min_viable = self._check_budget_feasibility(
                budget_amount, intercity_transport_cost, num_people, num_days, rooms,
                profile=profile, attraction_cost=attraction_cost,
                driving_mode=driving_mode, car_rental_cost=car_rental_cost,
                fuel_cost=fuel_cost,
            )
            self._last_min_viable = min_viable

            if not budget_infeasible and (subtotal * 1.1) > budget_amount:
                (
                    total_acc, total_food, total_trans,
                    acc_ppd, food_ppd, trans_ppd,
                    subtotal, budget_warning, budget_infeasible
                ) = self._optimize_for_budget(
                    acc_ppd, food_ppd, trans_ppd,
                    total_acc, total_food, total_trans,
                    subtotal,
                    num_people, num_days, rooms,
                    driving_mode,
                    attraction_cost, car_rental_cost, fuel_cost, intercity_transport_cost,
                    budget_amount, budget_strictness, spots,
                    profile=profile,
                )
            elif budget_infeasible and budget_strictness == "strict":
                # Keep the realistic minimum plan, but never pretend it fits.
                (
                    total_acc, total_food, total_trans,
                    acc_ppd, food_ppd, trans_ppd,
                    subtotal, _, _,
                ) = self._optimize_for_budget(
                    acc_ppd, food_ppd, trans_ppd,
                    total_acc, total_food, total_trans,
                    subtotal,
                    num_people, num_days, rooms,
                    driving_mode,
                    attraction_cost, car_rental_cost, fuel_cost, intercity_transport_cost,
                    budget_amount, budget_strictness, spots,
                    profile=profile,
                )

        # ── 6. Miscellaneous buffer (10 % of subtotal) ────────────────────────
        miscellaneous_cost = self._calculate_miscellaneous_cost(subtotal)
        total = subtotal + miscellaneous_cost

        remaining = round(budget_amount - total, 2) if budget_amount is not None else None

        # ── 7. Build calculation breakdown ────────────────────────────────────
        breakdown = self._build_breakdown(
            acc_ppd, food_ppd, trans_ppd,
            rooms, num_people, num_days,
            total_acc, total_food, total_trans,
            attraction_cost, spots,
            car_rental_cost, driving_mode,
            fuel_cost, fuel_km, budget_level,
            intercity_transport_cost, intercity_mode,
            subtotal, miscellaneous_cost, total,
            profile=profile,
        )
        if car_rental_source == "fallback_estimate":
            breakdown["car_rental"] = (
                f"Self-drive car estimated at ₹{rental_daily_estimate:,.0f}/day × {int(num_days)} day(s) "
                f"= ₹{car_rental_cost:,.0f} (indicative estimate — the rental provider was unavailable, "
                "so this is not a quote)."
            )
            breakdown["local_transport"] = (
                breakdown.get("local_transport")
                or "Local transport is covered by the self-drive estimate above (no double count)."
            )

        utilization_percentage = round((total / budget_amount * 100), 1) if budget_amount else 0.0
        if budget_amount is None:
            within_budget = None
        else:
            within_budget = (total <= budget_amount) and not budget_infeasible
        
        return {
            "budget_type":         budget_strictness or "flexible",
            "budget_strictness":   budget_strictness or "flexible",
            "budget_limit":        budget_amount,
            "estimated_total":     round(total, 2),
            "remaining_budget":    remaining,
            "within_budget":       within_budget,
            "transport":           round(intercity_transport_cost, 2),
            "accommodation":       round(total_acc, 2),
            "food":                round(total_food, 2),
            "activities":          round(attraction_cost, 2),
            # `local_transport` is the canonical sum-category and therefore
            # already includes car_rental + fuel.  car_rental/fuel are exposed
            # separately for information only (budget_norm treats them that way),
            # so no consumer should add them again.
            "local_transport":     round(total_trans + car_rental_cost + fuel_cost, 2),
            
            # Additional fields needed by system
            "miscellaneous":       round(miscellaneous_cost, 2),
            "car_rental":          round(car_rental_cost, 2),
            "fuel":                round(fuel_cost, 2),
            "car_rental_source":   car_rental_source,
            "car_rental_estimated": car_rental_source == "fallback_estimate",
            "budget_category":     budget_level,
            "budget_warning":      budget_warning,
            "budget_infeasible":   budget_infeasible,
            "min_viable_cost":     round(getattr(self, "_last_min_viable", 0.0) or 0.0, 2),
            "utilization_percentage": utilization_percentage,
            "rooms":               rooms,
            "days":                num_days,
            "people":              num_people,
            "nights":              num_days,
            "destination_profile": dict(profile),
            "cost_index":          profile["cost_index"],
            "currency":            "INR",
            "currency_assumptions": (
                [self._CURRENCY_ASSUMPTION_NOTE]
                + ([profile["assumption"]] if profile.get("assumption") else [])
            ),
            "breakdown":           breakdown,
        }

    # ═════════════════════════════════════════════════════════════════════════
    # PRIVATE COMPONENT CALCULATORS
    # ═════════════════════════════════════════════════════════════════════════

    def _resolve_budget_level(self, user_prefs: dict, cost_index: float = 1.0) -> tuple:
        """
        Determine budget level, numeric budget amount (or None), and strictness.

        The budget *level* must reflect how much each traveller actually gets to
        spend per day, NOT the absolute pot. A ₹150,000 pot is luxury for one
        person for a weekend but a backpacker budget for a group of 10 over 10
        days. We therefore derive an effective per-person-per-day figure when the
        group size and duration are known and classify on that:

            <  ₹2,500 / person / day  -> low      (India)
            <  ₹6,000 / person / day  -> medium   (India)
            >= ₹6,000 / person / day  -> high     (India)

        ``cost_index`` scales those thresholds for international destinations
        (e.g. Japan ≈ 3.2×), so ₹4,000/person/day in Japan is correctly
        classified as a low budget rather than a comfortable medium one.

        When people/days are unknown we fall back to sensible absolute totals
        (<=₹30k low, <=₹1L medium, else high). A string budget ("luxury",
        "backpacker", ...) is honoured when no amount is present.

        Returns (budget_level: str, budget_amount: int | None, strictness: str | None)
        """
        raw_budget        = user_prefs.get("budget", "medium")
        budget_amount     = user_prefs.get("budget_amount")
        budget_strictness = user_prefs.get("budget_strictness")

        if isinstance(budget_strictness, str):
            budget_strictness = budget_strictness.strip().lower()
            if budget_strictness not in ("strict", "flexible"):
                budget_strictness = None

        if budget_amount is not None:
            try:
                budget_amount = int(
                    str(budget_amount).replace(",", "").replace("₹", "").strip()
                )
            except (ValueError, TypeError):
                budget_amount = None

        try:
            index = max(0.5, float(cost_index or 1.0))
        except (TypeError, ValueError):
            index = 1.0
        low_threshold = 2_500 * index
        medium_threshold = 6_000 * index

        if budget_amount is not None:
            # Derive effective per-person-per-day spend when dimensions are known.
            try:
                num_people = max(1, int(user_prefs.get("people", 1) or 1))
            except (ValueError, TypeError):
                num_people = 1
            try:
                num_days = max(1, int(user_prefs.get("days", 1) or 1))
            except (ValueError, TypeError):
                num_days = 1

            per_person_per_day = budget_amount / (num_people * num_days)
            # Only trust the per-day model when the trip dimensions are actually
            # meaningful (a real group/length); otherwise use absolute totals.
            if (user_prefs.get("people") or user_prefs.get("days")) and per_person_per_day > 0:
                if per_person_per_day < low_threshold:
                    budget_level = "low"
                elif per_person_per_day < medium_threshold:
                    budget_level = "medium"
                else:
                    budget_level = "high"
            elif budget_amount <= 30_000 * index:
                budget_level = "low"
            elif budget_amount <= 1_00_000 * index:
                budget_level = "medium"
            else:
                budget_level = "high"
        elif isinstance(raw_budget, str):
            bl = raw_budget.lower()
            if any(k in bl for k in ("low", "cheap", "backpacker", "budget", "economy")):
                budget_level = "low"
            elif any(k in bl for k in ("high", "luxury", "expensive", "premium", "lavish")):
                budget_level = "high"
            else:
                budget_level = "medium"
        else:
            budget_level = "medium"

        return budget_level, budget_amount, budget_strictness

    # ─────────────────────────────────────────────────────────────────────────

    def _get_city_tier(self, city: str) -> int:
        """
        Return the pricing tier (1 / 2 / 3) for the given city name.
        Defaults to Tier 2 (mid-sized city) for unknown destinations.
        """
        key = city.lower().strip() if city else ""
        return self._CITY_TIERS.get(key, 2)  # default: mid-tier

    # ─────────────────────────────────────────────────────────────────────────

    def _destination_profile(self, user_prefs: dict, city_name: str,
                             spots: list | None = None) -> dict:
        """Return the destination's cost model (national vs international).

        The old code applied Indian Tier-2 prices to every destination, which
        made a Japan trip look affordable on an Indian budget.  This helper
        builds a transparent profile:

        * national (India)  → existing tier logic, cost_index 1.0
        * international     → country index applied to the Indian baseline
        * unknown country   → explicit assumption flag, never silently cheap

        No POI, price or availability is fabricated here; only the multiplier
        used by the auditable baseline is chosen.
        """
        scope = user_prefs.get("destination_scope") if isinstance(user_prefs, dict) else {}
        scope = scope if isinstance(scope, dict) else {}
        country = str(scope.get("country") or "").strip()
        if not country:
            for spot in spots or []:
                candidate = str((spot or {}).get("country") or "").strip()
                if candidate:
                    country = candidate
                    break
        if not country:
            country = str(user_prefs.get("country") or "").strip()

        country_key = country.lower().strip()
        scope_says_intl = bool(scope.get("is_international"))
        is_international = bool(user_prefs.get("is_international")) or scope_says_intl
        if country and country_key not in ("india", "in", "bharat"):
            # A named foreign country is authoritative even if a legacy flag is
            # missing; this prevents Indian tier pricing from leaking in.
            if country_key in self._COUNTRY_COST_INDEX:
                is_international = True

        assumption = None
        cost_index = 1.0
        if is_international:
            cost_index = self._COUNTRY_COST_INDEX.get(country_key)
            if cost_index is None:
                cost_index = self._INTERNATIONAL_DEFAULT_INDEX
                assumption = (
                    f"No verified cost profile for '{country or city_name}'. "
                    f"Using a conservative {cost_index:.1f}× international index "
                    "applied to the Indian Tier-1 baseline."
                )
            entry_multiplier = self._INTERNATIONAL_ENTRY_MULTIPLIER
            fuel_rate = self._INTERNATIONAL_FUEL_INR_PER_LITRE
            tier = 1
        else:
            tier = self._get_city_tier(city_name)
            entry_multiplier = 1.0
            fuel_rate = self._PETROL_INR_PER_LITRE

        return {
            "is_international": bool(is_international),
            "country": country or ("India" if not is_international else ""),
            "country_key": country_key,
            "tier": tier,
            "cost_index": float(cost_index),
            "entry_fee_multiplier": float(entry_multiplier),
            "fuel_inr_per_litre": float(fuel_rate),
            "assumption": assumption,
        }

    # ─────────────────────────────────────────────────────────────────────────

    def _get_per_day_costs(self, budget_level: str, city: str,
                           profile: dict | None = None) -> tuple:
        """
        Return realistic per-day costs in INR for accommodation, food, and
        local transport, adjusted for the destination city's price tier.

        Strategy:
        ---------
        1. Start with the destination-aware STATIC BASE COST (primary source).
        2. Optionally refine with an LLM call (secondary, clamped to caps × tier).
        3. Always clamp the final values to the tier-adjusted cap range.

        This approach is more predictable than pure-LLM estimation and avoids
        the tendency for LLMs to return values near the top of the range.

        Returns (acc_ppd, food_ppd, trans_ppd) — all in INR
        """
        profile = profile or {}
        tier       = self._get_city_tier(city) if not profile.get("is_international") else 1
        multiplier = self._TIER_MULTIPLIER.get(tier, 0.80) * float(profile.get("cost_index", 1.0))
        base       = self._BASE_COSTS.get(budget_level, self._BASE_COSTS["medium"])
        caps       = self._COST_CAPS.get(budget_level, self._COST_CAPS["medium"])

        # Tier-adjusted caps
        tier_acc_min   = caps["acc_min"]   * multiplier
        tier_acc_max   = caps["acc_max"]   * multiplier
        tier_food_min  = caps["food_min"]  * multiplier
        tier_food_max  = caps["food_max"]  * multiplier
        tier_trans_min = caps["trans_min"] * multiplier
        tier_trans_max = caps["trans_max"] * multiplier

        # Tier-adjusted static defaults (primary)
        default_acc   = base["acc"]   * multiplier
        default_food  = base["food"]  * multiplier
        default_trans = base["trans"] * multiplier

        # Attempt LLM refinement (secondary). For international destinations the
        # static country index is authoritative: the legacy prompt described
        # Indian market rates, which would drag a Japan estimate back down.
        if profile.get("is_international"):
            acc_ppd, food_ppd, trans_ppd = default_acc, default_food, default_trans
        else:
            acc_ppd, food_ppd, trans_ppd = self._refine_with_llm(
                budget_level, city, tier,
                default_acc, default_food, default_trans,
                tier_acc_min, tier_acc_max,
                tier_food_min, tier_food_max,
                tier_trans_min, tier_trans_max,
            )

        # Final clamp to tier-adjusted range
        return (
            self._clamp(acc_ppd,   tier_acc_min,   tier_acc_max),
            self._clamp(food_ppd,  tier_food_min,  tier_food_max),
            self._clamp(trans_ppd, tier_trans_min, tier_trans_max),
        )

    # ─────────────────────────────────────────────────────────────────────────

    def _refine_with_llm(
        self,
        budget_level: str, city: str, tier: int,
        default_acc: float, default_food: float, default_trans: float,
        acc_min: float, acc_max: float,
        food_min: float, food_max: float,
        trans_min: float, trans_max: float,
    ) -> tuple:
        """
        Optionally refine the static defaults using a lightweight LLM call.

        The prompt asks for AVERAGE market rates (not the upper end of the range),
        explicitly discouraging luxury estimates unless the budget_level is "high".
        Falls back silently to the static defaults on any error.

        Returns (acc_ppd, food_ppd, trans_ppd)
        """
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import HumanMessage
            from pydantic import BaseModel, Field

            class CityCosts(BaseModel):
                accommodation: float = Field(description="Cost per room per night")
                food: float = Field(description="Total food cost per person per day")
                transport: float = Field(description="Local city transport per person per day")

            llm = ChatGoogleGenerativeAI(model="gemini-flash-lite-latest", temperature=0.1)
            structured_llm = llm.with_structured_output(CityCosts)

            luxury_note = "" if budget_level == "high" else (
                " Do NOT use luxury hotel or fine-dining prices. "
                "Use the AVERAGE (not maximum) realistic price for this budget category."
            )

            prompt = (
                f"You are a travel cost expert for India.\n"
                f"Estimate realistic AVERAGE daily costs in INR for a '{budget_level}' budget traveller "
                f"visiting {city}.\n"
                f"{luxury_note}\n"
                f"accommodation: cost per ROOM per NIGHT for a typical "
                f"{'budget guesthouse/hostel' if budget_level=='low' else '3-star hotel' if budget_level=='medium' else '4-star hotel'} "
                f"in {city}. Realistic range: INR {int(acc_min)}–{int(acc_max)}.\n"
                f"food: total food cost per PERSON per DAY (breakfast + lunch + dinner) at "
                f"{'dhabas/local eateries' if budget_level=='low' else 'mid-range restaurants' if budget_level=='medium' else 'good restaurants'} "
                f"in {city}. Realistic range: INR {int(food_min)}–{int(food_max)}.\n"
                f"transport: local city transport per PERSON per DAY "
                f"(autos/metro/cabs within {city} only, NOT intercity). "
                f"Realistic range: INR {int(trans_min)}–{int(trans_max)}.\n"
            )

            try:
                resp = structured_llm.invoke([HumanMessage(content=prompt)])
                raw_acc = float(resp.accommodation)
                raw_food = float(resp.food)
                raw_trans = float(resp.transport)
            except Exception as e:
                print(f"[BudgetAgent WARN] Structured LLM cost failed: {e}. Retrying...")
                resp = structured_llm.invoke([HumanMessage(content=prompt)])
                raw_acc = float(resp.accommodation)
                raw_food = float(resp.food)
                raw_trans = float(resp.transport)

            print(f"[BudgetAgent] LLM costs for {city} ({budget_level}): "
                  f"acc=₹{raw_acc:.0f}, food=₹{raw_food:.0f}, trans=₹{raw_trans:.0f}")
            return raw_acc, raw_food, raw_trans

        except Exception as ex:
            print(f"[BudgetAgent WARN] LLM refinement skipped ({ex}); using static defaults.")
            return default_acc, default_food, default_trans

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_accommodation_cost(
        self, acc_ppd: float, rooms: int, num_days: int
    ) -> float:
        """
        Total accommodation cost.

        Formula: acc_ppd (per room per night) × rooms × nights
        Rooms   = ceil(num_people / 2) — 2 pax per room sharing
        """
        return acc_ppd * rooms * num_days

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_food_cost(
        self, food_ppd: float, num_people: int, num_days: int
    ) -> float:
        """
        Total food cost.

        Formula: food_ppd (per person per day) × num_people × num_days
        """
        return food_ppd * num_people * num_days

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_local_transport_cost(
        self,
        trans_ppd: float,
        num_people: int,
        num_days: int,
        should_rent_car: bool,
    ) -> float:
        """
        Total local transport cost within the destination city.

        Formula (no car): trans_ppd × num_people × num_days
        Formula (car):    0  (fuel_cost covers local driving instead)
        """
        if should_rent_car:
            return 0.0
        return trans_ppd * num_people * num_days

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_entry_tier(spot: dict) -> int | None:
        """
        Resolve a spot's entry-fee tier.

        Raw POIs carry ``price_level``, but the POI enrichment layer
        (``information_agent._enrich_single``) re-emits it as ``price``.
        Check both before giving up, so a known tier is never silently
        replaced by the ₹150 fallback.
        """
        for key in ("price_level", "price"):
            val = spot.get(key)
            if val is None:
                continue
            try:
                return int(val)
            except (TypeError, ValueError):
                return None
        return None

    def _calculate_attraction_cost(self, spots: list, num_people: int,
                                   profile: dict | None = None) -> float:
        """
        Total attraction entry-fee cost.

        Formula: Σ entry_fee(tier) × num_people

        The tier is read from ``price_level`` or the legacy ``price`` field
        (see ``_resolve_entry_tier``); unknown tiers use the default fee.

        Entry fee map (INR per person, realistic Indian tourist prices):
            0 = free       (parks, beaches, public squares)
            1 = ₹50        (small temples, minor heritage sites)
            2 = ₹200       (state museums, mid-range monuments)
            3 = ₹400       (ASI monuments, wildlife reserves)
            4 = ₹750       (Taj Mahal, major national parks)

        For international destinations the same tiers are scaled by
        ``profile["entry_fee_multiplier"]`` and the scale is stated in the
        breakdown, so a Japan temple is not priced at an Indian ₹50.
        """
        multiplier = float((profile or {}).get("entry_fee_multiplier", 1.0) or 1.0)
        total = 0.0
        for spot in spots or []:
            tier        = self._resolve_entry_tier(spot)
            per_person  = self._ENTRY_FEE_MAP.get(tier, self._ENTRY_FEE_DEFAULT)
            total      += per_person * multiplier * num_people
        return round(total, 2)

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_car_rental_cost(
        self,
        car_info: list | None,
        budget_level: str,
        num_days: int,
        profile: dict | None = None,
    ) -> tuple:
        """
        Total car rental cost.

        Methodology:
        -----------
        1. Always derive a per-DAY rate first, then multiply by num_days.
           This prevents API totals from being mistakenly used as daily rates.
        2. Per-day rate from car_info is normalised:
           - If price_inr >= (default_daily × 2 × num_days): too high, ignore API price
           - If price_inr / num_days is within realistic daily range: treat as TOTAL
           - If price_inr is within realistic daily range: treat as DAILY RATE
        3. Final daily rate is hard-capped at _RENTAL_DAILY_MAX[budget_level].

        Formula: per_day_inr × num_days

        Returns (total_cost: float, daily_rate_used: float)
        """
        from utils import ask_openai, extract_number

        default_daily = float(self._RENTAL_DAILY_INR.get(budget_level, 2_500))
        cost_index = float((profile or {}).get("cost_index", 1.0) or 1.0)
        if (profile or {}).get("is_international"):
            default_daily *= cost_index
        daily_max     = float(self._RENTAL_DAILY_MAX.get(budget_level, 3_500)) * max(1.0, cost_index if (profile or {}).get("is_international") else 1.0)

        if not car_info:
            return 0.0, 0.0

        # LLM picks the most suitable car for this budget
        idx = 0
        try:
            ai_response = ask_openai(
                prompt=(
                    f"Car rental options: {car_info}. Budget level: {budget_level}. "
                    "Pick the most budget-appropriate car. Return ONLY the 1-based index number."
                )
            )
            if ai_response and "answer" in ai_response:
                extracted = extract_number(ai_response.get("answer", ""))
                if extracted is not None:
                    rec = int(extracted)
                    if 1 <= rec <= len(car_info):
                        idx = rec - 1
        except Exception as e:
            print(f"[BudgetAgent WARN] Car selection LLM failed ({e}); using first option.")

        raw_price = car_info[idx].get("price", 0)
        currency  = str(car_info[idx].get("currency") or "").upper().strip()

        if raw_price and float(raw_price) > 0:
            price_val = float(raw_price)

            # Convert foreign currency to INR with a documented static rate.
            # The old code treated EVERY non-INR currency as USD, so a JPY or
            # EUR quote became a wildly wrong rupee amount.
            if currency not in ("INR", "RS", "RS.", "₹", ""):
                rate = self._CURRENCY_TO_INR.get(currency)
                if rate is None:
                    print(f"[BUDGET][CAR_RENTAL][FX] Unknown currency '{currency}'; "
                          "using the default daily estimate instead of assuming USD.")
                    daily = min(default_daily, daily_max)
                    return round(daily * num_days, 2), round(daily, 2)
                price_val *= rate
                print(f"[BUDGET][CAR_RENTAL][FX] {currency}→INR at static "
                      f"{rate:.4g} ({self._CURRENCY_ASSUMPTION_NOTE})")

            # Determine whether this is a per-day or total price
            # Heuristic: if price_val / num_days falls in a plausible daily range (₹500–₹8,000),
            #            treat price_val as a TOTAL; otherwise treat as a daily rate
            as_daily_if_total = price_val / max(num_days, 1)
            if 500 <= as_daily_if_total <= 8_000:
                # price_val is a total for the trip
                per_day = as_daily_if_total
            elif 500 <= price_val <= 8_000:
                # price_val is already a per-day rate
                per_day = price_val
            else:
                # Fallback: use the default daily rate
                per_day = default_daily

            # Hard cap
            per_day = min(per_day, daily_max)
            return round(per_day * num_days, 2), round(per_day, 2)

        daily = min(default_daily, daily_max)
        return daily * num_days, daily

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_fuel_cost(
        self,
        spots: list,
        budget_level: str,
        num_days: int,
        fuel_inr_per_litre: float | None = None,
    ) -> tuple:
        """
        Total fuel cost for local driving when the user rents a car.

        Methodology:
        -----------
        1. Compute the haversine (straight-line) distance between consecutive
           attractions in the list, then apply a road-factor of 1.3×.
        2. Divide by num_days to get the average daily driving distance.
        3. Cap the daily distance at _MAX_DAILY_KM (60 km) so that
           inter-city haversine distances (already covered by intercity_transport)
           are NOT double-counted in the fuel budget.
        4. Apply a floor of _BASE_DAILY_KM (30 km/day) for realistic local driving.
        5. Multiply by the vehicle's fuel consumption and petrol price.

        Formula:
            daily_km     = clamp(haversine_total × 1.3 / days, BASE_DAILY, MAX_DAILY)
            total_km     = daily_km × num_days
            litres       = total_km × L_per_100km / 100
            fuel_cost    = litres × ₹110/L

        Returns (fuel_cost: float, total_km_used: float)
        """
        consumption = self._CONSUMPTION_L_PER_100KM.get(budget_level, 8.0)

        # Haversine sum across consecutive spots
        raw_haversine_km = 0.0
        if spots and len(spots) > 1:
            for i in range(len(spots) - 1):
                raw_haversine_km += self._haversine_distance(spots[i], spots[i + 1])

        # Road distance ≈ 1.3× straight-line; derive per-day average
        road_km          = raw_haversine_km * 1.3
        per_day_km       = road_km / max(num_days, 1)

        # Clamp between floor and ceiling to avoid inter-city double-counting
        capped_daily_km  = self._clamp(per_day_km, self._BASE_DAILY_KM, self._MAX_DAILY_KM)
        total_km         = capped_daily_km * num_days

        litres_consumed  = total_km * consumption / 100.0
        fuel_rate = float(fuel_inr_per_litre or self._PETROL_INR_PER_LITRE)
        fuel_cost        = round(litres_consumed * fuel_rate, 2)

        return fuel_cost, round(total_km, 1)

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_intercity_transport_cost(
        self,
        transit_options: dict | None,
        budget_level: str,
        num_people: int,
        budget_amount: int | None = None,
        subtotal: float = 0.0,
        user_prefs: dict | None = None,
    ) -> tuple:
        """Round-trip intercity transport cost (origin → destination → origin).

        Cost basis
        ----------
        A fare is either per person, per vehicle or per group.  The old code
        multiplied every mode by ``num_people``, so one ₹4,000 taxi became
        ₹8,000 for two travellers and ₹16,000 for four.  We now prefer the
        already-normalised ``group_cost`` (one way, whole party) and multiply it
        by two only for the return leg.  ``per_vehicle``/``per_group`` prices are
        therefore no longer multiplied by the party size.

        Mode choice
        -----------
        1. An explicit user/UI transport preference wins.
        2. Otherwise the TransitAgent's recommended mode is used when available.
        3. Otherwise trains (the economical default) if present, else the
           cheapest priced option.

        Returns (cost: float, mode_description: str)
        """
        if not transit_options or not isinstance(transit_options, dict):
            return 0.0, "N/A"
        if transit_options.get("same_city"):
            return 0.0, "N/A"

        raw_options = transit_options.get("intercity_transport") or []
        if not isinstance(raw_options, list):
            return 0.0, "N/A"

        # Explicit user choice must never be overridden by the LLM's
        # recommendation or by the cheapest-fare heuristic.
        prefs = user_prefs or {}
        explicit_choice = str(
            prefs.get("transport_preference")
            or prefs.get("selected_transport")
            or transit_options.get("selected_mode")
            or transit_options.get("user_selected_mode")
            or ""
        ).strip().lower()
        recommended_mode = str(transit_options.get("recommended_mode") or "").strip().lower()

        options_list = []
        for o in raw_options:
            if not isinstance(o, dict):
                continue
            o = dict(o)
            currency = str(o.get("currency") or transit_options.get("currency") or "INR").strip().upper()
            inr = currency in ("INR", "\u20b9", "RS", "RS.", "RUPEE", "RUPEES")
            if not inr:
                o["price_inr"] = None
                options_list.append(o)
                continue

            price = o.get("price_inr")
            if price is None:
                price = o.get("estimated_cost")
            try:
                price = float(price) if price not in (None, "") else None
            except (TypeError, ValueError):
                price = None
            o["price_inr"] = price if price and price > 0 else None

            # One-way cost for the whole party.
            group = o.get("group_cost")
            try:
                group = float(group) if group not in (None, "") else None
            except (TypeError, ValueError):
                group = None
            basis = str(o.get("cost_basis") or "").strip().lower()
            if group is None and o["price_inr"] is not None:
                if basis in ("per_person", ""):
                    group = o["price_inr"] * max(1, num_people)
                else:  # per_vehicle / per_group / per_day / per_trip
                    group = o["price_inr"]
            o["_one_way_group_inr"] = group if (group and group > 0) else None
            options_list.append(o)

        priced = [o for o in options_list if o.get("_one_way_group_inr")]
        if not priced:
            return 0.0, "N/A"

        def _mode_of(option):
            return str(option.get("mode") or "").strip().lower()

        def _pick(mode_name):
            matches = [o for o in priced if _mode_of(o) == mode_name]
            return sorted(matches, key=lambda x: float(x["_one_way_group_inr"])) if matches else []

        def _round_trip(option):
            return round(float(option["_one_way_group_inr"]) * 2, 2)

        preferred_modes = []
        for candidate in (explicit_choice, recommended_mode):
            for token in ("flight", "train", "bus", "taxi", "car", "ferry"):
                if token in candidate and token not in preferred_modes:
                    preferred_modes.append(token)
        if not preferred_modes:
            preferred_modes = ["train"] if _pick("train") else []

        chosen = None
        mode_label = "transport"
        for mode_name in preferred_modes:
            matches = _pick(mode_name)
            if not matches:
                continue
            option = matches[0]
            cost = _round_trip(option)
            # An explicit choice is respected even if it exceeds the budget; the
            # feasibility warning then explains the overrun. Only the *implicit*
            # recommendation is allowed to fall back to an affordable mode.
            if explicit_choice:
                chosen, mode_label = option, mode_name
                break
            if budget_amount is None or (subtotal + cost) * 1.1 <= budget_amount:
                chosen, mode_label = option, mode_name
                break

        if chosen is None:
            # Cheapest option across every mode (still respecting the fare basis).
            cheapest = sorted(priced, key=lambda x: float(x["_one_way_group_inr"]))[0]
            chosen, mode_label = cheapest, _mode_of(cheapest) or "transport"

        return _round_trip(chosen), mode_label

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_miscellaneous_cost(self, subtotal: float) -> float:
        """
        Miscellaneous / emergency buffer = 10 % of the subtotal.
        Covers shopping, tips, unforeseen local expenses, etc.
        """
        return round(subtotal * 0.10, 2)

    # ─────────────────────────────────────────────────────────────────────────

    def _check_budget_feasibility(
        self,
        budget_amount: int,
        intercity_transport_cost: float,
        num_people: int,
        num_days: int,
        rooms: int,
        profile: dict | None = None,
        attraction_cost: float = 0.0,
        driving_mode: bool = False,
        car_rental_cost: float = 0.0,
        fuel_cost: float = 0.0,
    ) -> tuple:
        """Compare the stated budget with a realistic minimum viable cost.

        The previous version declared a trip feasible whenever the budget was at
        least 65 % of an Indian-priced minimum, then the optimiser could shave
        variable costs until the displayed total "fit".  A strict ₹40,000 Japan
        budget then appeared satisfied by an unrealistically cheap plan.

        Now the minimum uses the *destination's* low-tier floor (country index
        included) plus the selected paid attractions, rental/fuel and intercity
        fare.  If the budget is below that floor, no optimisation can make the
        trip feasible and we say so explicitly.

        Returns ``(is_infeasible, warning, minimum_viable_cost)``.
        """
        caps = self._COST_CAPS["low"]
        index = float((profile or {}).get("cost_index", 1.0) or 1.0)
        is_intl = bool((profile or {}).get("is_international"))

        min_acc = caps["acc_min"] * (index if is_intl else 1.0) * rooms * num_days
        min_food = caps["food_min"] * (index if is_intl else 1.0) * num_people * num_days
        if driving_mode:
            min_local = float(car_rental_cost or 0.0) + float(fuel_cost or 0.0)
        else:
            min_local = caps["trans_min"] * (index if is_intl else 1.0) * num_people * num_days

        min_viable = (
            min_acc + min_food + min_local
            + float(attraction_cost or 0.0) + float(intercity_transport_cost or 0.0)
        )

        if budget_amount < min_viable:
            shortfall = min_viable - budget_amount
            warning = (
                f"Your budget of ₹{budget_amount:,} does not cover the realistic minimum for "
                f"a {num_days}-day trip for {num_people} person(s)"
                + (f" to {profile.get('country')}" if profile and profile.get("country") else "")
                + f" — the minimum is ₹{int(round(min_viable)):,}, a shortfall of "
                f"₹{int(round(shortfall)):,}. "
                "This estimate uses the destination's low-cost floor; transport, accommodation "
                "and food are not free. Please increase the budget, reduce the number of days, "
                "choose fewer paid attractions, or travel in a cheaper season."
            )
            return True, warning, min_viable

        return False, None, min_viable

    # ─────────────────────────────────────────────────────────────────────────

    def _optimize_for_budget(
        self,
        acc_ppd: float,
        food_ppd: float,
        trans_ppd: float,
        total_acc: float,
        total_food: float,
        total_trans: float,
        current_total: float,
        num_people: int,
        num_days: int,
        rooms: int,
        should_rent_car: bool,
        attraction_cost: float,
        car_rental_cost: float,
        fuel_cost: float,
        intercity_transport_cost: float,
        budget_amount: int,
        budget_strictness: str | None,
        spots: list = None,
        profile: dict | None = None,
    ) -> tuple:
        """
        Iteratively reduce variable costs until the total fits the budget.

        Up to 8 passes; each pass applies:
            accommodation : × 0.80  (floor = low.acc_min)
            food          : × 0.85  (floor = low.food_min)
            transport     : × 0.85  (floor = low.trans_min)

        This means the optimiser can reduce accommodation by up to ~83% and
        food/transport by up to ~73% over 8 passes, before giving up.

        Returns (total_acc, total_food, total_trans,
                 acc_ppd, food_ppd, trans_ppd,
                 new_total, budget_warning)
        """
        caps    = self._COST_CAPS["low"]
        index   = float((profile or {}).get("cost_index", 1.0) or 1.0)
        if (profile or {}).get("is_international"):
            caps = {k: v * index for k, v in caps.items()}
        sa, sf, st = acc_ppd, food_ppd, trans_ppd

        new_total                  = current_total
        final_acc = final_food = final_trans = 0.0

        for _ in range(8):
            sa = max(sa * 0.80, caps["acc_min"])
            sf = max(sf * 0.85, caps["food_min"])
            st = max(st * 0.85, caps["trans_min"])

            new_acc   = sa * rooms      * num_days
            new_food  = sf * num_people * num_days
            new_trans = 0.0 if should_rent_car else st * num_people * num_days

            new_total = (
                new_acc + new_food + new_trans
                + attraction_cost + car_rental_cost + fuel_cost
                + intercity_transport_cost
            )

            final_acc, final_food, final_trans = new_acc, new_food, new_trans

            if (new_total * 1.1) <= budget_amount:
                break

        budget_infeasible = False
        
        if (new_total * 1.1) > budget_amount:
            excess = (new_total * 1.1) - budget_amount
            if budget_strictness == "strict":
                budget_infeasible = True
                
                expensive_attractions = []
                if spots and attraction_cost > 0:
                    expensive_attractions = [
                        s.get('name') for s in spots
                        if (self._resolve_entry_tier(s) or 0) >= 2
                    ]
                
                if expensive_attractions:
                    conflict_reason = f"User-selected paid attractions ({', '.join(expensive_attractions)})"
                elif should_rent_car and (car_rental_cost + fuel_cost > new_trans):
                    conflict_reason = "Opting for a rental car instead of local transport"
                else:
                    conflict_reason = f"Minimum cost of transport, accommodation, and food for {num_people} people over {num_days} days"

                budget_warning = (
                    f"Your requested trip cannot currently fit within the strict budget of ₹{budget_amount:,}. "
                    f"The exact excess is ₹{int(excess):,}. "
                    f"The closest feasible configuration is ₹{int(new_total * 1.1):,}. "
                    f"Conflict caused by: {conflict_reason}. "
                    "Please reduce travel days, choose fewer paid attractions, or select cheaper transport to fit within your strict budget."
                )
            else:
                budget_warning = (
                    f"Estimated trip cost (₹{int(new_total * 1.1):,}) is above your "
                    f"budget of ₹{budget_amount:,}. We've minimised costs where possible."
                )
        else:
            budget_warning = (
                f"Itinerary optimised to fit within your budget of ₹{budget_amount:,}. "
                "Budget-friendly accommodation, dining, and transport options selected."
            )

        return (
            round(final_acc,   2), round(final_food, 2), round(final_trans, 2),
            sa, sf, st,
            round(new_total,   2), budget_warning, budget_infeasible
        )

    # ─────────────────────────────────────────────────────────────────────────

    def _build_breakdown(
        self,
        acc_ppd: float, food_ppd: float, trans_ppd: float,
        rooms: int, num_people: int, num_days: int,
        total_acc: float, total_food: float, total_trans: float,
        attraction_cost: float, spots: list,
        car_rental_cost: float, should_rent_car: bool,
        fuel_cost: float, fuel_km: float, budget_level: str,
        intercity_transport_cost: float, intercity_mode: str,
        subtotal: float, miscellaneous_cost: float, total: float,
        profile: dict | None = None,
    ) -> dict:
        """
        Build human-readable calculation strings for every budget component.
        These strings make estimates easy to validate and debug.
        """
        consumption = self._CONSUMPTION_L_PER_100KM.get(budget_level, 8.0)
        mileage_kml = round(100.0 / consumption, 1)  # convert L/100km → km/L
        profile = profile or {}
        fuel_rate = float(profile.get("fuel_inr_per_litre", self._PETROL_INR_PER_LITRE) or self._PETROL_INR_PER_LITRE)
        entry_multiplier = float(profile.get("entry_fee_multiplier", 1.0) or 1.0)
        basis_note = ""
        if profile.get("is_international"):
            basis_note = (
                f"International basis: {profile.get('country') or 'destination'} "
                f"index ×{float(profile.get('cost_index', 1.0)):.2f} applied to the INR baseline; "
                f"entry fees ×{entry_multiplier:.1f}."
            )
            if profile.get("assumption"):
                basis_note += " " + str(profile["assumption"])

        acc_line = (
            f"₹{acc_ppd:,.0f}/room/night × {rooms} room(s) × {num_days} nights"
            f" = ₹{total_acc:,.0f}"
        )
        food_line = (
            f"₹{food_ppd:,.0f}/person/day × {num_people} person(s) × {num_days} days"
            f" = ₹{total_food:,.0f}"
        )
        trans_line = (
            "₹0 (car rented — see fuel)"
            if should_rent_car else
            f"₹{trans_ppd:,.0f}/person/day × {num_people} person(s) × {num_days} days"
            f" = ₹{total_trans:,.0f}"
        )
        n_spots = len(spots)
        avg_fee = round(attraction_cost / max(num_people, 1) / max(n_spots, 1)) if n_spots else 0
        attractions_line = (
            f"{n_spots} attraction(s) × avg ₹{avg_fee}/person × {num_people} person(s)"
            f" = ₹{attraction_cost:,.0f}"
            if n_spots else f"No attractions — ₹0"
        )
        if should_rent_car:
            daily_rate = round(car_rental_cost / max(num_days, 1))
            car_line   = (
                f"₹{daily_rate:,}/day × {num_days} days = ₹{car_rental_cost:,.0f}"
            )
            fuel_line  = (
                f"{fuel_km:.0f} km ÷ {mileage_kml} km/L × ₹{fuel_rate:.0f}/L"
                f" = ₹{fuel_cost:,.0f}"
            )
        else:
            car_line  = "No car rental"
            fuel_line = "No fuel cost (no car rental)"

        if intercity_transport_cost > 0:
            one_way_group = round(intercity_transport_cost / 2)
            transit_ln = (
                f"₹{one_way_group:,} ({intercity_mode}) for the party, one way × 2 "
                f"(round-trip) = ₹{intercity_transport_cost:,.0f} "
                f"[{num_people} traveller(s); per-vehicle fares are not multiplied by party size]"
            )
        else:
            transit_ln = "Not applicable (same city or no priced quote) — ₹0"

        misc_line = (
            f"10% of ₹{subtotal:,.0f} subtotal = ₹{miscellaneous_cost:,.0f}"
        )
        total_line = (
            f"₹{subtotal:,.0f} (subtotal) + ₹{miscellaneous_cost:,.0f} (misc)"
            f" = ₹{total:,.0f}"
        )

        return {
            "accommodation":       acc_line,
            "food":                food_line,
            "local_transport":     trans_line,
            "attractions":         attractions_line,
            "car_rental":          car_line,
            "fuel":                fuel_line,
            "intercity_transport": transit_ln,
            "miscellaneous":       misc_line,
            "total":               total_line,
            "cost_basis":          basis_note or "Indian domestic pricing (city tier applied to the INR baseline).",
        }

    # ═════════════════════════════════════════════════════════════════════════
    # PRIVATE GEOMETRY HELPERS
    # ═════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _haversine_distance(spot1: dict, spot2: dict) -> float:
        """
        Great-circle distance in km between two spots (Haversine formula).
        Falls back to 1.0 km if location data is missing.
        """
        loc1 = spot1.get("location") if spot1 else None
        loc2 = spot2.get("location") if spot2 else None

        if not loc1 or not loc2:
            return 1.0

        lat1 = loc1.get("lat", 0)
        lon1 = loc1.get("lng", 0)
        lat2 = loc2.get("lat", 0)
        lon2 = loc2.get("lng", 0)

        R = 6_371  # Earth radius in km
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        """Return ``value`` clamped to [lo, hi]."""
        return max(lo, min(hi, value))
