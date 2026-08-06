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
        # ── 1. Resolve budget metadata ────────────────────────────────────────
        budget_level, budget_amount, budget_strictness = self._resolve_budget_level(user_prefs)

        # ── 2. Trip dimensions ────────────────────────────────────────────────
        num_people = max(1, int(user_prefs.get("people", 1)))
        num_days   = max(1, int(user_prefs.get("days", 1)))
        rooms      = max(1, (num_people + 1) // 2)   # 2 pax per room, rounded up
        city_name  = user_prefs.get("city", "") or ""
        if not city_name or city_name.lower() == "any":
            city_name = spots[0].get("city", "India") if spots else "India"

        # ── 3. Destination-aware per-day costs (static-primary, LLM-optional) ─
        acc_ppd, food_ppd, trans_ppd = self._get_per_day_costs(budget_level, city_name)

        # ── 4. Calculate each budget component ────────────────────────────────
        total_acc   = self._calculate_accommodation_cost(acc_ppd, rooms, num_days)
        total_food  = self._calculate_food_cost(food_ppd, num_people, num_days)
        total_trans = self._calculate_local_transport_cost(trans_ppd, num_people, num_days, should_rent_car)

        attraction_cost = self._calculate_attraction_cost(spots, num_people)

        car_rental_cost = 0.0
        fuel_cost       = 0.0
        fuel_km         = 0.0
        if should_rent_car:
            car_rental_cost, daily_car_rate = self._calculate_car_rental_cost(
                car_info, budget_level, num_days
            )
            fuel_cost, fuel_km = self._calculate_fuel_cost(spots, budget_level, num_days)
            total_trans = 0.0   # local transport replaced by fuel when driving

        intercity_transport_cost, intercity_mode = self._calculate_intercity_transport_cost(
            transit_options, budget_level, num_people
        )

        subtotal = (
            total_acc + total_food + total_trans
            + attraction_cost + car_rental_cost + fuel_cost
            + intercity_transport_cost
        )

        # ── 5. Budget feasibility check and optimisation ──────────────────────
        budget_warning    = None
        budget_infeasible = False

        if budget_amount is not None:
            budget_infeasible, budget_warning = self._check_budget_feasibility(
                budget_amount, intercity_transport_cost, num_people, num_days, rooms
            )

            if not budget_infeasible and subtotal > budget_amount:
                (
                    total_acc, total_food, total_trans,
                    acc_ppd, food_ppd, trans_ppd,
                    subtotal, budget_warning
                ) = self._optimize_for_budget(
                    acc_ppd, food_ppd, trans_ppd,
                    total_acc, total_food, total_trans,
                    subtotal,
                    num_people, num_days, rooms,
                    should_rent_car,
                    attraction_cost, car_rental_cost, fuel_cost, intercity_transport_cost,
                    budget_amount, budget_strictness,
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
            car_rental_cost, should_rent_car,
            fuel_cost, fuel_km, budget_level,
            intercity_transport_cost, intercity_mode,
            subtotal, miscellaneous_cost, total,
        )

        return {
            "total":               round(total, 2),
            "accommodation":       round(total_acc, 2),
            "food":                round(total_food, 2),
            "transport":           round(total_trans, 2),
            "intercity_transport": round(intercity_transport_cost, 2),
            "miscellaneous":       round(miscellaneous_cost, 2),
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
            "breakdown":           breakdown,
        }

    # ═════════════════════════════════════════════════════════════════════════
    # PRIVATE COMPONENT CALCULATORS
    # ═════════════════════════════════════════════════════════════════════════

    def _resolve_budget_level(self, user_prefs: dict) -> tuple:
        """
        Determine budget level, numeric budget amount (or None), and strictness.

        Budget level thresholds (for numeric budgets):
            ≤ ₹30,000  → low
            ≤ ₹1,00,000 → medium
            > ₹1,00,000 → high

        Returns (budget_level: str, budget_amount: int | None, strictness: str | None)
        """
        raw_budget        = user_prefs.get("budget", "medium")
        budget_amount     = user_prefs.get("budget_amount")
        budget_strictness = user_prefs.get("budget_strictness")

        if budget_amount is not None:
            try:
                budget_amount = int(
                    str(budget_amount).replace(",", "").replace("₹", "").strip()
                )
            except (ValueError, TypeError):
                budget_amount = None

        if budget_amount is not None:
            if budget_amount <= 30_000:
                budget_level = "low"
            elif budget_amount <= 1_00_000:
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

    def _get_per_day_costs(self, budget_level: str, city: str) -> tuple:
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
        tier       = self._get_city_tier(city)
        multiplier = self._TIER_MULTIPLIER.get(tier, 0.80)
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

        # Attempt LLM refinement (secondary)
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

            llm = ChatGoogleGenerativeAI(model="gemini-flash-lite-latest", temperature=0.1)

            luxury_note = "" if budget_level == "high" else (
                " Do NOT use luxury hotel or fine-dining prices. "
                "Use the AVERAGE (not maximum) realistic price for this budget category."
            )

            prompt = (
                f"You are a travel cost expert for India.\n"
                f"Estimate realistic AVERAGE daily costs in INR for a '{budget_level}' budget traveller "
                f"visiting {city}.\n"
                f"{luxury_note}\n"
                f"Return ONLY a JSON object with these three fields:\n"
                f"  accommodation: cost per ROOM per NIGHT for a typical "
                f"{'budget guesthouse/hostel' if budget_level=='low' else '3-star hotel' if budget_level=='medium' else '4-star hotel'} "
                f"in {city}. Realistic range: INR {int(acc_min)}–{int(acc_max)}.\n"
                f"  food: total food cost per PERSON per DAY (breakfast + lunch + dinner) at "
                f"{'dhabas/local eateries' if budget_level=='low' else 'mid-range restaurants' if budget_level=='medium' else 'good restaurants'} "
                f"in {city}. Realistic range: INR {int(food_min)}–{int(food_max)}.\n"
                f"  transport: local city transport per PERSON per DAY "
                f"(autos/metro/cabs within {city} only, NOT intercity). "
                f"Realistic range: INR {int(trans_min)}–{int(trans_max)}.\n"
                'Return ONLY valid JSON (no markdown, no explanation): '
                '{"accommodation": N, "food": N, "transport": N}'
            )

            resp = llm.invoke([HumanMessage(content=prompt)])
            content = resp.content
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )
            content = content.strip()

            # Strip optional markdown fences
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL | re.IGNORECASE)
            content = m.group(1) if m else content[content.find("{") : content.rfind("}") + 1]

            d = json.loads(content)
            raw_acc   = float(d.get("accommodation", default_acc))
            raw_food  = float(d.get("food",          default_food))
            raw_trans = float(d.get("transport",     default_trans))

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

    def _calculate_attraction_cost(self, spots: list, num_people: int) -> float:
        """
        Total attraction entry-fee cost.

        Formula: Σ entry_fee(spot.price_level) × num_people

        Entry fee map (INR per person, realistic Indian tourist prices):
            0 = free       (parks, beaches, public squares)
            1 = ₹50        (small temples, minor heritage sites)
            2 = ₹200       (state museums, mid-range monuments)
            3 = ₹400       (ASI monuments, wildlife reserves)
            4 = ₹750       (Taj Mahal, major national parks)
        """
        total = 0.0
        for spot in spots:
            price_level = spot.get("price_level")
            per_person  = self._ENTRY_FEE_MAP.get(price_level, self._ENTRY_FEE_DEFAULT)
            total      += per_person * num_people
        return total

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_car_rental_cost(
        self,
        car_info: list | None,
        budget_level: str,
        num_days: int,
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
        daily_max     = float(self._RENTAL_DAILY_MAX.get(budget_level, 3_500))

        if not car_info:
            daily = min(default_daily, daily_max)
            return daily * num_days, daily

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
        currency  = str(car_info[idx].get("currency", "USD")).upper().strip()

        if raw_price and float(raw_price) > 0:
            price_val = float(raw_price)

            # Convert foreign currency to INR
            if currency not in ("INR", "RS", "RS.", "₹"):
                price_val *= self._USD_TO_INR

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
        fuel_cost        = round(litres_consumed * self._PETROL_INR_PER_LITRE, 2)

        return fuel_cost, round(total_km, 1)

    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_intercity_transport_cost(
        self,
        transit_options: dict | None,
        budget_level: str,
        num_people: int,
    ) -> tuple:
        """
        Round-trip intercity transport cost (origin → destination → origin).

        Default selection strategy:
        ─────────────────────────────────────────────────────────────────────
        Trains are the default mode for Indian intercity travel — they are the
        most economical and practical option.  The LOWEST available train fare
        (sorted by price_inr ascending) is used as the reference cost unless:

            a) The user / Transit Agent explicitly recommends "flight" or "bus"
               AND no train service is available for that route, OR
            b) No trains are available at all for the route.

        Selection order:
            1. If trains available  → always pick the LOWEST train fare
               (regardless of budget level or recommended_mode)
            2. Only switch to flights/buses if the user/agent explicitly
               selected that mode AND trains are not available.
            3. Last-resort fallback: cheapest option from any available mode.

        Formula: lowest_one_way_price_INR × 2 × num_people  (round trip)

        Returns (cost: float, mode_description: str)
        """
        if not transit_options:
            return 0.0, "N/A"

        recommended_mode = transit_options.get("recommended_mode", "").lower()
        trains           = transit_options.get("trains",  [])
        flights          = transit_options.get("flights", [])
        buses            = transit_options.get("buses",   [])

        options: list = []
        mode_label    = "transport"

        # ── Step 1: Default to lowest train fare whenever trains are available ─
        if trains:
            # Sort ascending by price_inr so we always use the cheapest train
            sorted_trains = sorted(trains, key=lambda t: float(t.get("price_inr", 0)))
            options    = sorted_trains
            mode_label = "train"

        # ── Step 2: Override with explicit user/agent mode selection ──────────
        # Only switch away from trains when the recommended mode is NOT train
        # AND the recommended mode has available options
        elif "flight" in recommended_mode and flights:
            options    = sorted(flights, key=lambda f: float(f.get("price_inr", 0)))
            mode_label = "flight"

        elif "bus" in recommended_mode and buses:
            options    = sorted(buses, key=lambda b: float(b.get("price_inr", 0)))
            mode_label = "bus"

        # ── Step 3: Last-resort fallback (no trains; no explicit mode match) ──
        if not options:
            all_options: list = []
            if flights:
                all_options += [("flight", f) for f in flights]
            if buses:
                all_options += [("bus",    b) for b in buses]
            if all_options:
                # Pick the cheapest option across any available mode
                all_options.sort(key=lambda x: float(x[1].get("price_inr", 0)))
                mode_label = all_options[0][0]
                options    = [all_options[0][1]]

        if not options:
            return 0.0, "N/A"

        # Always use the first item in the sorted list (lowest fare)
        one_way_inr = float(options[0].get("price_inr", 0))
        total_cost  = one_way_inr * 2 * num_people  # round trip
        return total_cost, mode_label

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
    ) -> tuple:
        """
        Determine whether the stated budget is feasible at all.

        A trip is considered infeasible when the budget is less than 65 % of
        the minimum viable cost (cheapest low-budget trip possible).

        Returns (is_infeasible: bool, warning_message: str | None)
        """
        caps = self._COST_CAPS["low"]
        min_viable = (
            caps["acc_min"]   * rooms      * num_days
            + caps["food_min"]  * num_people * num_days
            + caps["trans_min"] * num_people * num_days
            + intercity_transport_cost
        )

        if budget_amount < min_viable * 0.65:
            warning = (
                f"Your budget of ₹{budget_amount:,} appears too low for a {num_days}-day trip "
                f"for {num_people} person(s). Minimum realistic cost: ₹{int(min_viable):,}. "
                "Consider increasing your budget, reducing trip duration, or fewer paid attractions."
            )
            return True, warning

        return False, None

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

            if new_total <= budget_amount:
                break

        if new_total > budget_amount:
            if budget_strictness == "strict":
                budget_warning = (
                    f"Even after optimisation the estimated cost (₹{int(new_total):,}) "
                    f"exceeds your strict budget of ₹{budget_amount:,}. "
                    "Consider reducing travel days or choosing fewer paid attractions."
                )
            else:
                budget_warning = (
                    f"Estimated trip cost (₹{int(new_total):,}) is above your "
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
            round(new_total,   2), budget_warning
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
    ) -> dict:
        """
        Build human-readable calculation strings for every budget component.
        These strings make estimates easy to validate and debug.
        """
        consumption = self._CONSUMPTION_L_PER_100KM.get(budget_level, 8.0)
        mileage_kml = round(100.0 / consumption, 1)  # convert L/100km → km/L

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
                f"{fuel_km:.0f} km ÷ {mileage_kml} km/L × ₹{self._PETROL_INR_PER_LITRE:.0f}/L"
                f" = ₹{fuel_cost:,.0f}"
            )
        else:
            car_line  = "No car rental"
            fuel_line = "No fuel cost (no car rental)"

        if intercity_transport_cost > 0:
            one_way    = round(intercity_transport_cost / (2 * num_people))
            transit_ln = (
                f"₹{one_way:,} ({intercity_mode}) × 2 (round-trip) × {num_people} person(s)"
                f" = ₹{intercity_transport_cost:,.0f}"
            )
        else:
            transit_ln = "Not applicable (same city) — ₹0"

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
