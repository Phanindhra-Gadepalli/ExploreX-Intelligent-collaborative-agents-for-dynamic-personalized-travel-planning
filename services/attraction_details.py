"""
attraction_details.py — ExploreX Attraction Details Enrichment Service

Fetches factual, attraction-specific information from Wikipedia REST API
(free, no key required) and merges with any metadata already available
from Geoapify / OSM.

Design principles:
  - Zero paid API calls — uses Wikipedia REST summary endpoint only.
  - Cache-first: results are cached in memory (and optionally to disk) to
    avoid repeated network round-trips for the same attraction.
  - Never hallucinates: factual fields (opening_hours, address, etc.) come
    ONLY from retrieved data. LLM is NOT used here.
  - Graceful degradation: every field that cannot be resolved is omitted
    rather than filled with placeholder text.
"""

import os
import re
import time
import json
import hashlib
import requests
import unicodedata
from typing import Optional, Dict, Any

# ---------------------------------------------------------------------------
# In-memory cache  {cache_key: {data: {...}, ts: float}}
# ---------------------------------------------------------------------------
_DETAIL_CACHE: Dict[str, Dict] = {}
_CACHE_TTL_SECONDS = 86400  # 24 hours — Wikipedia summaries don't change daily

# ---------------------------------------------------------------------------
# Optional disk-based cache (survives server restarts)
# ---------------------------------------------------------------------------
_DISK_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'attraction_cache')

def _ensure_cache_dir():
    try:
        os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
    except Exception:
        pass

def _disk_cache_path(cache_key: str) -> str:
    return os.path.join(_DISK_CACHE_DIR, f"{cache_key}.json")

def _read_disk_cache(cache_key: str) -> Optional[Dict]:
    path = _disk_cache_path(cache_key)
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                entry = json.load(f)
            if time.time() - entry.get('ts', 0) < _CACHE_TTL_SECONDS:
                return entry.get('data')
    except Exception:
        pass
    return None

def _write_disk_cache(cache_key: str, data: Dict):
    _ensure_cache_dir()
    path = _disk_cache_path(cache_key)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'data': data, 'ts': time.time()}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _make_cache_key(name: str, lat: Optional[float] = None, lng: Optional[float] = None) -> str:
    """Deterministic cache key from attraction name + rough location."""
    normalized = unicodedata.normalize('NFC', name.strip().lower())
    coord_str = f"{lat:.3f},{lng:.3f}" if (lat is not None and lng is not None) else "nocoord"
    raw = f"{normalized}|{coord_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Wikipedia REST API helpers
# ---------------------------------------------------------------------------
_WIKI_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_WIKI_HEADERS = {
    "User-Agent": "ExploreX-Travel-App/2.0 (https://github.com/ExploreX; explorex14569@gmail.com)",
    "Accept": "application/json"
}

def _fetch_wikipedia_summary(query: str) -> Optional[Dict]:
    """
    Attempt to fetch Wikipedia summary for `query`.
    Returns the parsed JSON object from Wikipedia REST API, or None.

    Tries:
      1. The name exactly as-is.
      2. The name with leading articles stripped (e.g. "The ..." -> "...").
      3. Removing parenthetical disambiguation.
    """
    candidates = [query]

    # Strip leading articles
    stripped = re.sub(r'^(the|a|an)\s+', '', query, flags=re.IGNORECASE).strip()
    if stripped and stripped.lower() != query.lower():
        candidates.append(stripped)

    # Remove parenthetical e.g. "Louvre Museum (Paris)" -> "Louvre Museum"
    no_paren = re.sub(r'\s*\(.*?\)', '', query).strip()
    if no_paren and no_paren not in candidates:
        candidates.append(no_paren)

    for candidate in candidates:
        # Wikipedia titles use underscores
        title = candidate.replace(' ', '_')
        url = _WIKI_BASE + requests.utils.quote(title, safe='')
        try:
            resp = requests.get(url, headers=_WIKI_HEADERS, timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                # Confirm this is an actual article (not a disambiguation)
                if data.get('type') in ('standard', 'disambiguation') or data.get('extract'):
                    if data.get('type') == 'disambiguation':
                        # Try appending the city from the original name if possible
                        continue
                    return data
            elif resp.status_code == 404:
                continue  # Try next candidate
        except requests.exceptions.Timeout:
            print(f"[ATTRACTION_DETAILS] Wikipedia timeout for '{candidate}'")
        except Exception as e:
            print(f"[ATTRACTION_DETAILS] Wikipedia error for '{candidate}': {e}")

    return None


def _clean_extract(text: Optional[str], max_sentences: int = 4) -> Optional[str]:
    """
    Trim a Wikipedia extract to at most `max_sentences` sentences.
    Returns None if text is empty or too short to be useful.
    """
    if not text:
        return None
    text = text.strip()
    if len(text) < 30:
        return None

    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text)
    selected = sentences[:max_sentences]
    result = ' '.join(selected).strip()

    # Truncate if still very long (>800 chars)
    if len(result) > 800:
        result = result[:800].rsplit(' ', 1)[0] + '…'

    return result if len(result) > 30 else None


def _extract_wikidata_fields(wiki_data: Dict) -> Dict:
    """
    Extract structured fields from the Wikipedia REST summary response.
    Only includes fields that are ACTUALLY present in the data.
    """
    result = {}

    # Description (short tagline from Wikipedia)
    if wiki_data.get('description'):
        result['wiki_tagline'] = wiki_data['description']

    # Extract (the actual prose summary)
    extract = _clean_extract(wiki_data.get('extract'))
    if extract:
        result['description'] = extract

    # Coordinates from Wikipedia (more accurate than OSM approximate)
    coords = wiki_data.get('coordinates')
    if coords and coords.get('lat') is not None and coords.get('lon') is not None:
        result['wiki_lat'] = coords['lat']
        result['wiki_lng'] = coords['lon']

    # Thumbnail image (use as fallback only)
    thumbnail = wiki_data.get('thumbnail', {})
    if thumbnail.get('source'):
        result['wiki_thumbnail'] = thumbnail['source']

    # Wikipedia page link
    page_url = wiki_data.get('content_urls', {}).get('desktop', {}).get('page')
    if page_url:
        result['wiki_url'] = page_url

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_attraction_details(
    name: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    existing_data: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Return an enriched attraction-detail dictionary for the given POI.

    Parameters
    ----------
    name : str
        Attraction name (e.g. "Taj Mahal", "Eiffel Tower").
    lat, lng : float, optional
        Coordinates — used only for cache-key differentiation.
    existing_data : dict, optional
        The POI object already held in the ExploreX session.  Fields already
        present and non-generic override defaults; Wikipedia fills the gaps.

    Returns
    -------
    dict
        Merged attraction detail object.  All fields are optional — absent if
        data is unavailable.  No placeholder / hallucinated values are inserted.
    """
    if not name or not name.strip():
        return {}

    cache_key = _make_cache_key(name, lat, lng)

    def _merge_with_existing(wiki_enriched: Dict) -> Dict:
        """Merge Wikipedia enrichment with session-specific existing_data."""
        result = {}
        # Start with session data (has id, rating, image_url, etc.)
        if existing_data and isinstance(existing_data, dict):
            result.update(existing_data)
        # Layer Wikipedia enrichment on top — but DON'T overwrite a good description
        current_desc = result.get('description', '')
        is_generic = (
            not current_desc
            or 'renowned' in current_desc.lower()
            or ('beautiful' in current_desc.lower() and 'located in' in current_desc.lower())
            or len(current_desc) < 60
        )
        for k, v in wiki_enriched.items():
            if k == 'description':
                if is_generic:
                    result['description'] = v
            else:
                result.setdefault(k, v)
        return result

    # 1. Check in-memory cache (stores Wikipedia-only enrichment)
    mem_entry = _DETAIL_CACHE.get(cache_key)
    if mem_entry and (time.time() - mem_entry['ts']) < _CACHE_TTL_SECONDS:
        print(f"[ATTRACTION_DETAILS] Memory cache hit for '{name}'")
        return _merge_with_existing(mem_entry['data'])

    # 2. Check disk cache
    disk_data = _read_disk_cache(cache_key)
    if disk_data is not None:
        print(f"[ATTRACTION_DETAILS] Disk cache hit for '{name}'")
        _DETAIL_CACHE[cache_key] = {'data': disk_data, 'ts': time.time()}
        return _merge_with_existing(disk_data)


    print(f"[ATTRACTION_DETAILS] Fetching Wikipedia details for '{name}'")

    # 3. Query Wikipedia
    wiki_data = _fetch_wikipedia_summary(name)
    wiki_cache: Dict[str, Any] = {}  # Only Wikipedia-sourced fields go to cache

    if wiki_data:
        wiki_fields = _extract_wikidata_fields(wiki_data)

        # Description
        if wiki_fields.get('description'):
            wiki_cache['description'] = wiki_fields['description']
            wiki_cache['description_source'] = 'wikipedia'

        # Significance / tagline
        if wiki_fields.get('wiki_tagline'):
            wiki_cache['significance'] = wiki_fields['wiki_tagline']

        # Wikipedia URL
        if wiki_fields.get('wiki_url'):
            wiki_cache['wiki_url'] = wiki_fields['wiki_url']

        # Wikipedia thumbnail (only as fallback image)
        if wiki_fields.get('wiki_thumbnail'):
            wiki_cache['wiki_thumbnail'] = wiki_fields['wiki_thumbnail']

        # Coordinates from Wikipedia (for POIs with no coords)
        if wiki_fields.get('wiki_lat'):
            wiki_cache['wiki_location'] = {
                'lat': wiki_fields['wiki_lat'],
                'lng': wiki_fields['wiki_lng']
            }

    # 4. Store only the Wikipedia enrichment in both caches (reusable across sessions)
    _DETAIL_CACHE[cache_key] = {'data': wiki_cache, 'ts': time.time()}
    _write_disk_cache(cache_key, wiki_cache)

    # 5. Merge with existing session data and return
    return _merge_with_existing(wiki_cache)

