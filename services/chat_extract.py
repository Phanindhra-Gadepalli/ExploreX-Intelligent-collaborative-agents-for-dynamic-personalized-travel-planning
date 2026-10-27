"""Deterministic, offline parsing of trip details from a chat message.

The LLM structured extractor (``TravelState``) is the primary path. This module
is the fallback: it recovers the critical trip fields from the *same* message
without a second model call, so a provider outage, a quota error or an invalid
tool schema can never put the conversation into an endless
"please tell me X" loop.

Design rules
------------
* Only high-confidence, explicit statements are parsed. Nothing is inferred or
  defaulted — an absent field stays absent.
* A real zero is preserved (``kids = 0`` for "no kids"); it is never treated as
  "not answered".
* Anything ambiguous (for example a bare city name with no context) is left to
  the LLM rather than guessed.
* Values are only ever merged into fields that are still empty, so the LLM's
  reading of the sentence always wins when it produced one.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional

# Fields this module may fill. Kept in one place so callers can filter.
DETERMINISTIC_FIELDS = (
    "origin_city",
    "city",
    "days",
    "people",
    "kids",
    "budget",
    "budget_amount",
    "budget_strictness",
    "health",
    "hobbies",
    "start_date",
)

_WORD_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
}

_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Words that must never be mistaken for a place name.
_PLACE_STOPWORDS = {
    "the", "a", "an", "i", "we", "my", "our", "it", "this", "that", "there",
    "everyone", "all", "good", "high", "low", "medium", "no", "yes", "please",
    "for", "with", "and", "or", "but", "then", "next", "some", "any", "trip",
    "holiday", "vacation", "tour", "luxury", "budget", "photography",
    "viewpoints", "now", "later", "continue", "go", "ahead", "select",
}

# Trailing noise that follows a place name in natural sentences.
_PLACE_TAIL = {
    "trip", "holiday", "vacation", "tour", "travel", "for", "with", "and",
    "in", "on", "from", "to", "the", "a", "an", "please", "luxury", "budget",
    "next", "week", "month", "year", "days", "day",
}

_FLEXIBLE_DATE = re.compile(
    r"\b(?:flexible|not\s+decided|undecided|any\s*date|no\s+fixed\s+date|"
    r"haven'?t\s+decided|hasn'?t\s+decided|not\s+sure\s+yet|open\s+date|"
    r"whenever|no\s+specific\s+date|date\s+is\s+flexible)\b",
    re.IGNORECASE,
)

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{4})\b",
    re.IGNORECASE,
)

_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")


def _to_int(token: Optional[str]) -> Optional[int]:
    """Convert ``"10"`` / ``"ten"`` / ``"10,000"`` to an int, else ``None``."""
    if token is None:
        return None
    tok = str(token).strip().lower().replace(",", "")
    if not tok:
        return None
    if tok in _WORD_NUMBERS:
        return _WORD_NUMBERS[tok]
    try:
        return int(float(tok))
    except (TypeError, ValueError):
        return None


def _first_int(patterns, text: str) -> Optional[int]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        for group in match.groups():
            value = _to_int(group)
            if value is not None:
                return value
    return None


def _clean_place(raw: str) -> Optional[str]:
    """Trim sentence noise around a place name and reject non-places."""
    if not raw:
        return None
    words = [w.strip(" ,.;:!?") for w in raw.split()]
    words = [w for w in words if w]
    # Drop trailing filler ("Tokyo luxury trip" -> "Tokyo").
    while words and words[-1].lower() in _PLACE_TAIL:
        words.pop()
    if not words:
        return None
    if len(words) > 4:
        words = words[:4]
    if words[0].lower() in _PLACE_STOPWORDS:
        return None
    name = " ".join(words)
    # Require at least one alphabetic character and a sane length.
    if not re.search(r"[A-Za-z]", name) or len(name) > 60:
        return None
    return name.title() if name.islower() else name


def _scale_amount(number: float, suffix: Optional[str]) -> int:
    """Apply an Indian/English magnitude suffix to a raw number."""
    suffix = (suffix or "").lower()
    if suffix in ("lakh", "lac", "lakhs", "lacs"):
        number *= 100_000
    elif suffix in ("crore", "crores", "cr"):
        number *= 10_000_000
    elif suffix in ("thousand", "k"):
        number *= 1_000
    return int(round(number))


def _parse_budget_amount(text: str) -> Optional[int]:
    """Extract an explicit INR amount in plain rupees."""
    # ₹ / Rs / INR prefixed amounts, including a magnitude suffix so that
    # "Rs 2 lakh" is 200000 and not the bare "2" before the word "lakh".
    for match in re.finditer(
        r"(?:₹|rs\.?|inr)\s*([\d][\d,]*(?:\.\d+)?)\s*"
        r"(lakhs?|lacs?|crores?|cr|thousand|k)?",
        text,
        re.IGNORECASE,
    ):
        raw = match.group(1).replace(",", "")
        try:
            number = float(raw)
        except ValueError:
            continue
        value = _scale_amount(number, match.group(2))
        if value > 0:
            return value

    # "1 lakh", "2.5 lakh", "3 crore"
    match = re.search(
        r"\b([\d.]+)\s*(lakhs?|lacs?|crores?|cr)\b", text, re.IGNORECASE
    )
    if match:
        try:
            return _scale_amount(float(match.group(1)), match.group(2))
        except ValueError:
            pass

    # "50 thousand", "50k"
    match = re.search(
        r"\b([\d.]+)\s*(thousand|k)\b", text, re.IGNORECASE
    )
    if match:
        try:
            return _scale_amount(float(match.group(1)), match.group(2))
        except ValueError:
            pass

    # "budget is 80000" / "budget of 80,000"
    match = re.search(
        r"\bbudget(?:\s+(?:is|of|around|about|up\s*to|upto))?\s*"
        r"([\d][\d,]{3,})\b",
        text,
        re.IGNORECASE,
    )
    if match:
        value = _to_int(match.group(1))
        if value:
            return value

    return None


def _parse_budget_category(text: str) -> Optional[str]:
    lowered = text.lower()
    if re.search(r"\b(luxury|premium|high[- ]end|five[- ]star)\b", lowered):
        return "high"
    if re.search(r"\bhigh\s+(?:budget|range)\b", lowered):
        return "high"
    if re.search(r"\b(?:medium|moderate|mid[- ]range|mid[- ]budget)\b", lowered):
        return "medium"
    if re.search(
        r"\b(?:low\s+(?:budget|range)|cheap|budget[- ]friendly|shoestring|"
        r"on\s+a\s+budget|backpacker)\b",
        lowered,
    ):
        return "low"
    return None


def _parse_hobbies(text: str) -> Optional[str]:
    patterns = (
        r"\b(?:we|i|they)\s+(?:really\s+)?(?:like|love|enjoy|prefer|"
        r"are\s+interested\s+in|'re\s+interested\s+in|are\s+into|'re\s+into)\s+"
        r"([^.;!?]+)",
        r"\b(?:who|that)\s+(?:really\s+)?(?:like|love|enjoy|prefer|"
        r"are\s+interested\s+in|'re\s+interested\s+in)\s+([^.;!?]+)",
        r"\b(?:hobbies|interests)\s*(?:are|include|:)\s*([^.;!?]+)",
        r"\binterested\s+in\s+([^.;!?]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" ,")
        value = re.sub(r"\s+", " ", value)
        # Drop a trailing "for N days" style clause if the model-style text
        # happens to continue past the interests.
        value = re.split(r"\s+(?:for|during)\s+\d", value, maxsplit=1)[0].strip()
        if len(value) >= 2:
            return value
    return None


def _parse_health(text: str) -> Optional[str]:
    lowered = text.lower()
    if re.search(
        r"\b(?:limited\s+mobility|wheelchair|health\s+(?:issues?|problems?)|"
        r"medical\s+condition|elderly|senior\s+citizens?)\b",
        lowered,
    ):
        return "limited"
    if re.search(
        r"\b(?:everyone\s+is\s+(?:in\s+)?(?:good|fine|fit)|all\s+(?:are\s+)?"
        r"(?:healthy|fit|fine)|no\s+health\s+(?:issues?|problems?)|"
        r"good\s+health|healthy|fit\s+and\s+healthy)\b",
        lowered,
    ):
        return "good"
    return None


def _parse_start_date(text: str) -> Optional[str]:
    if _FLEXIBLE_DATE.search(text):
        return "flexible"

    match = _ISO_DATE.search(text)
    if match:
        raw = "-".join(match.groups())
        try:
            date.fromisoformat(raw)
            return raw
        except ValueError:
            pass

    match = _DAY_MONTH_YEAR.search(text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day)).isoformat()
            except ValueError:
                pass
    return None


def _parse_origin(text: str, current: Dict[str, Any]) -> Optional[str]:
    # "from Bengaluru", "origin is Mumbai"
    for pattern in (
        r"\bfrom\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})",
        r"\borigin(?:\s+city)?\s*(?:is|:)\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})",
        r"\bstarting\s+(?:from|in)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})",
    ):
        match = re.search(pattern, text)
        if match:
            place = _clean_place(match.group(1))
            if place:
                return place

    # A leading "City, ..." only names the origin once a destination is known.
    if current.get("city"):
        match = re.match(
            r"^\s*([A-Za-z][A-Za-z .'-]{1,30}?)\s*,\s*"
            r"(?=\d|\bno\b|\bwe\b|\ball\b|\bthere\b)",
            text,
        )
        if match:
            place = _clean_place(match.group(1))
            if place:
                return place
    return None


def _parse_city(text: str) -> Optional[str]:
    patterns = (
        r"\b(?:trip|travel|journey|holiday|vacation|going|flying?|fly)\s+to\s+"
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})",
        r"\b(?:visit|visiting|explore|exploring)\s+"
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})",
        r"\b\d+\s*[- ]\s*day\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})",
        r"\bto\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        place = _clean_place(match.group(1))
        if place:
            return place
    return None


def extract_trip_fields(
    text: str, current: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Parse explicit trip details from ``text`` without calling an LLM.

    Returns only the fields that could be read with confidence. ``current`` is
    used solely to disambiguate a leading "City, ..." phrase (origin vs
    destination); it is never modified.
    """
    if not text or not text.strip():
        return {}
    current = current or {}
    found: Dict[str, Any] = {}

    days = _first_int(
        (
            r"(\d+)\s*[- ]\s*day\b",
            r"(\d+)\s*[- ]\s*night",
            r"\bfor\s+(\d+)\s+days?\b",
            r"(\d+)\s+days?\b",
            r"\b([a-z]+)\s*[- ]\s*day\b",
            r"\b([a-z]+)\s+days?\b",
        ),
        text,
    )
    if days and 0 < days <= 90:
        found["days"] = days

    people = _first_int(
        (
            r"(\d+)\s*(?:people|persons?|travell?ers?|travelers?|adults?|"
            r"members?|guests?|pax|of\s+us)\b",
            r"\bgroup\s+of\s+([\w]+)",
            r"\bwe\s+are\s+([\w]+)",
            r"(\d+)\s*ppl\b",
        ),
        text,
    )
    if people and 0 < people <= 500:
        found["people"] = people

    if re.search(
        r"\b(?:no|zero|without\s+any?)\s+(?:kids?|children)\b|\b0\s+(?:kids?|children)\b",
        text,
        re.IGNORECASE,
    ):
        found["kids"] = 0
    else:
        kids = _first_int(
            (
                r"(\d+)\s+(?:kids?|children)\b",
                r"\bwith\s+(\d+)\s+(?:kids?|children)\b",
            ),
            text,
        )
        if kids is not None and 0 <= kids <= 50:
            found["kids"] = kids

    amount = _parse_budget_amount(text)
    if amount:
        found["budget_amount"] = amount

    category = _parse_budget_category(text)
    if category:
        found["budget"] = category

    lowered = text.lower()
    if re.search(
        r"\b(strictly|must\s+not\s+exceed|do\s+not\s+exceed|don'?t\s+exceed|"
        r"keep\s+(?:it\s+)?below|no\s+more\s+than|must\s+be\s+within|"
        r"should\s+not\s+exceed|less\s+than|maximum)\b",
        lowered,
    ):
        found["budget_strictness"] = "strict"
    elif re.search(
        r"\b(around|approximately|about|roughly|up\s*to|nearly|"
        r"flexible\s+budget)\b",
        lowered,
    ):
        found["budget_strictness"] = "flexible"

    health = _parse_health(text)
    if health:
        found["health"] = health

    start_date = _parse_start_date(text)
    if start_date:
        found["start_date"] = start_date

    hobbies = _parse_hobbies(text)
    if hobbies:
        found["hobbies"] = hobbies

    city = _parse_city(text)
    if city:
        found["city"] = city

    origin = _parse_origin(text, current)
    # "from X" must never be read as the destination when it is the origin.
    if origin and found.get("city", "").lower() == origin.lower():
        found.pop("city", None)
    if origin:
        found["origin_city"] = origin

    return {k: v for k, v in found.items() if k in DETERMINISTIC_FIELDS}


def has_value(value: Any) -> bool:
    """True when a value counts as answered (a real ``0`` does)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def merge_missing(
    target: Dict[str, Any],
    candidates: Dict[str, Any],
    keys: Optional[Any] = None,
) -> Dict[str, Any]:
    """Copy candidates into ``target`` only where it has no value yet.

    Returns the subset that was actually applied, so callers can log exactly
    what the fallback contributed.
    """
    allowed = set(keys) if keys is not None else None
    applied: Dict[str, Any] = {}
    for field, value in (candidates or {}).items():
        if allowed is not None and field not in allowed:
            continue
        if not has_value(value):
            continue
        if has_value(target.get(field)):
            continue
        target[field] = value
        applied[field] = value
    return applied


def is_valid_tool_name(name: Optional[str]) -> bool:
    """Whether ``name`` is a legal Gemini function-declaration name."""
    return bool(name) and bool(_TOOL_NAME.match(name))
