import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

from services.transit_norm import normalize_transit_options

class TransitAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize the TransitAgent with specified model."""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7)
    
    def get_transit_options(self, origin, destination, start_date, budget_level="medium", is_international=False, num_people=1):
        """Return a comprehensive, honestly-labelled set of transit options.

        Requirement 4: ExploreX must show every *relevant supported* transport
        mode with more than one option per mode when available, and clearly
        label every number as an **estimate** because we do not have live
        booking APIs wired in. Never fabricate exact flight numbers, live seat
        availability or booking URLs — the prompt and the schema below enforce
        that explicitly, and the frontend renders `is_estimate: true` as an
        "Estimated" chip on each card.

        Every result is passed through ``normalize_transit_options`` so the
        origin/destination leg, the currency and the cost basis are always
        present for the Transport tab, the budget and the email.
        """
        location_context = (
            "an international route" if is_international else "a domestic Indian route"
        )
        # The whole product prices in Indian Rupees (budget page, email, map).
        # Asking for USD on international legs made the UI print "$320" as
        # "₹320" for a Delhi->Dubai flight, so the model is now told to quote
        # INR directly instead of the app converting with a made-up rate.
        currency_context = (
            "Quote every price in Indian Rupees (INR) using plain numbers with no "
            "thousand separators - including international routes, for which you must "
            "convert the typical market fare into realistic INR yourself. Do NOT return "
            "prices in USD, EUR, AED or any other currency."
        )

        modes_block = (
            "For intercity_transport, provide one object per available mode — the goal is "
            "a fair comparison, not a single recommendation. Include as many of the following "
            "modes as are physically plausible for this origin/destination pair "
            "(aim for at least 4 entries total):\n"
            "  1) flight   — one entry per realistic operating pattern (non-stop + one-stop).\n"
            "  2) train    — one entry for the fastest train and one for an economical sleeper.\n"
            "  3) bus      — one entry for a state/volksbus or private coach.\n"
            "  4) self_drive (car) — road distance, ETA and estimated fuel/toll for a group car.\n"
            "  5) taxi_or_cab_transfer — intercity cab estimate for the group.\n"
            "If a mode is genuinely infeasible (no airport, no rail head, extreme distance for "
            "road), OMIT it entirely — do NOT fabricate an option for it."
        )

        local_modes_block = (
            "For local_transport, provide at least 3 clearly-different options suitable for "
            f"{destination} at a group size of {num_people} — e.g. rental_car (per day), "
            "taxi_or_app_cab (per day), public_bus_or_metro (per person/day), "
            "auto_rickshaw_shared (per day), bicycle_or_walk (free/short distance). "
            "Every option must be honestly labelled `is_estimate: true`."
        )

        try:
            people_hint = int(num_people or 1)
        except (TypeError, ValueError):
            people_hint = 1

        prompt = f"""
        Generate realistic travel-option ESTIMATES (not live bookings) for a journey
        from {origin} to {destination} ({location_context}), for a trip starting on {start_date}
        and a group of {people_hint} traveller(s). If origin/destination contains extra
        descriptive text, infer the actual city name and report it back. The budget level
        is {budget_level}.
        {currency_context}

        Return a JSON object with EXACTLY this structure:
        {{
            "origin": "Inferred origin city",
            "destination": "Inferred destination city",
            "is_estimate": true,
            "estimation_note": "Plain-language disclaimer that these are AI estimates and not live bookings.",
            "currency": "INR",
            "intercity_transport": [
                {{
                    "origin": "Inferred origin city (repeat for every option)",
                    "destination": "Inferred destination city (repeat for every option)",
                    "mode": "flight | train | bus | self_drive | taxi_or_cab_transfer",
                    "provider": "generic operator class, e.g. 'Major Indian full-service carrier', 'Indian Railways' — NEVER a specific flight/train number",
                    "duration": "human-readable total duration, e.g. '2h 30m'",
                    "estimated_cost": <ONE-WAY per-person estimate in INR>,
                    "cost_basis": "per_person | per_vehicle | per_group",
                    "currency": "INR",
                    "departure_info": "typical window, e.g. 'morning (06:00-10:00)' — do NOT invent an exact timetable",
                    "arrival_info": "typical window or 'varies'",
                    "stops": 0,
                    "recommendation_reason": "why a traveller might prefer this",
                    "is_estimate": true,
                    "eco_friendly": false,
                    "baggage": "short note if generally applicable, else omit"
                }}
            ],
            "local_transport": [
                {{
                    "mode": "rental_car | taxi | app_cab | public_transit | auto_rickshaw | walk_or_cycle",
                    "estimated_cost": <numeric INR>,
                    "cost_basis": "per_day | per_person | per_trip",
                    "currency": "INR",
                    "recommendation_reason": "...",
                    "is_estimate": true
                }}
            ],
            "recommended_mode": "one of: flight | train | bus | self_drive | taxi_or_cab_transfer",
            "recommended_local_mode": "one of the local_transport modes above"
        }}

        {modes_block}

        {local_modes_block}

        Pricing realism: `estimated_cost` is the ONE-WAY price per person (or per
        vehicle for self_drive/taxi) in INR. Use genuinely realistic market fares
        for this specific route and date — e.g. a Delhi->Dubai return economy air
        ticket is typically well over ₹20,000 for the pair, so a one-way figure in
        the ₹10,000-₹20,000 range is plausible while ₹320 is not. Never output 0
        because you are unsure: omit that mode instead.

        Group sizing: consider {people_hint} travellers when judging practicality. If
        a group of 6+ would realistically need 2 cabs or a large van, mention that in
        `recommendation_reason`.

        Distance-aware defaults: for journeys under ~500 km inside India, prefer
        train or self-drive over flight; do NOT default to flight for every trip.

        CRITICAL: `recommended_mode` must be the single most practical and cost-effective
        option given the distance, budget level, and group size, and must be one of the
        modes you actually listed in `intercity_transport`.

        Every entry must set `is_estimate: true` and the top-level object must set
        `is_estimate: true` with a human `estimation_note`. Do NOT fabricate exact
        flight numbers, live prices, seat availability, or booking URLs. Do not use
        markdown fences. Return ONLY the raw JSON object.
        """
        
        messages = [
            SystemMessage(content="You are a travel API that returns strictly valid JSON data representing transit schedules. Do not return any conversational text."),
            HumanMessage(content=prompt)
        ]
        try:
            response = self.model.invoke(messages)
            if isinstance(response.content, str):
                content = response.content.strip()
            elif isinstance(response.content, list):
                # Langchain sometimes returns a list of parts
                parts = []
                for p in response.content:
                    if isinstance(p, dict) and 'text' in p:
                        parts.append(p['text'])
                    elif isinstance(p, str):
                        parts.append(p)
                content = "".join(parts).strip()
            else:
                content = str(response.content)
            
            # Robust JSON extraction
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL | re.IGNORECASE)
            if json_match:
                content = json_match.group(1)
            else:
                # Fallback: find first { and last }
                start_idx = content.find('{')
                end_idx = content.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    content = content[start_idx:end_idx+1]
                
            parsed = json.loads(content.strip())
            # Canonical shape regardless of how faithfully the model obeyed the
            # schema: the leg (origin -> destination), INR currency and the
            # one-way cost basis are always present for the UI / budget / email.
            normalized = normalize_transit_options(
                parsed, origin=origin, destination=destination, num_people=people_hint
            )
            print(f"[TRANSIT] {normalized['route_label']}: {len(normalized['intercity_transport'])} "
                  f"intercity / {len(normalized['local_transport'])} local options "
                  f"(status={normalized['provider_status']})")
            return normalized
        except Exception as e:
            import traceback
            print(f"[ERROR] Failed to generate transit options: {e}")
            traceback.print_exc()
            # The old bare ``{"flights": [], ...}`` payload was unreadable by the
            # Transport tab, so a provider failure looked like "no options exist".
            failed = normalize_transit_options(
                None, origin=origin, destination=destination, num_people=people_hint
            )
            failed["provider_status"] = "error"
            failed["estimation_note"] = (
                "Transport options could not be generated right now, so no fares are "
                "shown. Nothing here is guessed - regenerate the plan to try again."
            )
            return failed
