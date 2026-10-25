"""Defensive JSON extraction for LLM / web retrieval output.

Models routinely return JSON that is *almost* valid: wrapped in markdown
fences, preceded by "Here are the attractions:", split into several
concatenated objects, or using a trailing comma.  The previous implementation
called ``json.loads`` on the whole string and silently returned zero POIs on a
parse error, which is exactly what happened to the Japan web-retrieval stage.

``extract_json_values`` never raises and returns every complete JSON value it
can find.  ``records_from_text`` flattens those values into a list of dicts and
understands the common wrapper keys (``pois``, ``attractions``, ``places``,
``results``, ``data``).
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, Iterable, List

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_LIST_WRAPPER_KEYS = (
    "pois", "poi", "attractions", "attraction", "places", "place",
    "results", "result", "items", "destinations", "data",
)


def _strip_fences(text: str) -> List[str]:
    """Return the fenced blocks plus the original text as a fallback."""
    blocks = [m.group(1).strip() for m in _FENCE_RE.finditer(text or "")]
    blocks.append(text or "")
    return [b for b in blocks if b and b.strip()]


def _repair(text: str) -> str:
    cleaned = (text or "").strip().lstrip("\ufeff")
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    # A single smart quote from a model can break an otherwise valid payload.
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"')
    return cleaned


def _scan_json(text: str) -> List[Any]:
    """Extract every complete JSON value from arbitrary surrounding prose."""
    text = _repair(text)
    if not text:
        return []

    values: List[Any] = []
    # Fast path: the whole payload is valid.
    try:
        return [json.loads(text)]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    while idx < length:
        starts = [p for p in (text.find("{", idx), text.find("[", idx)) if p != -1]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            idx = start + 1
            continue
        values.append(value)
        idx = max(end, start + 1)
    return values


def _literal_eval_fallback(text: str) -> List[Any]:
    """Last resort for Python-style dict/list literals (single quotes)."""
    text = _repair(text)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start == -1 or end <= start:
            continue
        snippet = text[start:end + 1]
        try:
            value = ast.literal_eval(snippet)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, (dict, list)):
            return [value]
    return []


def extract_json_values(text: Any) -> List[Any]:
    """Return every JSON value found in ``text`` (never raises)."""
    if text is None:
        return []
    if isinstance(text, (dict, list)):
        return [text]
    if not isinstance(text, str):
        text = str(text)

    values: List[Any] = []
    seen_reprs = set()
    for block in _strip_fences(text):
        candidates = _scan_json(block)
        if not candidates:
            candidates = _literal_eval_fallback(block)
        for value in candidates:
            marker = repr(value)
            if marker in seen_reprs:
                continue
            seen_reprs.add(marker)
            values.append(value)
    return values


def _flatten(value: Any, depth: int = 0) -> Iterable[Dict[str, Any]]:
    if depth > 4:
        return
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item, depth + 1)
        return
    if not isinstance(value, dict):
        return

    # A wrapper object such as {"pois": [...]} or {"data": {"places": [...]}}.
    wrapper = None
    for key in _LIST_WRAPPER_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, list):
            wrapper = candidate
            break
        if isinstance(candidate, dict) and key == "data":
            wrapper = candidate
            break
    if wrapper is not None:
        yield from _flatten(wrapper, depth + 1)
        # Some model outputs include both a wrapper and top-level metadata; the
        # wrapper is authoritative, so do not emit the metadata as a POI.
        return

    yield value


def records_from_text(text: Any) -> List[Dict[str, Any]]:
    """Extract a flat list of record dicts from arbitrary model output."""
    records: List[Dict[str, Any]] = []
    for value in extract_json_values(text):
        records.extend(_flatten(value))
    return records


def clamp_rating(value: Any) -> float | None:
    """Return a real 0–5 rating or ``None``; never synthesise a default."""
    if value is None or isinstance(value, bool):
        return None
    try:
        rating = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 0.0 < rating <= 5.0:
        return round(rating, 2)
    return None


def clamp_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def clean_name(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n-–—|")
    if not text or text.lower() in {"none", "null", "n/a", "na", "unknown", "name"}:
        return ""
    return text[:160]
