"""
Single authoritative transit-normalisation layer (Transport-options repair).

``TransitAgent.get_transit_options`` asks an LLM for a JSON blob, and the
result historically arrived with any of: a legacy ``{"flights": [...],
"trains": [...]}`` bucket shape, an ``options`` list, prices whose currency was
``USD`` while every consumer (budget, map, email, UI) labelled them ``₹``,
missing ``origin``/``destination`` so the UI could not say *which* journey the
fares referred to, ``estimated_cost: 0`` for "no idea" (rendered as a free
flight) and no round-trip/one-way basis at all.

``normalize_transit_options`` is the ONE place that converts whatever the agent
produced into the canonical payload shared by ``/api/state``, the Transport
tab, ``BudgetAgent`` and the email.  It never invents a price, a flight number
or an exchange rate: a value it cannot trust is returned as ``None`` with an
explicit ``cost_status`` instead.

Contract produced:

    {
        "origin": str | None,             # authoritative, from the session
        "destination": str | None,        # authoritative, from the session
        "route_label": "Delhi → Dubai",
        "same_city": bool,
        "is_estimate": True,
        "estimation_note": str,
        "trip_basis": "one_way",          # every quoted cost is one-way
        "currency": "INR",                # payload-level default currency
        "intercity_transport": [
            {
                "mode": str,
                "provider": str | None,
                "duration": str | None,
                "estimated_cost": float | None,
                "price_inr": float | None,     # alias consumed by BudgetAgent
                "cost_status": "estimated" | "unavailable" | "non_inr_currency",
                "cost_basis": "per_person" | "per_vehicle" | "per_group" | None,
                "currency": "INR",
                "group_cost": float | None,    # cost x people (one way)
                "origin": str, "destination": str,
                "departure_info": str | None, "arrival_info": str | None,
                "stops": int | None,
                "recommendation_reason": str | None,
                "eco_friendly": bool, "baggage": str | None,
                "is_estimate": True,
            }, ...
        ],
        "local_transport": [ ...same shape, cost_basis per_day/per_person/per_trip... ],
        "recommended_mode": str | None,
        "recommended_local_mode": str | None,
        "provider_status": "ok" | "empty" | "error",
    }
"""

from __future__ import annotations

import re

# Currency codes that mean "this is already rupees" - anything else is kept
# honestly labelled instead of being relabelled as INR.
_INR_ALIASES = {"inr", "₹", "rs", "rs.", "rupee", "rupees", "rupee(s)", "indian rupee", "indian rupees"}

_CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "AED": "AED ",
    "SGD": "S$ ",
    "THB": "฿",
}

_KNOWN_BASIS = {
    "per_person", "per_vehicle", "per_group", "per_day", "per_trip", "per_night",
}

# Legacy TransitAgent failure shape and older model outputs bucketed by mode.
_LEGACY_BUCKETS = {
    "flights": "flight",
    "trains": "train",
    "buses": "bus",
    "cars": "self_drive",
    "cab": "taxi",
}

_ARROW = " → "


def currency_symbol(code):
    """Return the display symbol for a currency code ('' when unknown)."""
    return _CURRENCY_SYMBOLS.get(normalize_currency(code) or "", "")


def normalize_currency(value, default="INR"):
    """Normalize a currency token to a 3-letter code; ``default`` when blank."""
    if value is None:
        return default
    raw = str(value).strip()
    if not raw:
        return default
    low = raw.lower().strip(". ")
    if low in _INR_ALIASES:
        return "INR"
    token = re.sub(r"[^a-z]", "", low).upper()
    if len(token) == 3:
        return token
    if "rupee" in low or "rs" == low:
        return "INR"
    return default


def is_inr(value):
    return normalize_currency(value) == "INR"


def _to_number(value):
    """Best-effort numeric parse of an LLM-supplied cost. ``None`` when absent."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_text(value, limit=160):
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n-–—|")
    if not text or text.lower() in ("none", "null", "n/a", "na", "unknown", "varies"):
        return None
    return text[:limit]


def _normalize_basis(value, fallback=None):
    raw = _clean_text(value)
    if not raw:
        return fallback
    token = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    if token in _KNOWN_BASIS:
        return token
    for candidate in ("per_person", "per_vehicle", "per_group", "per_day", "per_trip", "per_night"):
        if candidate in token:
            return candidate
    if "person" in token or "pax" in token or "traveller" in token or "traveler" in token:
        return "per_person"
    if "day" in token:
        return "per_day"
    if "group" in token:
        return "per_group"
    if "vehicle" in token or "car" in token:
        return "per_vehicle"
    return fallback


def _coerce_list(raw, *keys):
    """Pull the first list found under any of ``keys`` (dict values flattened)."""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, dict):
            value = list(value.values())
        if isinstance(value, list):
            return [v for v in value if v is not None]
    return []


def _legacy_buckets(raw):
    """Flatten the old ``{"flights": [...], "trains": [...]}`` shape."""
    out = []
    for key, mode in _LEGACY_BUCKETS.items():
        entries = raw.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str):
                out.append({"mode": mode, "provider": entry})
            elif isinstance(entry, dict):
                item = dict(entry)
                item.setdefault("mode", mode)
                out.append(item)
    return out


def _normalize_option(opt, origin, destination, default_currency, num_people, default_mode=None):
    """Return one canonical option dict, or ``None`` when there is nothing usable."""
    if isinstance(opt, str):
        opt = {"mode": default_mode or opt}
    if not isinstance(opt, dict):
        return None

    mode = _clean_text(opt.get("mode") or opt.get("type") or default_mode, 40)
    provider = _clean_text(opt.get("provider") or opt.get("operator") or opt.get("company"))
    if not mode and not provider:
        return None

    currency = normalize_currency(opt.get("currency") or default_currency)
    raw_cost = opt.get("estimated_cost")
    if raw_cost is None:
        raw_cost = opt.get("price_inr") if "price_inr" in opt else opt.get("price")
    if raw_cost is None:
        raw_cost = opt.get("cost")
    cost = _to_number(raw_cost)
    # A quoted 0 is only believable for a genuinely free mode.
    if cost is not None and cost <= 0:
        cost = cost if cost == 0 and "walk" in str(mode or "").lower() else None

    inr = is_inr(currency)
    if cost is None:
        cost_status = "unavailable"
    elif not inr:
        # Never relabel a foreign-currency quote as ₹ - it is shown with its own
        # symbol and excluded from the INR budget instead of being miscounted.
        cost_status = "non_inr_currency"
    else:
        cost_status = "estimated"

    basis = _normalize_basis(opt.get("cost_basis"))
    group_cost = None
    if cost is not None and inr and basis in ("per_person", "per_vehicle"):
        travellers = num_people if basis == "per_person" else 1
        try:
            travellers = max(1, int(travellers or 1))
        except (TypeError, ValueError):
            travellers = 1
        group_cost = round(cost * travellers, 2)

    stops = opt.get("stops")
    try:
        stops = int(stops) if stops is not None else None
    except (TypeError, ValueError):
        stops = None

    normalized = {
        "mode": str(mode),
        "provider": provider,
        "duration": _clean_text(opt.get("duration") or opt.get("travel_time"), 60),
        "estimated_cost": cost,
        # BudgetAgent reads price_inr; keeping the alias in one place stops it
        # from having to re-parse the LLM output.
        "price_inr": cost if inr else None,
        "cost_status": cost_status,
        "cost_basis": basis,
        "currency": currency,
        "group_cost": group_cost,
        "origin": _clean_text(opt.get("origin"), 80) or origin,
        "destination": _clean_text(opt.get("destination"), 80) or destination,
        "departure_info": _clean_text(opt.get("departure_info") or opt.get("departure"), 80),
        "arrival_info": _clean_text(opt.get("arrival_info") or opt.get("arrival"), 80),
        "stops": stops,
        "recommendation_reason": _clean_text(opt.get("recommendation_reason") or opt.get("reason"), 240),
        "eco_friendly": bool(opt.get("eco_friendly")),
        "baggage": _clean_text(opt.get("baggage"), 80),
        "is_estimate": True,
    }
    return normalized


def _dedupe(options):
    seen = set()
    out = []
    for opt in options:
        key = (str(opt.get("mode", "")).lower(), str(opt.get("provider", "")).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(opt)
    return out


def normalize_transit_options(raw, origin=None, destination=None, num_people=1,
                              same_city=False, estimation_note=None):
    """Convert any TransitAgent output into the canonical payload.

    Never raises: malformed input degrades to an empty, honestly-labelled
    result so the itinerary and budget steps still receive a usable shape.
    """
    origin = _clean_text(origin, 80)
    destination = _clean_text(destination, 80)

    data = raw if isinstance(raw, dict) else {}
    # The authoritative session values win over anything the model echoed.
    origin = origin or _clean_text(data.get("origin"), 80)
    destination = destination or _clean_text(data.get("destination"), 80)

    try:
        people = max(1, int(num_people or 1))
    except (TypeError, ValueError):
        people = 1

    same_city = bool(same_city) or (
        bool(origin) and bool(destination)
        and re.sub(r"[^a-z0-9]", "", origin.lower()) == re.sub(r"[^a-z0-9]", "", destination.lower())
    )

    default_currency = normalize_currency(data.get("currency"))
    raw_intercity = _coerce_list(data, "intercity_transport", "options")
    raw_local = _coerce_list(data, "local_transport", "local_transport_options")
    if not raw_intercity and not raw_local:
        raw_intercity = _legacy_buckets(data)

    intercity = [o for o in (
        _normalize_option(opt, origin, destination, default_currency, people)
        for opt in raw_intercity
    ) if o]
    local = [o for o in (
        _normalize_option(opt, origin, destination, default_currency, people)
        for opt in raw_local
    ) if o]

    # A same-city trip has no outbound leg; drop anything the model returned so
    # it can never leak into the budget or the email.
    if same_city:
        intercity = []

    intercity = _dedupe(intercity)
    local = _dedupe(local)

    def _match_mode(value, options):
        """Resolve a recommended-mode label onto a mode that is actually listed.

        Returns ``None`` rather than a dangling recommendation the UI would
        highlight against no card.
        """
        if not value:
            return None
        needle = re.sub(r"[^a-z]+", "", value.lower())
        for opt in options:
            name = re.sub(r"[^a-z]+", "", str(opt.get("mode", "")).lower())
            if name and (name == needle or needle in name or name in needle):
                return str(opt.get("mode")).lower()
        return None

    recommended = _match_mode(_clean_text(data.get("recommended_mode"), 40), intercity)
    recommended_local = _match_mode(_clean_text(data.get("recommended_local_mode"), 40), local)

    note = _clean_text(estimation_note or data.get("estimation_note"), 400)
    if same_city:
        note = note or ("You are already travelling within your destination city, so no "
                        "intercity journey was planned. Local transport is estimated below.")
    elif not note:
        note = ("Figures above are AI-generated planning estimates. They are not bookings "
                "and are not guaranteed prices.")

    if intercity or local:
        provider_status = "ok"
    elif data.get("provider_status") == "error":
        # A provider failure must stay distinguishable from "the route simply
        # has no options" - and this function is idempotent on its own output.
        provider_status = "error"
    else:
        provider_status = "empty"

    return {
        "origin": origin,
        "destination": destination,
        "route_label": (origin + _ARROW + destination) if (origin and destination) else (destination or origin),
        "same_city": same_city,
        "is_estimate": True,
        "estimation_note": note,
        "trip_basis": "one_way",
        "currency": default_currency if is_inr(default_currency) else "INR",
        "intercity_transport": intercity,
        "local_transport": local,
        "recommended_mode": recommended,
        "recommended_local_mode": recommended_local,
        "provider_status": provider_status,
        "num_people": people,
    }
