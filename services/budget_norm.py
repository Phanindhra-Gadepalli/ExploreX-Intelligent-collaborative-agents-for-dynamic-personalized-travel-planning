"""
Single authoritative budget-normalisation layer (Phase 5/6 of the repair spec).

``BudgetAgent.estimate_budget`` emits ``estimated_total`` plus a ``breakdown``
of *human-readable strings*.  Downstream consumers (email service, budget page,
API responses, session state) historically looked for ``total`` / ``attractions``
and even parsed numbers back out of the descriptive strings, which produced the
reported "Total ₹0 / Transport ₹0 / Activities ₹16" symptoms.

``normalize_budget`` is now the ONE place that converts a raw BudgetAgent result
into a numeric, checksum-validated breakdown.  Every consumer must use it.

Contract produced:

    {
        "total": float,                  # == sum of the numeric categories
        "total_is_valid": bool,          # checksum agreed with estimated_total
        "accommodation": float,
        "food": float,
        "transport": float,              # intercity (origin <-> destination)
        "local_transport": float,        # in-city cabs/rental/fuel/transit
        "attractions": float,            # entry fees / activities
        "miscellaneous": float,
        "car_rental": float,             # informational, inside local_transport
        "fuel": float,                   # informational, inside local_transport
        "currency": "INR",
        "budget_limit": float | None,
        "budget_strictness": str | None,
        "status": "within" | "over" | "incomplete",
        "within_budget": bool | None,
        "remaining_budget": float | None,
        "utilization_percentage": float | None,
        "data_complete": bool,
        "people": int | None,
        "days": int | None,
        "rooms": int | None,
        "breakdown_text": {...},         # original human-readable strings
    }
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Categories that must sum to `total`. car_rental/fuel deliberately excluded:
# BudgetAgent folds them into `local_transport` and including both would
# double-count (spec Phase 4/5: keep intercity separate from local).
SUM_CATEGORY_KEYS = (
    "accommodation",
    "food",
    "transport",
    "local_transport",
    "attractions",
    "miscellaneous",
)

_CURRENCY_SYMBOL = "₹"


def _to_number(value: Any) -> Optional[float]:
    """Coerce genuine numeric values only.

    Unlike the old email-service helper, this NEVER scrapes numbers out of
    descriptive sentences ("16 attraction(s) x avg ₹150..." must not become
    16.0).  Strings are accepted only when they are a pure number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (OverflowError, ValueError, TypeError):
            return None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("₹", "").replace("$", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _first_number(raw: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        n = _to_number(raw.get(key))
        if n is not None:
            return n
    return None


def normalize_budget(raw: Any) -> Dict[str, Any]:
    """Convert any budget payload into the canonical numeric breakdown."""
    if not isinstance(raw, dict):
        raw = {}

    breakdown = raw.get("breakdown") if isinstance(raw.get("breakdown"), dict) else {}

    # --- totals -------------------------------------------------------------
    total = _first_number(raw, "total", "total_cost", "grand_total", "estimated_total")

    # --- categories (numeric fields ONLY; never re-parse descriptive text) --
    accommodation = _first_number(
        raw, "accommodation", "accommodation_cost", "stay_cost"
    )
    food = _first_number(raw, "food", "food_cost", "meals", "meals_cost")
    # BudgetAgent's "transport" is intercity-only; accept the synonyms too.
    transport = _first_number(raw, "transport", "intercity_transport", "intercity_transport_cost")
    local_transport = _first_number(raw, "local_transport", "local_transport_cost", "local_transport_total")
    attractions = _first_number(raw, "activities", "attractions", "attractions_cost", "activities_cost")
    miscellaneous = _first_number(raw, "miscellaneous", "misc", "misc_cost", "buffer")
    car_rental = _first_number(raw, "car_rental", "car_rental_cost")
    fuel = _first_number(raw, "fuel", "fuel_cost")

    parts = {
        "accommodation": accommodation,
        "food": food,
        "transport": transport,
        "local_transport": local_transport,
        "attractions": attractions,
        "miscellaneous": miscellaneous,
    }

    # If no explicit total was emitted, take the checksum of known parts.
    known_sum = sum(v for v in parts.values() if v is not None)
    any_part_known = any(v is not None for v in parts.values())

    # Where the announced figure came from matters to the reader: a total the
    # agent reported is different from one we had to add up ourselves from the
    # few categories that happened to have data.
    total_source = "none"
    if total is not None:
        total_source = "reported"
    elif any_part_known:
        total = known_sum
        total_source = "derived_from_categories"

    # Data is "incomplete" when the agent produced no usable total, when the
    # announced total disagrees with the categories we can actually show, or
    # when one of the displayed categories was never reported at all (a partial
    # sum must never be presented as the whole cost of a trip).
    checksum_ok = True
    if total is not None and any_part_known:
        checksum_ok = abs(known_sum - total) <= max(1.0, 0.02 * abs(total))

    missing_categories = [k for k, v in parts.items() if v is None]
    data_complete = bool(total is not None and checksum_ok and not missing_categories)

    parts_out = {k: (v if v is not None else 0.0) for k, v in parts.items()}
    for k, v in parts_out.items():
        parts_out[k] = round(v, 2)
    total_out = round(float(total), 2) if total is not None else 0.0

    # --- limit / strictness / status -----------------------------------------
    limit = _first_number(raw, "budget_limit", "budget", "budget_amount")
    strictness = raw.get("budget_type") or raw.get("budget_strictness")

    if total is None:
        status = "incomplete"
        within = None
    elif limit is None or limit <= 0:
        # No numeric limit known -> "fits the budget" cannot be asserted at all.
        status = "within" if data_complete else "incomplete"
        within = None
    elif total > limit:
        # Exceeding the limit always wins, even with incomplete data: showing an
        # "incomplete" label would hide a real overrun.
        status = "over"
        within = False
    else:
        status = "within" if data_complete else "incomplete"
        # An understated partial sum proves nothing about affordability.
        within = True if data_complete else None

    # The agent's own feasibility flag always wins for the "over" case.
    if raw.get("budget_infeasible"):
        status = "over" if limit else status

    remaining = raw.get("remaining_budget")
    remaining = _to_number(remaining)
    if remaining is None and limit is not None and total is not None:
        remaining = limit - total

    utilization = _to_number(raw.get("utilization_percentage"))
    if utilization is None and limit and total is not None and limit > 0:
        utilization = round(total / limit * 100.0, 1)

    normalized: Dict[str, Any] = {
        "total": total_out,
        "total_is_valid": bool(total is not None),
        "total_source": total_source,
        "checksum_consistent": bool(checksum_ok),
        "currency": "INR",
        "data_complete": bool(data_complete),
        "missing_categories": missing_categories,
        "car_rental": round(car_rental, 2) if car_rental is not None else 0.0,
        "fuel": round(fuel, 2) if fuel is not None else 0.0,
        "budget_limit": round(limit, 2) if limit is not None else None,
        "budget_strictness": strictness if strictness in ("strict", "flexible") else None,
        "status": status,
        # "Cannot claim fits-budget when the total exceeds the limit" (spec 5.6),
        # and never when the underlying data is only partly available.
        "within_budget": (
            False if (limit is not None and total is not None and total > limit)
            else (bool(raw["within_budget"]) and bool(data_complete)
                  if raw.get("within_budget") is not None else within)
        ),
        "remaining_budget": round(remaining, 2) if remaining is not None else None,
        "utilization_percentage": utilization,
        "people": raw.get("people"),
        "days": raw.get("days"),
        "rooms": raw.get("rooms"),
        "budget_category": raw.get("budget_category"),
        "budget_warning": raw.get("budget_warning"),
        "budget_infeasible": bool(raw.get("budget_infeasible")),
        "breakdown_text": breakdown,
    }
    normalized.update(parts_out)
    return normalized


def format_inr(amount: Any) -> str:
    """Render a numeric amount as ₹ with Indian digit grouping; None-safe."""
    n = _to_number(amount)
    if n is None:
        return "Not available"
    n = int(round(n))
    sign = "-" if n < 0 else ""
    digits = str(abs(n))
    if len(digits) <= 3:
        return f"{sign}{_CURRENCY_SYMBOL}{digits}"
    head, tail = digits[:-3], digits[-3:]
    chunks = []
    while len(head) > 2:
        chunks.insert(0, head[-2:])
        head = head[:-2]
    if head:
        chunks.insert(0, head)
    return f"{sign}{_CURRENCY_SYMBOL}{','.join(chunks)}{tail}"
