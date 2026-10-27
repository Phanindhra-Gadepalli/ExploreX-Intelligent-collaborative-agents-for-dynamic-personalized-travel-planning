"""Reusable, cached attraction-image resolution.

Why this module exists
----------------------
Provider records are inconsistent: Geoapify and OSM usually carry no photo at
all, Wikipedia sometimes carries a thumbnail, and RAG/LLM records carry none.
The Explore page therefore showed ``No photo available`` for almost every card.

This module resolves an image in a *verifiable* way, in stages:

1. Keep a genuine provider image when one exists (``image_url`` / ``image`` /
   ``photo`` / ``thumbnail`` / ``wiki_thumbnail``), normalising the URL.
2. Use the identifiers the providers already give us:
   * an OSM ``image`` / ``wikimedia_commons`` / ``wikipedia`` / ``wikidata`` tag,
   * Geoapify's ``wikidata`` / ``wikipedia`` fields.
   Exact identifiers are trusted; their image is the place's own photo.
3. Batch-query Wikipedia GeoSearch for nearby notable articles and match by
   title + a tight distance check.
4. Search Wikipedia by name + destination for places GeoSearch missed.  The
   article title must match and, when coordinates are known, the article must
   be close to the attraction.
5. As the final, request-hungry fallback, batch-query Wikimedia Commons
   GeoSearch around clusters of the remaining places.  A Commons file is
   accepted only when it is geotagged within a few hundred metres *and* its
   name shares a distinctive token with the attraction.  This is what gives
   small temples, churches and local landmarks a real photo.
6. Only after every Wikimedia identifier/batch source has been exhausted, ask
   Pexels for a landscape stock photo.  A result is preferred when its alt text
   or photo slug contains the attraction's distinctive name tokens (or, for a
   partial match, the city).  A generic "temple" or city skyline is accepted
   only by the representative fallback below, never as a verified match.
7. Guarantee a visual for every displayed attraction.  If no verified or
   name-matched photo exists, the resolver tries, in order: a category + city
   Pexels photo, a nearby geotagged Commons photo, the destination's Wikipedia
   image, and finally a deterministic ExploreX travel card (an inline SVG).
   These fallbacks set ``image_is_representative=True`` so callers can label
   them honestly; they are never presented as verified photos of the place.

The ExploreX contract is therefore: an attraction card always has an image,
and that image is either verified, explicitly marked representative, or a
generated travel card.  ``None`` is only returned by the low-level lookup when
the caller opts out of the guarantee (``guarantee=False``).

Operational guarantees
----------------------
* Positive and negative TTL caches keyed by name + destination.
* A shared request throttle, short timeouts and batch API calls (not one call
  per place) so a 60-place state search stays within a few seconds.
* One HTTP 429 never discards the rest of the batch: the affected stage pauses,
  the other stages still run, and rate-limited records are not cached as
  permanent "no image" answers.
* Diagnostics in ``resolver.last_status`` tell the API/UI why images are
  missing (``rate_limited``, ``empty``, ``disabled``, ``error``).

No secrets are required by this module.
"""

from __future__ import annotations

import base64
import html
import os
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests


_WIKI_HEADERS = {
    "User-Agent": "ExploreXTravelPlanner/1.0 (educational trip-planning project)",
    "Accept": "application/json",
}

_OSM_HEADERS = {
    "User-Agent": "ExploreXTravelPlanner/1.0 (educational trip-planning project)",
    "Accept": "application/json",
}

_WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_OSM_API = "https://api.openstreetmap.org/api/0.6"
_PEXELS_API = "https://api.pexels.com/v1/search"

# Tokens that do not help decide whether a Wikipedia page is about this place.
_STOPWORDS = {
    "the", "a", "an", "of", "in", "at", "on", "and", "or", "for", "to",
    "near", "new", "old", "city", "town", "village", "district", "india",
}

# Generic words carry no identity in a Commons filename: "Temple.jpg" or
# "St Mary's Church.jpg" could be anywhere.  They are removed before comparing
# an attraction name with a candidate photo filename.
_IMAGE_GENERIC_TOKENS = {
    "temple", "temples", "church", "churches", "mosque", "mosques", "masjid",
    "masjidh", "juma", "cathedral", "chapel", "shrine", "kovil", "kshethram",
    "park", "beach", "dam", "falls", "waterfall", "waterfalls", "lake",
    "garden", "gardens", "palace", "fort", "museum", "viewpoint", "view",
    "point", "hill", "mount", "mountain", "cave", "caves", "island", "river",
    "bridge", "tower", "memorial", "monument", "national", "state",
    "government", "public", "main", "north", "south", "east", "west",
    "upper", "lower", "town", "city", "village", "road", "street",
    "junction", "centre", "center", "attraction", "tourism", "tourist",
    "saint", "santa", "santo", "sree", "sri", "shri", "st", "devi",
    "bhagavathi", "bhadrakali", "maha", "matsya", "matsyavatara",
    "orthodox", "catholic", "syrian", "csi", "jain", "hindu", "muslim",
    "christian", "old", "new", "the", "of", "and", "de", "da", "el", "la",
}

_BAD_URL_FRAGMENTS = (
    "pixel.gif", "spacer.gif", "blank.gif", "transparent.gif",
    "1x1.gif", "1x1.png",
)

# Names that are common enough that a matching photo could be anywhere:
# "St Mary's Church" exists in hundreds of towns.  These require destination
# corroboration before a Pexels photo is accepted.
_PEXELS_AMBIGUOUS_TOKENS = {
    "mary", "marys", "maria", "john", "johns", "george", "joseph", "james",
    "peter", "peters", "paul", "pauls", "michael", "gabriel", "anthony",
    "francis", "teresa", "andrew", "andrews", "thomas", "matthew", "mark",
    "luke", "martin", "patrick", "stephen", "stevens", "helen", "elizabeth",
    "anne", "ann", "central", "victoria", "albert", "edward", "grand",
    "great", "royal", "memorial", "college", "school", "university",
}

_COMPLETED = object()

# Category words used only by the representative-image fallback.  A verified
# photo still has to name the attraction; these terms merely ask a stock or
# nearby-photo provider for the right *kind* of place when none exists.
_CATEGORY_KEYWORDS = (
    ("church", ("church", "cathedral", "basilica", "chapel", "seminary")),
    ("temple", ("temple", "kovil", "kshethram", "shrine", "mandir", "devasthanam")),
    ("mosque", ("mosque", "masjid", "masjidh", "juma", "dargah")),
    ("fort", ("fort", "fortress", "citadel", "castle")),
    ("palace", ("palace", "mahal", "haveli", "mansion")),
    ("museum", ("museum", "gallery", "archive")),
    ("beach", ("beach", "coast", "shore", "seaside")),
    ("waterfall", ("waterfall", "falls", "cascade")),
    ("lake", ("lake", "backwater", "lagoon", "pond")),
    ("park", ("park", "garden", "botanical", "zoo", "sanctuary")),
    ("viewpoint", ("viewpoint", "view point", "lookout", "peak", "hill")),
    ("monument", ("monument", "memorial", "statue", "tower", "gate")),
    ("bridge", ("bridge", "viaduct")),
    ("cave", ("cave", "cavern", "grotto")),
    ("market", ("market", "bazaar", "shopping")),
    ("stadium", ("stadium", "arena", "ground")),
    ("theatre", ("theatre", "theater", "auditorium", "cinema")),
    ("library", ("library", "university", "college", "school", "institute")),
)

_TRAVEL_CARD_PALETTES = (
    ("#0f766e", "#0ea5e9", "#ecfeff"),
    ("#7c3aed", "#db2777", "#fdf2f8"),
    ("#b45309", "#ea580c", "#fff7ed"),
    ("#1d4ed8", "#0891b2", "#eff6ff"),
    ("#166534", "#65a30d", "#f7fee7"),
    ("#9f1239", "#f59e0b", "#fff1f2"),
)


def _category_term(poi: Dict[str, Any]) -> str:
    """Best-guess category word for a representative photo query."""
    text = " ".join(
        _coerce_text(poi.get(key)).lower()
        for key in ("name", "category", "description", "types")
    )
    for term, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return term
    return "landmark"


def _is_display_image(value: Any) -> bool:
    """True when ``value`` can be rendered as an image by the frontend."""
    if normalize_image_url(value):
        return True
    text = _coerce_text(value)
    return text.startswith("data:image/svg+xml;base64,")


def _svg_lines(text: str, limit: int = 28, max_lines: int = 2) -> List[str]:
    """Wrap a label for the generated travel card without clipping words."""
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines - 1:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        lines.append(text[:limit])
    # If the final line still overflows, trim it with an ellipsis.
    if len(lines[-1]) > limit:
        lines[-1] = lines[-1][: limit - 1].rstrip() + "…"
    return lines


def _travel_card_svg(poi: Dict[str, Any]) -> str:
    """Generate a deterministic, self-contained travel card as a data URI.

    This is the final guarantee that a card never renders an empty image area.
    It is deliberately labelled as a generated card, not as a photo.
    """
    name = _coerce_text(poi.get("name")) or "Attraction"
    city = _coerce_text(
        poi.get("city") or poi.get("state_city") or poi.get("region")
    )
    country = _coerce_text(poi.get("country"))
    place = ", ".join(part for part in (city, country) if part) or "ExploreX"
    category = _category_term(poi).replace("_", " ").title()

    seed = sum(ord(ch) for ch in f"{name}|{place}") % len(_TRAVEL_CARD_PALETTES)
    start, end, ink = _TRAVEL_CARD_PALETTES[seed]
    lines = _svg_lines(name)
    title_svg = "".join(
        f'<tspan x="56" dy="{0 if index == 0 else 46}">{html.escape(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    title_y = 286 - (len(lines) - 1) * 23
    safe_place = html.escape(place)
    safe_category = html.escape(category)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450" role="img" aria-label="{html.escape(name)}">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{start}"/>
      <stop offset="100%" stop-color="{end}"/>
    </linearGradient>
    <linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#000000" stop-opacity="0"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0.62"/>
    </linearGradient>
  </defs>
  <rect width="800" height="450" fill="url(#sky)"/>
  <circle cx="640" cy="92" r="46" fill="{ink}" opacity="0.28"/>
  <path d="M0 300 L120 220 L230 286 L340 190 L470 292 L590 214 L720 282 L800 238 L800 450 L0 450 Z" fill="{ink}" opacity="0.16"/>
  <path d="M0 352 L110 292 L225 344 L350 268 L485 348 L610 288 L735 338 L800 310 L800 450 L0 450 Z" fill="{ink}" opacity="0.22"/>
  <rect y="250" width="800" height="200" fill="url(#fade)"/>
  <g font-family="Segoe UI, Arial, sans-serif" fill="#ffffff">
    <text x="56" y="60" font-size="16" font-weight="600" letter-spacing="3" opacity="0.9">{safe_category.upper()}</text>
    <text x="56" y="{title_y}" font-size="38" font-weight="700">{title_svg}</text>
    <text x="56" y="376" font-size="19" opacity="0.92">{safe_place}</text>
    <text x="56" y="414" font-size="13" letter-spacing="2" opacity="0.7">EXPLOREX TRAVEL CARD</text>
  </g>
</svg>"""
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"



def _coerce_text(value: Any) -> str:
    """Return a clean string for any provider value (never ``.lower()`` on int)."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def normalize_image_url(value: Any) -> Optional[str]:
    """Return a safe absolute http(s) image URL, or ``None``.

    Handles the malformed values seen in provider data: surrounding whitespace,
    protocol-relative URLs, ``http://`` on an HTTPS page, empty strings and
    non-string JSON values.  ``data:``, ``javascript:`` and local file URLs are
    rejected rather than passed to an ``<img>`` tag.
    """
    text = _coerce_text(value)
    if not text:
        return None
    text = text.replace("\\/", "/").strip().strip("'\"")
    if text.startswith("//"):
        text = "https:" + text
    if text.lower().startswith("http://"):
        text = "https://" + text[7:]
    if not text.lower().startswith("https://"):
        return None
    if len(text) > 2048 or any(fragment in text.lower() for fragment in _BAD_URL_FRAGMENTS):
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.netloc or "").strip().lower()
    if not host or "." not in host:
        return None
    return text


def _poi_candidate_urls(poi: Dict[str, Any]) -> Iterable[Any]:
    if not isinstance(poi, dict):
        return ()
    values = [poi.get("image_url"), poi.get("image"), poi.get("photo"), poi.get("thumbnail")]
    metadata = poi.get("metadata")
    if isinstance(metadata, dict):
        values.extend([
            metadata.get("image_url"),
            metadata.get("image"),
            metadata.get("photo"),
            metadata.get("thumbnail"),
            metadata.get("wiki_thumbnail"),
        ])
    media = poi.get("media")
    if isinstance(media, dict):
        values.extend([media.get("image_url"), media.get("url")])
    return values


def _tokens(text: Any) -> set:
    value = _coerce_text(text)
    if not value:
        return set()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return {token for token in value.split() if len(token) > 1 and token not in _STOPWORDS}


def _norm_title(text: Any) -> str:
    return re.sub(r"\s+", " ", _coerce_text(text).replace("_", " ")).strip().lower()


def _title_matches(name: Any, title: Any, city: Any = None) -> bool:
    """Conservative title/name match used to verify a Wikipedia result.

    A single shared word is not enough for a multi-word name: searching
    "Sunset Deck" must not return an article about "Sunset Boulevard".  A
    one-word attraction (``"Munnar"``) may match a longer article title.

    A name made only of generic words ("Temple", "CSI Church", "Beach") has no
    identity to verify, so it may only match an article with that exact title.
    Otherwise every church near a place called "Church" would "match".

    ``city`` (when known) prevents a town article from being attached to an
    unrelated place that merely has the town in its name: "LFGHS Munnar" must
    not borrow the "Munnar" town photo.
    """
    name_tokens = _tokens(name)
    title_tokens = _tokens(title)
    if not name_tokens or not title_tokens:
        return False
    name_distinctive = _distinctive_tokens(name)
    if not name_distinctive:
        return _norm_title(name) == _norm_title(title)

    shared = name_tokens & title_tokens
    shared_distinctive = name_distinctive & title_tokens
    if not shared_distinctive:
        # Only type words in common ("temple", "church"): not evidence.
        return False
    city_tokens = _distinctive_tokens(city) if city else set()
    if city_tokens and shared_distinctive <= city_tokens and _norm_title(name) != _norm_title(title):
        # Only the town name is shared.  Accept it only when the POI itself is
        # just the town name and the article title starts with it; otherwise the
        # town's photo would be attached to a specific place inside the town.
        if not (name_distinctive == city_tokens and name_tokens <= title_tokens):
            return False

    if title_tokens <= name_tokens:
        # The article title is a shorter form of the name: that is fine only
        # when the extra words are generic ("Changampuzha Park" -> "Changampuzha").
        extras = name_tokens - title_tokens
        return not extras or extras <= _IMAGE_GENERIC_TOKENS
    if name_tokens <= title_tokens:
        return True
    if len(name_distinctive) == 1:
        return next(iter(name_distinctive)) in title_tokens
    return len(shared) >= 2


def _token_overlap(name: Any, title: Any) -> int:
    return len(_tokens(name) & _tokens(title))


def _distinctive_tokens(text: Any) -> set:
    """Tokens that identify a place, not merely its type ("temple", "beach")."""
    return {token for token in _tokens(text) if token not in _IMAGE_GENERIC_TOKENS}


def _token_matches_text(token: str, text_tokens: set) -> bool:
    """Match a name token, tolerating transliteration/spelling variants.

    Pexels alt text spells Indian place names inconsistently
    ("Ranthambore" / "Ranthambhore").  A long token may match a close variant,
    but short tokens ("fort", "temple") must match exactly.
    """
    if not token:
        return False
    if token in text_tokens:
        return True
    if len(token) >= 6:
        for candidate in text_tokens:
            if len(candidate) >= 6 and SequenceMatcher(None, token, candidate).ratio() >= 0.86:
                return True
    return False


def _pexels_photo_text(photo: Dict[str, Any]) -> str:
    """Text used to decide whether a stock photo is about this attraction."""
    alt = _coerce_text(photo.get("alt"))
    url = _coerce_text(photo.get("url")).rstrip("/")
    slug = url.rsplit("/", 1)[-1] if url else ""
    return f"{alt} {slug.replace('-', ' ')}"


def _pexels_photo_matches(name: Any, location: Any, photo: Dict[str, Any]) -> bool:
    """Return True only when the stock photo text names this attraction.

    This is deliberately strict.  Pexels has thousands of generic temple,
    beach and city photos; attaching one to an unrelated attraction is worse
    than showing the honest placeholder.  The rule is:

    * the attraction must have at least one distinctive token (not "temple");
    * all tokens for a 1-2 word name must appear in the photo text;
    * for longer names a majority must appear;
    * common names ("St Mary's Church", "Victoria Memorial", "Central Park")
      must additionally match the city/state/country in the photo text, or
      they are rejected as likely photos of a same-named place elsewhere.
    """
    if not isinstance(photo, dict):
        return False
    name_tokens = _distinctive_tokens(name)
    if not name_tokens:
        return False
    text_tokens = _tokens(_pexels_photo_text(photo))
    if not text_tokens:
        return False

    matched = sum(1 for token in name_tokens if _token_matches_text(token, text_tokens))
    if matched <= 0:
        return False
    if len(name_tokens) <= 2:
        required = len(name_tokens)
    else:
        required = max(2, round(len(name_tokens) * 0.66))
    if matched < required:
        return False

    ambiguous = bool(name_tokens & _PEXELS_AMBIGUOUS_TOKENS)
    location_tokens = _distinctive_tokens(location)
    if ambiguous:
        # Common names must be corroborated by the destination; otherwise a
        # same-named church overseas would be attached to this attraction.
        return bool(location_tokens) and any(
            _token_matches_text(token, text_tokens) for token in location_tokens
        )
    return True


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math

    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _coerce_coord(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _chunks(values: Sequence[Any], size: int) -> Iterable[List[Any]]:
    for index in range(0, len(values), size):
        yield list(values[index:index + size])


def _coordinate_clusters(
    pois: Iterable[Dict[str, Any]],
    radius_km: float = 8.0,
    max_clusters: int = 32,
    include_overflow: bool = True,
) -> List[Tuple[List[Dict[str, Any]], float, float, float]]:
    """Group POIs that can share one GeoSearch request.

    Returns ``(group, centroid_lat, centroid_lng, request_radius_km)``.  Using a
    real centroid (instead of rounding coordinates to one decimal place) keeps
    every member inside the queried circle.  A request may cover at most 10 km
    because that is MediaWiki's GeoSearch ceiling.

    ``include_overflow`` controls what happens when there are more clusters than
    ``max_clusters``: the Wikipedia stage turns the remainder into one-POI
    clusters so nothing is skipped, while the request-hungry Commons fallback
    keeps a hard request cap (skipped places are simply not negative-cached, so
    a later enrichment can still resolve them).
    """
    points: List[Tuple[Dict[str, Any], float, float]] = []
    for poi in pois:
        if not isinstance(poi, dict) or poi.get("image_url"):
            continue
        lat = _coerce_coord(poi.get("latitude", poi.get("lat")))
        lng = _coerce_coord(poi.get("longitude", poi.get("lng")))
        if lat is None or lng is None:
            continue
        points.append((poi, lat, lng))

    clusters: List[Tuple[List[Dict[str, Any]], float, float, float]] = []
    remaining = points
    while remaining and len(clusters) < max_clusters:
        seed = remaining[0]
        group = [seed]
        rest: List[Tuple[Dict[str, Any], float, float]] = []
        for item in remaining[1:]:
            if _haversine_km(seed[1], seed[2], item[1], item[2]) <= radius_km:
                group.append(item)
            else:
                rest.append(item)
        remaining = rest

        center_lat = sum(item[1] for item in group) / len(group)
        center_lng = sum(item[2] for item in group) / len(group)
        span = max(
            _haversine_km(center_lat, center_lng, item[1], item[2]) for item in group
        )
        clusters.append(([item[0] for item in group], center_lat, center_lng,
                         min(10.0, max(1.5, span + 0.5))))

    if include_overflow:
        for poi, lat, lng in remaining:
            clusters.append(([poi], lat, lng, 1.5))
    return clusters


_IMAGE_FILE_RE = re.compile(r"\.(jpe?g|png|webp|tiff?)$", re.IGNORECASE)


def _is_image_filename(title: Any) -> bool:
    return bool(_IMAGE_FILE_RE.search(_coerce_text(title)))


def _commons_file_confidence(
    name: Any,
    title: Any,
    distance_km: float,
    max_distance_km: float = 0.30,
    city: Any = None,
) -> Optional[Tuple[int, set]]:
    """Return a confidence rank when a Commons file really is this place.

    Guard rails:

    * the file must be geotagged within ``max_distance_km`` of the attraction;
    * its filename must share a *distinctive* token with the attraction name
      ("temple", "church", "beach" do not count);
    * a single-token name needs that whole token, and either a long token or a
      very tight (120 m) proximity;
    * a multi-token name needs two shared tokens, or one long shared token
      within 150 m.

    Anything weaker is rejected, even if a photo happens to be nearby.
    """
    if distance_km > max_distance_km:
        return None
    name_tokens = _distinctive_tokens(name)
    if not name_tokens:
        return None
    file_text = _coerce_text(title)
    if file_text.lower().startswith("file:"):
        file_text = file_text[5:].strip()
    file_text = _IMAGE_FILE_RE.sub("", file_text)
    file_tokens = _distinctive_tokens(file_text)
    overlap = name_tokens & file_tokens
    if not overlap:
        return None
    # A town-name token alone is not evidence when the place has another
    # distinctive token: "LFGHS Munnar" must not borrow a generic "Munnar
    # 4.jpg" photo.  But a place genuinely named after its town ("Fort Kochi
    # Beach") may still match a photo whose only distinctive token is the town.
    city_tokens = _distinctive_tokens(city) if city else set()
    if city_tokens and overlap <= city_tokens and len(name_tokens) > len(overlap):
        return None

    if len(name_tokens) == 1:
        token = next(iter(name_tokens))
        if token in file_tokens and (len(token) >= 6 or distance_km <= 0.12):
            return 1, overlap
        return None
    if len(overlap) >= 2:
        return len(overlap), overlap
    if distance_km <= 0.15 and max(len(token) for token in overlap) >= 6:
        return 1, overlap
    return None


def _commons_url(filename: Any) -> Optional[str]:
    """Build a stable Commons file URL when the API did not return one."""
    name = _coerce_text(filename)
    if not name:
        return None
    if name.lower().startswith("file:"):
        name = name[5:].strip()
    if not name:
        return None
    safe = requests.utils.quote(name.replace(" ", "_"))
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{safe}?width=900"


class AttractionImageResolver:
    """Resolve and cache attraction images without inventing them."""

    def __init__(
        self,
        language: Optional[str] = None,
        timeout: Optional[float] = None,
        min_interval: Optional[float] = None,
        cache_ttl: Optional[float] = None,
        negative_ttl: Optional[float] = None,
        max_lookups: Optional[int] = None,
        pexels_key: Optional[str] = None,
    ):
        self.language = (language or os.environ.get("WIKIPEDIA_LANGUAGE", "en")).strip() or "en"
        self.timeout = self._env_float("ATTRACTION_IMAGE_TIMEOUT", timeout, 5.0)
        self.min_interval = self._env_float("ATTRACTION_IMAGE_MIN_INTERVAL", min_interval, 0.5)
        self.cache_ttl = self._env_float("ATTRACTION_IMAGE_CACHE_TTL", cache_ttl, 7 * 86400)
        self.negative_ttl = self._env_float("ATTRACTION_IMAGE_NEGATIVE_TTL", negative_ttl, 3600)
        self.default_max_lookups = self._env_int("ATTRACTION_IMAGE_MAX_LOOKUPS", max_lookups, 60)
        self.enabled = os.environ.get("ATTRACTION_IMAGE_LOOKUP", "1").strip() != "0"
        # Pexels is the stock-photo fallback *after* every Wikimedia source has
        # been exhausted.  It is disabled automatically when no key is present
        # and can be switched off entirely with ATTRACTION_IMAGE_PEXELS=0.
        self.pexels_key = (
            pexels_key if pexels_key is not None
            else os.environ.get("PEXELS_API_KEY", "")
        )
        self.pexels_key = _coerce_text(self.pexels_key).strip()
        self.pexels_enabled = (
            os.environ.get("ATTRACTION_IMAGE_PEXELS", "1").strip() != "0"
        )
        # Every Explore card must render an image.  Verified photos still win;
        # this only controls the representative/generated fallback chain.
        self.guarantee_images = (
            os.environ.get("ATTRACTION_IMAGE_GUARANTEE", "1").strip() != "0"
        )
        self.pexels_timeout = self._env_float("ATTRACTION_IMAGE_PEXELS_TIMEOUT", None, 5.0)
        self.pexels_max_calls = self._env_int(
            "ATTRACTION_IMAGE_PEXELS_MAX_CALLS", None, 40
        )
        self.pexels_deadline = self._env_float(
            "ATTRACTION_IMAGE_PEXELS_DEADLINE", None, 30.0
        )
        self.pexels_per_page = self._env_int(
            "ATTRACTION_IMAGE_PEXELS_PER_PAGE", None, 15
        )
        self._pexels_calls = 0
        self._pexels_disabled_reason = ""
        self._pexels_cooldown_until = 0.0
        # A stock photo may only represent one attraction.  Track ownership by
        # cache key so a cached positive answer for the same place is reusable.
        self._pexels_owners: Dict[str, str] = {}
        # The MediaWiki search generator is much more expensive than the batch
        # APIs.  Space those requests out; a burst of 2/s is what triggered the
        # HTTP 429s in live runs.
        self.search_min_interval = self._env_float(
            "ATTRACTION_IMAGE_SEARCH_INTERVAL", None, 1.0
        )
        self.commons_geosearch_enabled = (
            os.environ.get("ATTRACTION_IMAGE_COMMONS_GEOSEARCH", "1").strip() != "0"
        )
        # Soft wall-clock ceiling for the request-hungry stages only.  On a bad
        # Wikimedia day the search stage can spend minutes; a plan must not wait
        # for it.  Batch identifier lookups always run.
        self.deadline_seconds = self._env_float("ATTRACTION_IMAGE_DEADLINE", None, 120.0)

        self._cache: Dict[str, Tuple[float, Optional[str], str]] = {}
        self._cache_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._last_search_at = 0.0
        self._budget_lock = threading.Lock()
        self._remaining_lookups = self.default_max_lookups
        self._active_budget = self.default_max_lookups
        # Search is the expensive MediaWiki endpoint.  After a 429 we stop only
        # the search stage for a short while; the batch stages still run.
        self._search_cooldown_until = 0.0
        self._rate_limit_cooldown = self._env_float(
            "ATTRACTION_IMAGE_RATE_LIMIT_COOLDOWN", None, 10.0
        )
        self._search_completed = False
        self._stage_status: Dict[str, Dict[str, Any]] = {}
        self._source_counts: Dict[str, int] = {}
        self._geo_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._commons_geo_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._commons_nearby_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._commons_text_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._destination_image_cache: Dict[str, Optional[str]] = {}
        self.last_status: Dict[str, Any] = {"state": "idle", "resolved": 0, "lookups": 0}
        self._session = requests.Session()
        self._session.headers.update(_WIKI_HEADERS)

    @staticmethod
    def _env_float(name: str, explicit: Optional[float], default: float) -> float:
        if explicit is not None:
            return float(explicit)
        try:
            return float(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _env_int(name: str, explicit: Optional[int], default: int) -> int:
        if explicit is not None:
            return max(0, int(explicit))
        try:
            return max(0, int(os.environ.get(name, str(default))))
        except (TypeError, ValueError):
            return default

    # -- cache -------------------------------------------------------------
    def _cache_key(self, name: str, city: Optional[str]) -> str:
        return f"{str(name or '').strip().lower()}|{str(city or '').strip().lower()}"

    def _cache_get(self, key: str) -> Tuple[bool, Optional[str], str]:
        now = time.time()
        with self._cache_lock:
            entry = self._cache.get(key)
            if not entry:
                return False, None, ""
            expires_at, url, source = entry
            if expires_at <= now:
                self._cache.pop(key, None)
                return False, None, ""
            return True, url, source

    def _cache_put(self, key: str, url: Optional[str], source: str,
                   cache_negative: bool = True) -> None:
        if not url and not cache_negative:
            return
        ttl = self.cache_ttl if url else self.negative_ttl
        with self._cache_lock:
            self._cache[key] = (time.time() + ttl, url, source)
            # Bound memory for a long-running process.
            if len(self._cache) > 4096:
                oldest = sorted(self._cache.items(), key=lambda item: item[1][0])[:512]
                for old_key, _ in oldest:
                    self._cache.pop(old_key, None)

    # -- network -----------------------------------------------------------
    def reset_budget(self, max_lookups: Optional[int] = None) -> None:
        with self._budget_lock:
            self._remaining_lookups = (
                self.default_max_lookups if max_lookups is None else max(0, int(max_lookups))
            )
            self._active_budget = self._remaining_lookups
        self._search_completed = False
        self._stage_status = {}
        self._source_counts = {}
        self._pexels_calls = 0
        self.last_status = {
            "state": "idle",
            "resolved": 0,
            "lookups": 0,
            "budget": self._remaining_lookups,
        }

    def _take_lookup(self) -> bool:
        with self._budget_lock:
            if self._remaining_lookups <= 0:
                return False
            self._remaining_lookups -= 1
            return True

    def _throttle(self) -> None:
        with self._request_lock:
            wait = self.min_interval - (time.time() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.time()

    def _throttle_search(self) -> None:
        """Enforce a slower cadence for ``generator=search`` only."""
        with self._request_lock:
            wait = self.search_min_interval - (time.time() - self._last_search_at)
            if wait > 0:
                time.sleep(wait)
            self._last_search_at = time.time()

    def _stage(self, stage: str, state: str, detail: str = "") -> None:
        self._stage_status[stage] = {"state": state, "detail": detail}

    def _request(self, url: str, params: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None,
                 timeout: Optional[float] = None):
        self._throttle()
        return self._session.get(
            url,
            params=params or {},
            headers=headers or _WIKI_HEADERS,
            timeout=self.timeout if timeout is None else timeout,
        )

    @staticmethod
    def _retry_after(response, default: float = 1.0) -> float:
        try:
            return min(max(float(response.headers.get("Retry-After")), 0.0), 3.0)
        except (TypeError, ValueError, AttributeError):
            return default

    def _get_json(self, url: str, params: Dict[str, Any], stage: str,
                  headers: Optional[Dict[str, str]] = None,
                  timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """One polite JSON request with a single retry on 429/5xx.

        A 429 records a short cooldown for the *search* stage and returns
        ``None``; it never aborts the caller's whole batch.
        """
        max_attempts = 3 if stage == "search" else 2
        for attempt in range(max_attempts):
            try:
                response = self._request(url, params=params, headers=headers, timeout=timeout)
            except requests.exceptions.RequestException as exc:
                self._stage(stage, "error", type(exc).__name__)
                return None

            if response.status_code == 429:
                if attempt < max_attempts - 1:
                    wait = self._retry_after(response, 0.5) if attempt == 0 else 1.5
                    time.sleep(wait)
                    continue
                self._stage(stage, "rate_limited", "HTTP 429")
                if stage == "search":
                    self._search_cooldown_until = time.time() + max(5.0, self._rate_limit_cooldown)
                return None

            if response.status_code >= 500:
                if attempt == 0:
                    time.sleep(0.3)
                    continue
                self._stage(stage, "error", f"HTTP {response.status_code}")
                return None

            if response.status_code != 200:
                self._stage(stage, "empty", f"HTTP {response.status_code}")
                return None

            try:
                payload = response.json()
            except ValueError:
                self._stage(stage, "error", "invalid JSON")
                return None

            # MediaWiki reports some rate limits / backend overload as HTTP 200
            # with an ``error`` member (e.g. ``cirrussearch-too-busy-error``).
            if isinstance(payload, dict) and payload.get("error"):
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                code = _coerce_text(error.get("code")) or "api_error"
                busy = any(
                    marker in code.lower()
                    for marker in ("too-busy", "ratelimit", "rate-limit", "maxlag")
                )
                if busy and attempt < max_attempts - 1:
                    time.sleep(1.5)
                    continue
                self._stage(stage, "rate_limited" if busy else "error", code)
                return None

            self._stage(stage, "ok")
            return payload

        return None

    # -- metadata / identifiers -------------------------------------------
    @staticmethod
    def _sources(poi: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        for key in ("metadata", "osm_tags"):
            value = poi.get(key)
            if isinstance(value, dict):
                yield value
        metadata = poi.get("metadata")
        if isinstance(metadata, dict):
            tags = metadata.get("osm_tags")
            if isinstance(tags, dict):
                yield tags

    def _metadata_image(self, poi: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Return ``(url, commons_file)`` from provider/OSM metadata."""
        for source in self._sources(poi):
            for key in ("image", "image_url", "photo", "wiki_image", "wiki_thumbnail"):
                value = source.get(key)
                url = normalize_image_url(value)
                if url:
                    return url, None
            commons = _coerce_text(source.get("wikimedia_commons"))
            if commons:
                return None, commons
        return None, None

    def _wikipedia_title(self, poi: Dict[str, Any]) -> str:
        for source in self._sources(poi):
            value = _coerce_text(source.get("wikipedia"))
            if not value:
                continue
            if ":" in value:
                lang, title = value.split(":", 1)
                if lang.strip().lower() not in ("", self.language.lower()):
                    continue
                value = title
            return value.strip()
        return ""

    def _wikidata_id(self, poi: Dict[str, Any]) -> str:
        for source in self._sources(poi):
            value = _coerce_text(source.get("wikidata_id") or source.get("wikidata"))
            if value.upper().startswith("Q") and value[1:].isdigit():
                return value.upper()
        return ""

    def _commons_value(self, poi: Dict[str, Any]) -> str:
        for source in self._sources(poi):
            value = _coerce_text(source.get("wikimedia_commons") or source.get("commons"))
            if value:
                return value
        return ""

    @staticmethod
    def _osm_ref(poi: Dict[str, Any]) -> Tuple[str, str]:
        metadata = poi.get("metadata") if isinstance(poi.get("metadata"), dict) else {}
        otype = _coerce_text(metadata.get("osm_type") or poi.get("osm_type")).lower()
        oid = _coerce_text(metadata.get("osm_id") or poi.get("osm_id"))
        if not oid:
            raw = _coerce_text(poi.get("id"))
            if raw.startswith("osm_"):
                oid = raw[4:]
        mapping = {"n": "node", "w": "way", "r": "relation",
                   "nodes": "node", "ways": "way", "relations": "relation"}
        otype = mapping.get(otype, otype)
        if otype in ("node", "way", "relation") and oid.isdigit():
            return otype, oid
        return "", ""

    def _apply_metadata(self, poi: Dict[str, Any], source_name: str) -> Optional[str]:
        url, commons = self._metadata_image(poi)
        if url:
            self._set_image(poi, url, source_name)
            return url
        return None

    def _set_image(self, poi: Dict[str, Any], url: Optional[str], source: str,
                   representative: bool = False) -> None:
        if not url or poi.get("image_url"):
            # First verified match wins: a later, weaker stage must never
            # overwrite an image that an exact identifier already supplied.
            return
        poi["image_url"] = url
        poi.setdefault("image_source", source)
        if representative:
            poi["image_is_representative"] = True
        self._source_counts[source] = self._source_counts.get(source, 0) + 1

    # -- OSM tags ----------------------------------------------------------
    def _hydrate_osm_tags(self, pois: List[Dict[str, Any]]) -> None:
        """Fetch ``image`` / ``wikipedia`` / ``wikidata`` tags in bulk.

        Geoapify returns an OSM reference but only a subset of tags, and
        Overpass may be unavailable entirely.  The OSM API accepts up to 100
        element ids per request, so this is a handful of calls, not one per
        attraction.  Failure is non-fatal.
        """
        groups: Dict[str, List[Tuple[Dict[str, Any], str]]] = {}
        for poi in pois:
            if self._metadata_image(poi)[0]:
                continue
            if self._wikipedia_title(poi) or self._wikidata_id(poi) or self._commons_value(poi):
                continue
            otype, oid = self._osm_ref(poi)
            if otype:
                groups.setdefault(otype, []).append((poi, oid))

        for otype, entries in groups.items():
            plural = {"node": "nodes", "way": "ways", "relation": "relations"}[otype]
            by_id = {oid: poi for poi, oid in entries}
            for chunk in _chunks(list(by_id), 100):
                payload = self._get_json(
                    f"{_OSM_API}/{plural}",
                    {plural: ",".join(chunk)},
                    "osm",
                    headers=_OSM_HEADERS,
                    timeout=max(self.timeout, 10.0),
                )
                if not isinstance(payload, dict):
                    continue
                for element in payload.get("elements") or []:
                    if not isinstance(element, dict):
                        continue
                    poi = by_id.get(_coerce_text(element.get("id")))
                    if poi is None:
                        continue
                    tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
                    metadata = poi.get("metadata") if isinstance(poi.get("metadata"), dict) else {}
                    metadata["osm_type"] = otype
                    metadata["osm_id"] = _coerce_text(element.get("id"))
                    metadata.setdefault("osm_tags", {})
                    if isinstance(metadata["osm_tags"], dict):
                        metadata["osm_tags"].update(tags)
                    for key, target in (
                        ("wikidata", "wikidata_id"),
                        ("wikipedia", "wikipedia"),
                        ("wikimedia_commons", "wikimedia_commons"),
                        ("image", "image"),
                    ):
                        if tags.get(key):
                            metadata[target] = _coerce_text(tags[key])
                    poi["metadata"] = metadata

        for poi in pois:
            self._apply_metadata(poi, "provider")

    # -- exact identifiers: Wikipedia titles -------------------------------
    def _resolve_wikipedia_titles(self, pois: List[Dict[str, Any]]) -> None:
        titles: Dict[str, List[Dict[str, Any]]] = {}
        for poi in pois:
            if poi.get("image_url"):
                continue
            title = self._wikipedia_title(poi)
            if title:
                titles.setdefault(title, []).append(poi)
        if not titles:
            return

        for chunk in _chunks(list(titles), 50):
            payload = self._get_json(
                _WIKIPEDIA_API.format(lang=self.language),
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "titles": "|".join(chunk),
                    "redirects": 1,
                    "prop": "pageimages|pageprops|coordinates",
                    "piprop": "thumbnail",
                    "pithumbsize": 900,
                },
                "wikipedia_title",
            )
            if not isinstance(payload, dict):
                continue
            query = payload.get("query") or {}
            alias: Dict[str, str] = {}
            for item in query.get("normalized") or []:
                if isinstance(item, dict):
                    alias[_norm_title(item.get("from"))] = _norm_title(item.get("to"))
            for item in query.get("redirects") or []:
                if isinstance(item, dict):
                    alias[_norm_title(item.get("from"))] = _norm_title(item.get("to"))

            pages: Dict[str, Dict[str, Any]] = {}
            raw_pages = query.get("pages") or []
            if isinstance(raw_pages, dict):
                raw_pages = list(raw_pages.values())
            for page in raw_pages:
                if isinstance(page, dict) and page.get("title"):
                    pages[_norm_title(page.get("title"))] = page

            def _follow(title: str) -> str:
                seen = set()
                current = _norm_title(title)
                while current in alias and current not in seen:
                    seen.add(current)
                    current = alias[current]
                return current

            for requested, targets in titles.items():
                if requested not in chunk:
                    continue
                page = pages.get(_follow(requested)) or pages.get(_norm_title(requested))
                if not page or "disambiguation" in (page.get("pageprops") or {}):
                    continue
                image = normalize_image_url((page.get("thumbnail") or {}).get("source"))
                if image:
                    for poi in targets:
                        self._set_image(poi, image, "wikipedia_title")
                # A page without a lead image can still point at a Wikidata
                # entity whose P18 image we can use in the next stage.
                wikibase = _coerce_text((page.get("pageprops") or {}).get("wikibase_item"))
                if wikibase and not image:
                    for poi in targets:
                        metadata = poi.get("metadata") if isinstance(poi.get("metadata"), dict) else {}
                        metadata.setdefault("wikidata_id", wikibase)
                        poi["metadata"] = metadata

    # -- exact identifiers: Wikidata P18 ----------------------------------
    def _resolve_wikidata(self, pois: List[Dict[str, Any]]) -> None:
        id_map: Dict[str, List[Dict[str, Any]]] = {}
        for poi in pois:
            if poi.get("image_url"):
                continue
            qid = self._wikidata_id(poi)
            if qid:
                id_map.setdefault(qid, []).append(poi)
        if not id_map:
            return

        file_map: Dict[str, List[Dict[str, Any]]] = {}
        category_map: Dict[str, List[Dict[str, Any]]] = {}
        for chunk in _chunks(list(id_map), 50):
            payload = self._get_json(
                _WIKIDATA_API,
                {
                    "action": "wbgetentities",
                    "format": "json",
                    "ids": "|".join(chunk),
                    "props": "claims",
                },
                "wikidata",
            )
            if not isinstance(payload, dict):
                continue
            entities = payload.get("entities")
            if not isinstance(entities, dict):
                continue
            for qid, entity in entities.items():
                if not isinstance(entity, dict):
                    continue
                claims = entity.get("claims") if isinstance(entity.get("claims"), dict) else {}
                for claim in claims.get("P18") or []:
                    try:
                        value = claim["mainsnak"]["datavalue"]["value"]
                    except (KeyError, TypeError):
                        continue
                    name = _coerce_text(value)
                    if name:
                        file_map.setdefault(name, []).extend(id_map.get(qid, []))
                        break
                for claim in claims.get("P373") or []:
                    try:
                        value = claim["mainsnak"]["datavalue"]["value"]
                    except (KeyError, TypeError):
                        continue
                    name = _coerce_text(value)
                    if name:
                        category_map.setdefault(name, []).extend(id_map.get(qid, []))
                        break

        if file_map:
            self._resolve_commons_files(file_map)
        if category_map:
            self._resolve_commons_categories(category_map, limit=8)

    # -- exact identifiers: Commons files/categories ----------------------
    def _resolve_commons_files(self, file_map: Dict[str, List[Dict[str, Any]]]) -> None:
        for chunk in _chunks(list(file_map), 50):
            titles = [name if name.lower().startswith("file:") else f"File:{name}" for name in chunk]
            payload = self._get_json(
                _COMMONS_API,
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "titles": "|".join(titles),
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": 900,
                },
                "commons_file",
            )
            if not isinstance(payload, dict):
                continue
            pages = ((payload.get("query") or {}).get("pages") or [])
            if isinstance(pages, dict):
                pages = list(pages.values())
            by_title: Dict[str, str] = {}
            for page in pages:
                if not isinstance(page, dict) or not page.get("title"):
                    continue
                info = page.get("imageinfo") or []
                url = None
                if info and isinstance(info[0], dict):
                    url = normalize_image_url(info[0].get("thumburl") or info[0].get("url"))
                if url:
                    by_title[_norm_title(page.get("title"))] = url
            for name in chunk:
                url = by_title.get(_norm_title(f"File:{name}")) or by_title.get(_norm_title(name))
                if url:
                    for poi in file_map.get(name, []):
                        self._set_image(poi, url, "commons")

    def _resolve_commons_categories(self, category_map: Dict[str, List[Dict[str, Any]]],
                                    limit: int = 8) -> None:
        for index, (category, targets) in enumerate(category_map.items()):
            if index >= limit:
                break
            title = category if category.lower().startswith("category:") else f"Category:{category}"
            payload = self._get_json(
                _COMMONS_API,
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "generator": "categorymembers",
                    "gcmtitle": title,
                    "gcmtype": "file",
                    "gcmlimit": 5,
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": 900,
                },
                "commons_category",
            )
            if not isinstance(payload, dict):
                continue
            pages = ((payload.get("query") or {}).get("pages") or [])
            if isinstance(pages, dict):
                pages = list(pages.values())
            for page in pages:
                if not isinstance(page, dict):
                    continue
                info = page.get("imageinfo") or []
                if not info or not isinstance(info[0], dict):
                    continue
                url = normalize_image_url(info[0].get("thumburl") or info[0].get("url"))
                if url:
                    for poi in targets:
                        self._set_image(poi, url, "commons_category")
                    break

    def _resolve_commons_values(self, pois: List[Dict[str, Any]]) -> None:
        files: Dict[str, List[Dict[str, Any]]] = {}
        categories: Dict[str, List[Dict[str, Any]]] = {}
        for poi in pois:
            if poi.get("image_url"):
                continue
            value = self._commons_value(poi)
            if not value:
                continue
            lowered = value.lower()
            if lowered.startswith("file:") or re.search(r"\.(jpe?g|png|gif|webp|tiff?)$", lowered):
                files.setdefault(value, []).append(poi)
            elif lowered.startswith("category:") or not re.search(r"\.\w{2,5}$", lowered):
                categories.setdefault(value, []).append(poi)
        if files:
            self._resolve_commons_files(files)
        if categories:
            # A file match from the same tag may already have resolved a POI;
            # only query categories for the places still missing a photo.
            remaining = {
                value: [poi for poi in group if not poi.get("image_url")]
                for value, group in categories.items()
            }
            remaining = {value: group for value, group in remaining.items() if group}
            if remaining:
                self._resolve_commons_categories(remaining)

    # -- nearby Commons photos (one request per cluster) ------------------
    def _resolve_commons_geosearch(self, pois: List[Dict[str, Any]]) -> None:
        """Find real photos of small/local places from geotagged Commons files.

        A Commons file is only used when it is geotagged within 300 m of the
        attraction and its filename shares a distinctive name token.  Generic
        filenames (``Temple.jpg``, ``Church 02.jpg``) are rejected.  Files are
        matched by coordinates first, then their URLs are fetched in batches of
        50, so this costs at most eight cluster requests plus one request per 50
        hits - a hard ceiling, because this is the last and most request-hungry
        fallback.
        """
        if not self.commons_geosearch_enabled:
            self._stage("commons_geosearch", "disabled", "ATTRACTION_IMAGE_COMMONS_GEOSEARCH=0")
            return
        clusters = _coordinate_clusters(
            pois, radius_km=8.0, max_clusters=32, include_overflow=False
        )
        # Keep the eight clusters that cover the most places; one request then
        # covers a whole group instead of a single attraction.
        clusters.sort(key=lambda item: len(item[0]), reverse=True)
        clusters = clusters[:8]
        candidates: Dict[str, List[Dict[str, Any]]] = {}
        for group, center_lat, center_lng, radius_km in clusters:
            cache_key = f"commons:{center_lat:.3f},{center_lng:.3f},{radius_km:.1f}"
            pages = self._commons_geo_cache.get(cache_key)
            if pages is None:
                payload = self._get_json(
                    _COMMONS_API,
                    {
                        "action": "query",
                        "format": "json",
                        "formatversion": 2,
                        "generator": "geosearch",
                        "ggscoord": f"{center_lat:.4f}|{center_lng:.4f}",
                        "ggsradius": int(radius_km * 1000),
                        "ggslimit": 500,
                        "ggsnamespace": 6,
                        "prop": "coordinates",
                        "colimit": 500,
                    },
                    "commons_geosearch",
                    timeout=max(self.timeout, 10.0),
                )
                if payload is None:
                    # A failed/rate-limited request must not be cached as a
                    # permanent "no photo here" answer.
                    continue
                raw_pages = ((payload or {}).get("query") or {}).get("pages") or []
                if isinstance(raw_pages, dict):
                    raw_pages = list(raw_pages.values())
                pages = [page for page in raw_pages if isinstance(page, dict)]
                self._commons_geo_cache[cache_key] = pages

            for poi in group:
                if poi.get("image_url"):
                    continue
                name = _coerce_text(poi.get("name"))
                lat = _coerce_coord(poi.get("latitude", poi.get("lat")))
                lng = _coerce_coord(poi.get("longitude", poi.get("lng")))
                if not name or lat is None or lng is None:
                    continue
                city = _coerce_text(
                    poi.get("state_city") or poi.get("city") or poi.get("region")
                )
                best = None
                best_rank: Optional[Tuple[int, float]] = None
                for page in pages:
                    title = _coerce_text(page.get("title"))
                    if not _is_image_filename(title):
                        continue
                    coords = page.get("coordinates") or []
                    if not coords or not isinstance(coords[0], dict):
                        continue
                    page_lat = _coerce_coord(coords[0].get("lat"))
                    page_lng = _coerce_coord(coords[0].get("lon"))
                    if page_lat is None or page_lng is None:
                        continue
                    distance = _haversine_km(lat, lng, page_lat, page_lng)
                    confidence = _commons_file_confidence(
                        name, title, distance, city=city
                    )
                    if not confidence:
                        continue
                    rank = (confidence[0], -distance)
                    if best_rank is None or rank > best_rank:
                        best_rank = rank
                        best = title
                if best:
                    candidates.setdefault(best, []).append(poi)

        if not candidates:
            return

        for chunk in _chunks(list(candidates), 50):
            payload = self._get_json(
                _COMMONS_API,
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "titles": "|".join(chunk),
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": 900,
                },
                "commons_geosearch_file",
                timeout=max(self.timeout, 10.0),
            )
            by_title: Dict[str, str] = {}
            raw_pages = ((payload or {}).get("query") or {}).get("pages") or []
            if isinstance(raw_pages, dict):
                raw_pages = list(raw_pages.values())
            for page in raw_pages:
                if not isinstance(page, dict) or not page.get("title"):
                    continue
                info = page.get("imageinfo") or []
                url = None
                if info and isinstance(info[0], dict):
                    url = normalize_image_url(info[0].get("thumburl") or info[0].get("url"))
                by_title[_norm_title(page.get("title"))] = url or ""
            for title in chunk:
                url = by_title.get(_norm_title(title)) or _commons_url(title)
                if not url:
                    continue
                for poi in candidates.get(title, []):
                    self._set_image(poi, url, "commons_geosearch")

    # -- nearby notable articles (one request per cluster) -----------------
    def _resolve_geosearch(self, pois: List[Dict[str, Any]]) -> None:
        clusters = _coordinate_clusters(pois, radius_km=8.0)
        for group, center_lat, center_lng, radius_km in clusters:
            cache_key = f"wiki:{center_lat:.3f},{center_lng:.3f},{radius_km:.1f}"
            pages = self._geo_cache.get(cache_key)
            if pages is None:
                payload = self._get_json(
                    _WIKIPEDIA_API.format(lang=self.language),
                    {
                        "action": "query",
                        "format": "json",
                        "formatversion": 2,
                        "generator": "geosearch",
                        "ggscoord": f"{center_lat:.4f}|{center_lng:.4f}",
                        "ggsradius": int(radius_km * 1000),
                        "ggslimit": 500,
                        "ggsnamespace": 0,
                        "prop": "pageimages|coordinates",
                        "piprop": "thumbnail",
                        "pithumbsize": 900,
                        "colimit": 500,
                    },
                    "geosearch",
                    timeout=max(self.timeout, 10.0),
                )
                if payload is None:
                    # Do not cache a failed/rate-limited lookup as a negative.
                    continue
                raw_pages = ((payload or {}).get("query") or {}).get("pages") or []
                if isinstance(raw_pages, dict):
                    raw_pages = list(raw_pages.values())
                pages = [page for page in raw_pages if isinstance(page, dict)]
                self._geo_cache[cache_key] = pages

            for poi in group:
                if poi.get("image_url"):
                    continue
                name = _coerce_text(poi.get("name"))
                lat = _coerce_coord(poi.get("latitude", poi.get("lat")))
                lng = _coerce_coord(poi.get("longitude", poi.get("lng")))
                if not name or lat is None or lng is None:
                    continue
                city = _coerce_text(
                    poi.get("state_city") or poi.get("city") or poi.get("region")
                )
                best = None
                best_score = None
                for page in pages:
                    image = normalize_image_url((page.get("thumbnail") or {}).get("source"))
                    title = _coerce_text(page.get("title"))
                    if not image or not title or not _title_matches(name, title, city):
                        continue
                    coords = page.get("coordinates") or []
                    if coords and isinstance(coords[0], dict):
                        page_lat = _coerce_coord(coords[0].get("lat"))
                        page_lng = _coerce_coord(coords[0].get("lon"))
                        if page_lat is not None and page_lng is not None:
                            distance = _haversine_km(lat, lng, page_lat, page_lng)
                            if distance > 3.0:
                                continue
                        elif _norm_title(name) == _norm_title(title):
                            distance = 3.0
                        else:
                            # The page was found near the cluster centroid but
                            # has no coordinates of its own; only an exact title
                            # is safe enough to attach its photo.
                            continue
                    elif _norm_title(name) == _norm_title(title):
                        distance = 3.0
                    else:
                        continue
                    score = (_token_overlap(name, title), -distance)
                    if best_score is None or score > best_score:
                        best_score = score
                        best = image
                if best:
                    self._set_image(poi, best, "geosearch")

    # -- Wikipedia search fallback ----------------------------------------
    @staticmethod
    def _search_sort_key(poi: Dict[str, Any]) -> Tuple[int, int, int]:
        """Spend the scarce search budget on names that can identify an article.

        ``Temple`` / ``Church`` / ``CSI Church`` have no distinctive token and
        almost never produce a usable article; searching them wastes quota that
        a named landmark could use.
        """
        name = _coerce_text(poi.get("name")) if isinstance(poi, dict) else ""
        distinctive = _distinctive_tokens(name)
        return (0 if distinctive else 1, -len(distinctive), -len(name))

    def _search_wikipedia(self, name: str, city: Optional[str], lat: Optional[float] = None,
                          lng: Optional[float] = None) -> Tuple[Optional[str], bool]:
        """Search Wikipedia; return ``(image_url, completed)``.

        ``completed`` is ``False`` when the request was skipped or rate-limited,
        so the caller must not cache a negative answer.
        """
        if not self.enabled:
            self.last_status = {"state": "disabled", "resolved": 0, "lookups": 0}
            return None, False
        if time.time() < self._search_cooldown_until:
            self._stage("search", "rate_limited", "cooldown")
            return None, False

        search_term = " ".join(part for part in (name, city) if part).strip()
        if not search_term:
            return None, False

        # The search generator is the endpoint that rate-limits first.
        self._throttle_search()
        payload = self._get_json(
            _WIKIPEDIA_API.format(lang=self.language),
            {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "generator": "search",
                "gsrsearch": search_term,
                "gsrnamespace": 0,
                "gsrlimit": 5,
                "prop": "pageimages|pageprops|coordinates|info",
                "piprop": "thumbnail",
                "pithumbsize": 900,
                "inprop": "url",
                "redirects": 1,
            },
            "search",
        )
        if not isinstance(payload, dict):
            return None, False

        pages = ((payload.get("query") or {}).get("pages") or [])
        if isinstance(pages, dict):
            pages = list(pages.values())

        for page in pages:
            if not isinstance(page, dict):
                continue
            if "disambiguation" in (page.get("pageprops") or {}):
                continue
            title = _coerce_text(page.get("title"))
            image = normalize_image_url((page.get("thumbnail") or {}).get("source"))
            if not title or not image or not _title_matches(name, title, city):
                continue
            coords = page.get("coordinates") or []
            if coords and lat is not None and lng is not None:
                page_lat = _coerce_coord(coords[0].get("lat"))
                page_lng = _coerce_coord(coords[0].get("lon"))
                if page_lat is not None and page_lng is not None:
                    # Tight radius: a same-named church in another town is not
                    # this attraction.  (The old 60 km check was too permissive.)
                    if _haversine_km(lat, lng, page_lat, page_lng) > 5.0:
                        continue
            elif _norm_title(name) != _norm_title(title):
                # Without coordinates, only an exact title is safe.
                continue
            return image, True

        return None, True

    # -- Pexels stock-photo fallback --------------------------------------
    def _pexels_cache_key(self, name: str, city: Optional[str]) -> str:
        """Separate cache namespace from the Wikimedia negative answers.

        A Wikipedia miss must never prevent the Pexels fallback from running,
        and a Pexels miss must not be mistaken for a verified Wikimedia answer.
        """
        return "pexels|" + self._cache_key(name, city)

    def _pexels_queries(self, name: str, city: Optional[str],
                        poi: Dict[str, Any]) -> List[str]:
        """Build at most two attraction-specific queries."""
        contexts: List[str] = []
        for value in (
            city,
            poi.get("state"),
            poi.get("region"),
            poi.get("country"),
        ):
            text = _coerce_text(value)
            if text and _norm_title(text) not in {_norm_title(c) for c in contexts}:
                contexts.append(text)

        queries: List[str] = []
        name_norm = _norm_title(name)
        for context in contexts:
            if _norm_title(context) in name_norm:
                continue
            query = f"{name} {context}".strip()
            if _norm_title(query) not in {_norm_title(q) for q in queries}:
                queries.append(query)
            if len(queries) >= 2:
                break
        if not queries:
            queries.append(name)
        return queries

    def _pexels_search(self, query: str, name: str, location: str,
                       owner_key: str, strict: bool = True) -> Tuple[Optional[str], bool]:
        """Return ``(image_url, completed)`` for one Pexels query.

        ``strict`` keeps the verified behaviour: the photo text must name the
        attraction (plus destination corroboration for ambiguous names).  The
        representative fallback calls with ``strict=False`` and accepts the
        best unused landscape result, preferring a text match when one exists.

        ``completed`` is False for configuration, transport and rate-limit
        failures, so a transient outage is never cached as a permanent miss.
        """
        if not self.pexels_enabled or not self.pexels_key:
            self._stage("pexels", "disabled", "not_configured")
            return None, False
        if self._pexels_disabled_reason:
            self._stage("pexels", "disabled", self._pexels_disabled_reason)
            return None, False
        if time.time() < self._pexels_cooldown_until:
            self._stage("pexels", "rate_limited", "cooldown")
            return None, False
        if self._pexels_calls >= self.pexels_max_calls:
            self._stage("pexels", "skipped", "budget")
            return None, False

        self._pexels_calls += 1
        headers = {
            "Authorization": self.pexels_key,
            "Accept": "application/json",
            "User-Agent": _WIKI_HEADERS["User-Agent"],
        }
        params = {
            "query": query,
            "per_page": max(5, min(40, self.pexels_per_page)),
            "orientation": "landscape",
        }
        try:
            response = self._session.get(
                _PEXELS_API, headers=headers, params=params,
                timeout=self.pexels_timeout,
            )
        except Exception as exc:
            self._stage("pexels", "error", type(exc).__name__)
            return None, False

        status = getattr(response, "status_code", None)
        if status in (401, 403):
            # Bad/revoked key: stop trying for the rest of the process.
            self._pexels_disabled_reason = f"http_{status}"
            self._stage("pexels", "disabled", self._pexels_disabled_reason)
            return None, False
        if status == 429:
            try:
                retry_after = float(response.headers.get("Retry-After"))
            except (AttributeError, TypeError, ValueError):
                retry_after = 5.0
            self._pexels_cooldown_until = time.time() + min(max(retry_after, 1.0), 60.0)
            self._stage("pexels", "rate_limited", "http_429")
            return None, False
        if status != 200:
            self._stage("pexels", "error", f"http_{status}")
            return None, False

        try:
            payload = response.json()
        except Exception:
            self._stage("pexels", "error", "invalid_json")
            return None, False
        photos = payload.get("photos") if isinstance(payload, dict) else None
        if not isinstance(photos, list):
            self._stage("pexels", "error", "invalid_payload")
            return None, False

        # In representative mode, a photo whose own text names the attraction
        # is still preferred.  Next prefer one that at least shows the right
        # category in the right destination, so "church Kottayam" does not
        # return a lighthouse just because it was the first result.
        ordered_photos = photos
        if not strict:
            category = _category_term({"name": name})
            location_tokens = {
                token for token in _tokens(location) if len(token) > 2
            }

            def _representative_rank(photo: Any) -> int:
                if not isinstance(photo, dict):
                    return 9
                if _pexels_photo_matches(name, location, photo):
                    return 0
                text = _pexels_photo_text(photo).lower()
                category_hit = category != "landmark" and category in text
                location_hit = any(token in text for token in location_tokens)
                if category_hit and location_hit:
                    return 1
                if category_hit:
                    return 2
                if location_hit:
                    return 3
                return 4

            ordered_photos = sorted(photos, key=_representative_rank)

        for photo in ordered_photos:
            if not isinstance(photo, dict):
                continue
            if strict and not _pexels_photo_matches(name, location, photo):
                continue
            src = photo.get("src") if isinstance(photo.get("src"), dict) else {}
            # Identify the photo independently of its size-specific URL.  Large
            # and medium renditions are the same stock image and must not be
            # attached to two different attractions.
            photo_key = (
                _coerce_text(photo.get("id"))
                or _coerce_text(photo.get("url"))
                or _coerce_text(src.get("large"))
                or _coerce_text(src.get("medium"))
            )
            if photo_key:
                with self._cache_lock:
                    owner = self._pexels_owners.get(photo_key)
                if owner and owner != owner_key:
                    # The same stock photo must not illustrate two attractions.
                    continue
            for size in ("large", "landscape", "medium", "original"):
                url = normalize_image_url(src.get(size))
                if not url:
                    continue
                if photo_key:
                    with self._cache_lock:
                        self._pexels_owners[photo_key] = owner_key
                if strict:
                    self._stage("pexels", "ok", "relevant_match")
                else:
                    self._stage("pexels", "ok", "representative_match")
                return url, True

        self._stage("pexels", "empty", "no_relevant_result")
        return None, True

    def _resolve_pexels(self, pois: Iterable[Dict[str, Any]],
                        city: Optional[str] = None) -> None:
        """Fill remaining POIs from Pexels, with strict relevance checks."""
        pending = [
            poi for poi in (pois or [])
            if isinstance(poi, dict) and not normalize_image_url(poi.get("image_url"))
        ]
        if not pending:
            self._stage("pexels", "skipped", "no_missing_image")
            return
        if not self.enabled:
            self._stage("pexels", "disabled", "lookup_disabled")
            return
        if not self.pexels_enabled:
            self._stage("pexels", "disabled", "provider_disabled")
            return
        if not self.pexels_key:
            self._stage("pexels", "disabled", "no_api_key")
            return
        if self._pexels_disabled_reason:
            self._stage("pexels", "disabled", self._pexels_disabled_reason)
            return

        started_at = time.time()
        resolved_here = 0
        for poi in pending:
            if (
                self.pexels_deadline > 0
                and (time.time() - started_at) > self.pexels_deadline
            ):
                self._stage("pexels", "skipped", "deadline")
                break
            if time.time() < self._pexels_cooldown_until:
                self._stage("pexels", "rate_limited", "cooldown")
                break

            name = _coerce_text(poi.get("name"))
            if not name or not _distinctive_tokens(name):
                # "Temple" / "Beach" cannot identify a specific stock photo.
                continue
            lookup_city = (
                _coerce_text(poi.get("city") or poi.get("state_city"))
                or _coerce_text(city)
            )
            location = " ".join(
                part for part in (
                    lookup_city,
                    _coerce_text(poi.get("state")),
                    _coerce_text(poi.get("region")),
                    _coerce_text(poi.get("country")),
                ) if part
            )
            key = self._cache_key(name, lookup_city)
            pexels_key = self._pexels_cache_key(name, lookup_city)

            hit, cached_url, _source = self._cache_get(pexels_key)
            if hit:
                if cached_url:
                    self._set_image(poi, cached_url, "pexels")
                    with self._cache_lock:
                        self._pexels_owners.setdefault(cached_url, pexels_key)
                        resolved_here += 1
                continue

            queries = self._pexels_queries(name, lookup_city, poi)
            found_url = None
            attempted = 0
            completed_count = 0
            for query in queries:
                attempted += 1
                url, query_completed = self._pexels_search(
                    query, name, location, pexels_key
                )
                if query_completed:
                    completed_count += 1
                if url:
                    found_url = url
                    break
                if self._pexels_disabled_reason:
                    break
                if time.time() < self._pexels_cooldown_until:
                    break
                if self._pexels_calls >= self.pexels_max_calls:
                    break

            if found_url:
                self._set_image(poi, found_url, "pexels")
                self._cache_put(pexels_key, found_url, "pexels")
                # Also populate the normal lookup cache so /api/attraction-image
                # and a later enrich() do not repeat the request.
                self._cache_put(key, found_url, "pexels")
                with self._cache_lock:
                    self._pexels_owners[found_url] = pexels_key
                resolved_here += 1
            elif attempted and completed_count == attempted:
                # Every query completed with no relevant photo: a real miss.
                # A timeout/429 on any query is retried on the next request.
                self._cache_put(pexels_key, None, "none")

        if "pexels" not in self._stage_status:
            self._stage("pexels", "ok" if resolved_here else "empty",
                        f"{resolved_here} matched")
        else:
            state = self._stage_status.get("pexels", {}).get("state", "")
            if state not in ("disabled", "rate_limited", "error", "skipped"):
                self._stage("pexels", "ok" if resolved_here else "empty",
                            f"{resolved_here} matched")

    # -- guaranteed representative images ---------------------------------
    def _representative_query(self, poi: Dict[str, Any], city: Optional[str]) -> str:
        """Build a category + destination query for a representative photo."""
        lookup_city = (
            _coerce_text(poi.get("city") or poi.get("state_city") or poi.get("region"))
            or _coerce_text(city)
        )
        country = _coerce_text(poi.get("country"))
        term = _category_term(poi)
        name = _coerce_text(poi.get("name"))
        if term == "landmark" and not lookup_city:
            return name
        parts = [term, lookup_city, country]
        seen: List[str] = []
        for part in parts:
            if part and part.lower() not in {item.lower() for item in seen}:
                seen.append(part)
        return " ".join(seen)

    def _resolve_commons_text_search(self, pois: List[Dict[str, Any]],
                                     city: Optional[str] = None,
                                     max_requests: int = 8) -> None:
        """Find a Commons file by name + destination when geosearch missed it.

        Unlike the verified stages, this accepts a file that matches the city
        and the place *type* (a church in Kottayam, a temple in Alappuzha) even
        when it does not spell out the attraction's full name.  Results are
        marked representative and every file is used at most once.
        """
        pending = [poi for poi in (pois or []) if not _is_display_image(poi.get("image_url"))]
        if not pending:
            return

        used_titles = set()
        requests_made = 0
        for poi in pending:
            if requests_made >= max_requests:
                break
            if _is_display_image(poi.get("image_url")):
                continue
            name = _coerce_text(poi.get("name"))
            if not name:
                continue
            lookup_city = (
                _coerce_text(poi.get("city") or poi.get("state_city") or poi.get("region"))
                or _coerce_text(city)
            )
            query = " ".join(part for part in (name, lookup_city) if part)
            cache_key = _norm_title(query)
            pages = self._commons_text_cache.get(cache_key)
            if pages is None:
                requests_made += 1
                payload = self._get_json(
                    _COMMONS_API,
                    {
                        "action": "query",
                        "format": "json",
                        "formatversion": 2,
                        "generator": "search",
                        "gsrsearch": query,
                        "gsrnamespace": 6,
                        "gsrlimit": 8,
                        "prop": "imageinfo",
                        "iiprop": "url",
                        "iiurlwidth": 900,
                    },
                    "commons_search",
                    timeout=max(self.timeout, 10.0),
                )
                if payload is None:
                    continue
                raw_pages = ((payload or {}).get("query") or {}).get("pages") or []
                if isinstance(raw_pages, dict):
                    raw_pages = list(raw_pages.values())
                pages = [page for page in raw_pages if isinstance(page, dict)]
                self._commons_text_cache[cache_key] = pages

            name_tokens = _distinctive_tokens(name)
            city_tokens = {
                token for token in _tokens(lookup_city) if len(token) > 2
            }
            category = _category_term(poi)
            best = None
            best_rank = None
            for index, page in enumerate(pages):
                title = _coerce_text(page.get("title"))
                if not title or title in used_titles or not _is_image_filename(title):
                    continue
                info = page.get("imageinfo") or []
                url = None
                if info and isinstance(info[0], dict):
                    url = normalize_image_url(info[0].get("thumburl") or info[0].get("url"))
                if not url:
                    continue
                title_tokens = _tokens(title)
                title_text = title.lower()
                name_hits = sum(
                    1 for token in name_tokens
                    if _token_matches_text(token, title_tokens)
                )
                city_hit = any(token in title_tokens for token in city_tokens)
                category_hit = category != "landmark" and category in title_text
                if not (name_hits or city_hit or category_hit):
                    continue
                if name_hits and city_hit:
                    rank = 0
                elif name_hits:
                    rank = 1
                elif city_hit and category_hit:
                    rank = 2
                elif city_hit:
                    rank = 3
                else:
                    rank = 4
                score = (rank, -name_hits, index)
                if best_rank is None or score < best_rank:
                    best_rank = score
                    best = (title, url)
            if best:
                used_titles.add(best[0])
                self._set_image(
                    poi, best[1], "commons_search", representative=True
                )

        self._stage(
            "commons_search",
            "ok" if any(_is_display_image(poi.get("image_url")) for poi in pending) else "empty",
            f"{requests_made} queries",
        )

    def _resolve_commons_nearby(self, pois: List[Dict[str, Any]]) -> None:
        """Attach the nearest geotagged Commons photo when no named photo exists.

        This is a *representative* fallback: the file does not need to name the
        attraction, but it must be a real geotagged photo within the cluster's
        radius (MediaWiki caps that at 10 km).  Each file is used at most once.
        """
        pending = [poi for poi in (pois or []) if not _is_display_image(poi.get("image_url"))]
        if not pending:
            return
        if not self.commons_geosearch_enabled:
            self._stage("commons_nearby", "disabled", "ATTRACTION_IMAGE_COMMONS_GEOSEARCH=0")
            return

        clusters = _coordinate_clusters(
            pending, radius_km=5.0, max_clusters=8, include_overflow=True
        )
        used_titles = set()
        for group, center_lat, center_lng, radius_km in clusters:
            cache_key = f"commons_nearby:{center_lat:.3f},{center_lng:.3f},{radius_km:.1f}"
            pages = self._commons_nearby_cache.get(cache_key)
            if pages is None:
                payload = self._get_json(
                    _COMMONS_API,
                    {
                        "action": "query",
                        "format": "json",
                        "formatversion": 2,
                        "generator": "geosearch",
                        "ggscoord": f"{center_lat:.4f}|{center_lng:.4f}",
                        "ggsradius": int(min(10.0, max(1.5, radius_km)) * 1000),
                        "ggslimit": 100,
                        "ggsnamespace": 6,
                        "prop": "coordinates|imageinfo",
                        "iiprop": "url",
                        "iiurlwidth": 900,
                        "colimit": 100,
                    },
                    "commons_nearby",
                    timeout=max(self.timeout, 10.0),
                )
                if payload is None:
                    continue
                raw_pages = ((payload or {}).get("query") or {}).get("pages") or []
                if isinstance(raw_pages, dict):
                    raw_pages = list(raw_pages.values())
                pages = [page for page in raw_pages if isinstance(page, dict)]
                self._commons_nearby_cache[cache_key] = pages

            candidates: List[Tuple[str, str, Optional[float], Optional[float]]] = []
            for page in pages:
                title = _coerce_text(page.get("title"))
                if not _is_image_filename(title):
                    continue
                info = page.get("imageinfo") or []
                url = None
                if info and isinstance(info[0], dict):
                    url = normalize_image_url(info[0].get("thumburl") or info[0].get("url"))
                if not url:
                    continue
                coords = page.get("coordinates") or []
                page_lat = page_lng = None
                if coords and isinstance(coords[0], dict):
                    page_lat = _coerce_coord(coords[0].get("lat"))
                    page_lng = _coerce_coord(coords[0].get("lon"))
                candidates.append((title, url, page_lat, page_lng))

            for poi in group:
                if _is_display_image(poi.get("image_url")):
                    continue
                lat = _coerce_coord(poi.get("latitude", poi.get("lat")))
                lng = _coerce_coord(poi.get("longitude", poi.get("lng")))
                term = _category_term(poi)
                best = None
                best_rank = None
                for title, url, page_lat, page_lng in candidates:
                    if title in used_titles:
                        continue
                    distance = (
                        _haversine_km(lat, lng, page_lat, page_lng)
                        if lat is not None and lng is not None
                        and page_lat is not None and page_lng is not None
                        else 99.0
                    )
                    category_bonus = 0 if term and term in title.lower() else 1
                    rank = (category_bonus, distance, title)
                    if best_rank is None or rank < best_rank:
                        best_rank = rank
                        best = (title, url)
                if best:
                    used_titles.add(best[0])
                    self._set_image(
                        poi, best[1], "commons_nearby", representative=True
                    )

        self._stage(
            "commons_nearby",
            "ok" if any(_is_display_image(poi.get("image_url")) for poi in pending) else "empty",
            f"{sum(1 for poi in pending if _is_display_image(poi.get('image_url')))} nearby",
        )

    def _destination_image(self, city: Optional[str], country: Optional[str]) -> Optional[str]:
        """Return a cached Wikipedia image for the destination itself."""
        targets = [
            _coerce_text(city),
            _coerce_text(country),
        ]
        targets = [target for target in targets if target]
        if not targets:
            return None
        key = "|".join(targets)
        if key in self._destination_image_cache:
            return self._destination_image_cache[key]

        url = None
        for target in targets:
            payload = self._get_json(
                _WIKIPEDIA_API.format(lang=self.language),
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "titles": target,
                    "prop": "pageimages",
                    "pithumbsize": 900,
                    "redirects": 1,
                },
                "destination",
                timeout=self.timeout,
            )
            pages = ((payload or {}).get("query") or {}).get("pages") or []
            if isinstance(pages, dict):
                pages = list(pages.values())
            for page in pages:
                if not isinstance(page, dict):
                    continue
                thumbnail = page.get("thumbnail") or {}
                original = page.get("original") or {}
                url = normalize_image_url(
                    thumbnail.get("source") if isinstance(thumbnail, dict) else None
                ) or normalize_image_url(
                    original.get("source") if isinstance(original, dict) else None
                )
                if url:
                    break
            if url:
                break
        self._destination_image_cache[key] = url
        return url

    def _resolve_representative(self, pois: Iterable[Dict[str, Any]],
                                 city: Optional[str] = None,
                                 allow_network: bool = True) -> None:
        """Guarantee a visual for every POI, in descending order of relevance.

        1. category + destination Pexels photo (real stock photo),
        2. nearest geotagged Wikimedia Commons photo,
        3. the destination's own Wikipedia image,
        4. a generated ExploreX travel card.

        Every result is marked ``image_is_representative`` so the UI can label
        it and no caller mistakes it for a verified photo of the attraction.
        """
        pending = [
            poi for poi in (pois or [])
            if isinstance(poi, dict) and not _is_display_image(poi.get("image_url"))
        ]
        if not pending:
            return
        if not self.guarantee_images:
            self._stage("guarantee", "disabled", "ATTRACTION_IMAGE_GUARANTEE=0")
            return

        # 1. Best local relevance: Commons text search for the name + city.
        #    Bounded so a large state list cannot issue one request per card.
        if allow_network:
            self._resolve_commons_text_search(pending, city, max_requests=8)

        # 2. Representative stock photo: ask for the right kind of place in the
        #    right destination.  Text matches are still preferred by the search.
        can_pexels = bool(
            allow_network
            and self.pexels_enabled
            and self.pexels_key
            and not self._pexels_disabled_reason
            and self._pexels_calls < self.pexels_max_calls
        )
        if can_pexels:
            started_at = time.time()
            for poi in pending:
                if _is_display_image(poi.get("image_url")):
                    continue
                if (
                    self.pexels_deadline > 0
                    and (time.time() - started_at) > self.pexels_deadline
                ):
                    self._stage("pexels", "skipped", "representative_deadline")
                    break
                if time.time() < self._pexels_cooldown_until:
                    break
                if self._pexels_calls >= self.pexels_max_calls:
                    break

                name = _coerce_text(poi.get("name"))
                if not name:
                    continue
                lookup_city = (
                    _coerce_text(poi.get("city") or poi.get("state_city") or poi.get("region"))
                    or _coerce_text(city)
                )
                country = _coerce_text(poi.get("country"))
                location = " ".join(
                    part for part in (lookup_city, country) if part
                )
                query = self._representative_query(poi, lookup_city)
                owner_key = f"representative:{_norm_title(name)}|{_norm_title(lookup_city)}"
                cache_key = f"pexels_rep:{owner_key}"
                hit, cached_url, _source = self._cache_get(cache_key)
                if hit and cached_url:
                    if normalize_image_url(cached_url):
                        self._set_image(
                            poi, cached_url, "pexels_representative",
                            representative=True,
                        )
                    continue
                url, _completed = self._pexels_search(
                    query, name, location, owner_key, strict=False
                )
                if url:
                    self._set_image(
                        poi, url, "pexels_representative", representative=True
                    )
                    self._cache_put(cache_key, url, "pexels_representative")

        # 2. Real nearby photo from Commons, then the destination's own image.
        still_missing = [poi for poi in pending if not _is_display_image(poi.get("image_url"))]
        if still_missing and allow_network:
            self._resolve_commons_nearby(still_missing)

        for poi in pending:
            if _is_display_image(poi.get("image_url")):
                continue
            if not allow_network:
                break
            lookup_city = (
                _coerce_text(poi.get("city") or poi.get("state_city") or poi.get("region"))
                or _coerce_text(city)
            )
            url = self._destination_image(lookup_city, _coerce_text(poi.get("country")))
            if url:
                self._set_image(poi, url, "destination", representative=True)

        # 3. Last resort: a generated card.  Always succeeds and always carries
        #    the attraction's name, so the card is never an empty grey box.
        generated = 0
        for poi in pending:
            if _is_display_image(poi.get("image_url")):
                continue
            poi["image_url"] = _travel_card_svg(poi)
            poi["image_source"] = "generated"
            poi["image_is_representative"] = True
            self._source_counts["generated"] = self._source_counts.get("generated", 0) + 1
            generated += 1

        filled = sum(1 for poi in pending if _is_display_image(poi.get("image_url")))
        self._stage(
            "guarantee", "ok" if filled == len(pending) else "partial",
            f"{filled}/{len(pending)} filled" + (f", {generated} generated" if generated else ""),
        )

    # -- public API --------------------------------------------------------
    def resolve(self, poi: Dict[str, Any], city: Optional[str] = None,
                allow_lookup: bool = True, guarantee: bool = False) -> Optional[str]:
        """Return an image URL for one POI, or ``None``.

        Provider and Wikimedia sources are tried first; a relevance-checked
        Pexels photo is the next fallback.  With ``guarantee=True`` the
        representative chain (category photo, nearby Commons, destination
        image, generated travel card) runs last, so the caller always gets a
        renderable value.  The low-level default stays ``None`` so callers can
        still distinguish "no verified photo" when they need to.
        """
        if not isinstance(poi, dict):
            return None

        for value in _poi_candidate_urls(poi):
            url = normalize_image_url(value)
            if url:
                return url
            if _is_display_image(value):
                return _coerce_text(value)
        url = self._apply_metadata(poi, "provider")
        if url:
            return url

        name = _coerce_text(poi.get("name"))
        if not name:
            return None

        lookup_city = city or _coerce_text(poi.get("city") or poi.get("state_city") or poi.get("region"))
        key = self._cache_key(name, lookup_city)
        hit, url, _source = self._cache_get(key)
        if hit:
            return url

        if not allow_lookup or not self.enabled:
            return None

        # Exact identifiers, Commons and GeoSearch do not consume the per-POI
        # search budget, and they remain usable while the search endpoint is
        # cooling down.
        one = [poi]
        self._hydrate_osm_tags(one)
        if poi.get("image_url"):
            self._cache_put(key, poi["image_url"], "exact")
            return poi["image_url"]
        self._resolve_wikipedia_titles(one)
        if poi.get("image_url"):
            self._cache_put(key, poi["image_url"], "wikipedia_title")
            return poi["image_url"]
        self._resolve_wikidata(one)
        if poi.get("image_url"):
            self._cache_put(key, poi["image_url"], "wikidata")
            return poi["image_url"]
        self._resolve_commons_values(one)
        if poi.get("image_url"):
            self._cache_put(key, poi["image_url"], "commons")
            return poi["image_url"]
        self._resolve_geosearch(one)
        if poi.get("image_url"):
            self._cache_put(key, poi["image_url"], "geosearch")
            return poi["image_url"]
        self._resolve_commons_geosearch(one)
        if poi.get("image_url"):
            self._cache_put(key, poi["image_url"], "commons_geosearch")
            return poi["image_url"]

        # 1) Wikimedia name search: a verified article lead image is preferred
        #    over any stock photo.  A rate-limited or exhausted search does not
        #    block the Pexels fallback below.
        url = None
        if time.time() < self._search_cooldown_until:
            self._stage("search", "rate_limited", "cooldown")
        elif not self._take_lookup():
            self._stage("search", "skipped", "budget")
        else:
            lat = _coerce_coord(poi.get("latitude", poi.get("lat")))
            lng = _coerce_coord(poi.get("longitude", poi.get("lng")))
            if lat is None or lng is None:
                location = poi.get("location")
                if isinstance(location, dict):
                    lat = _coerce_coord(location.get("lat"))
                    lng = _coerce_coord(location.get("lng"))

            url, completed = self._search_wikipedia(name, lookup_city, lat, lng)
            self._cache_put(key, url, "wikipedia" if url else "none", cache_negative=completed)
            used = max(0, self._active_budget - self._remaining_lookups)
            if self._stage_status.get("search", {}).get("state") == "rate_limited":
                self.last_status.update({
                    "state": "rate_limited",
                    "detail": "HTTP 429",
                    "resolved": self.last_status.get("resolved", 0),
                    "lookups": used,
                })
            elif url:
                self.last_status.update({
                    "state": "ok",
                    "detail": "resolved",
                    "resolved": self.last_status.get("resolved", 0) + 1,
                    "lookups": used,
                })
            elif completed:
                self.last_status.update({
                    "state": "empty",
                    "detail": "no verified matching page",
                    "lookups": used,
                })
            if url:
                return url

        # 2) Last resort: a stock photo whose own text names this attraction.
        #    Runs even when the Wikimedia search endpoint is rate-limited.
        self._resolve_pexels(one, lookup_city)
        if poi.get("image_url"):
            self._cache_put(key, poi["image_url"], "pexels")
            self.last_status.update({
                "state": "ok",
                "detail": "pexels",
                "resolved": self.last_status.get("resolved", 0) + 1,
                "sources": dict(self._source_counts),
                "stages": dict(self._stage_status),
            })
            return poi["image_url"]

        # 3) Opt-in guarantee used by the UI endpoints: never return an empty
        #    image for an attraction that will be displayed.
        if guarantee and self.guarantee_images:
            self._resolve_representative(one, lookup_city)
            if _is_display_image(poi.get("image_url")):
                self._cache_put(
                    key, poi["image_url"], poi.get("image_source") or "representative"
                )
                self.last_status.update({
                    "state": "ok",
                    "detail": poi.get("image_source") or "representative",
                    "resolved": self.last_status.get("resolved", 0) + 1,
                    "sources": dict(self._source_counts),
                    "stages": dict(self._stage_status),
                })
                return poi["image_url"]
        return None

    def enrich(self, pois: Iterable[Dict[str, Any]], city: Optional[str] = None,
               max_lookups: Optional[int] = None) -> list:
        """Set ``image_url`` on POIs that lack one, within a lookup budget.

        Existing valid URLs are preserved.  Verified photos are preferred, but
        the final guarantee stage fills every remaining item with an explicitly
        representative photo or a generated travel card, so a displayed
        attraction never has an empty image area.  Set
        ``ATTRACTION_IMAGE_GUARANTEE=0`` to restore the old ``None`` behaviour.
        """
        items = [poi for poi in (pois or []) if isinstance(poi, dict)]
        if max_lookups is None:
            max_lookups = max(24, min(120, len(items)))

        if not self.enabled:
            # ``ATTRACTION_IMAGE_LOOKUP=0`` is an offline switch: keep any image
            # the provider or OSM tags already contain, but never touch the
            # network.
            resolved = 0
            for poi in items:
                for value in _poi_candidate_urls(poi):
                    url = normalize_image_url(value)
                    if url:
                        poi["image_url"] = url
                        resolved += 1
                        break
                if poi.get("image_url"):
                    continue
                metadata_url, _commons = self._metadata_image(poi)
                if metadata_url:
                    poi["image_url"] = metadata_url
                    resolved += 1
            self.last_status = {
                "state": "disabled",
                "resolved": resolved,
                "missing": len(items) - resolved,
                "total": len(items),
                "lookups": 0,
                "budget": 0,
                "sources": {},
                "stages": {},
            }
            return items

        self.reset_budget(max_lookups)

        # 1. Anything the provider already gave us.
        pending: List[Dict[str, Any]] = []
        for poi in items:
            existing = None
            for value in _poi_candidate_urls(poi):
                normalized = normalize_image_url(value)
                if normalized:
                    existing = normalized
                    break
                if _is_display_image(value):
                    existing = _coerce_text(value)
                    break
            if existing:
                poi["image_url"] = existing
                continue
            self._apply_metadata(poi, "provider")
            if poi.get("image_url"):
                continue
            pending.append(poi)

        # 2. Reuse successful mappings from earlier requests before touching the
        #    network again.  Only positive cache entries are hydrated; a cached
        #    negative answer must never suppress a provider that may now work.
        hydrated: List[Dict[str, Any]] = []
        for poi in pending:
            name = _coerce_text(poi.get("name"))
            lookup_city = (
                _coerce_text(poi.get("city") or poi.get("state_city"))
                or _coerce_text(city)
            )
            hit, cached_url, source = self._cache_get(self._cache_key(name, lookup_city))
            if hit and cached_url and name:
                self._set_image(poi, cached_url, source or "cache")
            else:
                hydrated.append(poi)
        pending = hydrated

        # 3. Batch identifier lookups.  Each stage is independent: a failure or
        #    rate limit in one never discards the results of the others.
        self._hydrate_osm_tags(pending)
        pending = [poi for poi in pending if not poi.get("image_url")]

        self._resolve_wikipedia_titles(pending)
        pending = [poi for poi in pending if not poi.get("image_url")]

        self._resolve_wikidata(pending)
        pending = [poi for poi in pending if not poi.get("image_url")]

        self._resolve_commons_values(pending)
        pending = [poi for poi in pending if not poi.get("image_url")]

        self._resolve_geosearch(pending)
        pending = [poi for poi in pending if not poi.get("image_url")]

        # 4. Per-place search, bounded.  Stop only this stage on a 429; records
        #    skipped for rate limiting are not cached as permanent negatives.
        #    If the first search is rate-limited, wait one short cooldown and
        #    retry: losing every remaining place after a single 429 is what made
        #    coverage collapse in the live run.
        pending.sort(key=self._search_sort_key)
        started_at = time.time()

        def _past_deadline() -> bool:
            return (
                self.deadline_seconds > 0
                and (time.time() - started_at) > self.deadline_seconds
            )

        cooldown_waits = 0
        searched_names = set()
        for poi in pending:
            if _past_deadline():
                self._stage("search", "skipped", "deadline")
                break
            if time.time() < self._search_cooldown_until:
                remaining = self._search_cooldown_until - time.time()
                if cooldown_waits >= 1 or remaining > 12.0:
                    self._stage("search", "rate_limited", "cooldown")
                    break
                time.sleep(max(0.0, remaining))
                cooldown_waits += 1
                self._search_cooldown_until = 0.0
            name = _coerce_text(poi.get("name"))
            if not name or not _distinctive_tokens(name):
                continue
            lookup_city = _coerce_text(poi.get("city") or poi.get("state_city")) or city
            normalized = (_norm_title(name), _norm_title(lookup_city))
            if normalized in searched_names:
                continue
            searched_names.add(normalized)
            if not self._take_lookup():
                break
            lat = _coerce_coord(poi.get("latitude", poi.get("lat")))
            lng = _coerce_coord(poi.get("longitude", poi.get("lng")))
            url, completed = self._search_wikipedia(name, lookup_city, lat, lng)
            key = self._cache_key(name, lookup_city)
            if url:
                self._set_image(poi, url, "search")
                self._cache_put(key, url, "wikipedia")
            elif completed:
                self._cache_put(key, None, "none")

        # 5. Commons GeoSearch: the most request-hungry Wikimedia stage, so it
        #    must not consume the quota the higher-yield search stage needs.
        still_missing = [poi for poi in items if not normalize_image_url(poi.get("image_url"))]
        if _past_deadline():
            self._stage("commons_geosearch", "skipped", "deadline")
        else:
            self._resolve_commons_geosearch(still_missing)

        # 6. Pexels stock-photo fallback for places that no Wikimedia source
        #    could verify.  It has its own budget, deadline and relevance gate.
        still_missing = [poi for poi in items if not _is_display_image(poi.get("image_url"))]
        if _past_deadline():
            self._stage("pexels", "skipped", "deadline")
        else:
            self._resolve_pexels(still_missing, city)

        # 7. Guarantee: every displayed attraction gets an image.  Prefer a
        #    representative category photo / nearby Commons / destination image;
        #    fall back to a generated card.  Near the wall-clock deadline we skip
        #    further network calls and generate cards immediately.
        still_missing = [poi for poi in items if not _is_display_image(poi.get("image_url"))]
        if still_missing:
            self._resolve_representative(
                still_missing, city, allow_network=not _past_deadline()
            )

        resolved = sum(1 for poi in items if _is_display_image(poi.get("image_url")))
        missing = len(items) - resolved
        if not self.enabled:
            state = "disabled"
        elif self._stage_status.get("search", {}).get("state") == "rate_limited":
            state = "rate_limited"
        elif resolved == 0:
            state = "empty"
        elif missing:
            state = "partial"
        else:
            state = "ok"

        self.last_status = {
            "state": state,
            "resolved": resolved,
            "lookups": max(0, self._active_budget - self._remaining_lookups),
            "budget": self._active_budget,
            "total": len(items),
            "missing": missing,
            "sources": dict(self._source_counts),
            "stages": dict(self._stage_status),
        }
        return items


_RESOLVER: Optional[AttractionImageResolver] = None
_RESOLVER_LOCK = threading.Lock()


def get_image_resolver() -> AttractionImageResolver:
    """Process-wide resolver so its cache survives across requests."""
    global _RESOLVER
    if _RESOLVER is None:
        with _RESOLVER_LOCK:
            if _RESOLVER is None:
                _RESOLVER = AttractionImageResolver()
    return _RESOLVER


def resolve_attraction_image(poi: Dict[str, Any], city: Optional[str] = None) -> Optional[str]:
    """Convenience wrapper used by the API endpoint and enrichment."""
    return get_image_resolver().resolve(poi, city=city)


def enrich_poi_images(pois: Iterable[Dict[str, Any]], city: Optional[str] = None,
                      max_lookups: Optional[int] = None) -> list:
    return get_image_resolver().enrich(pois, city=city, max_lookups=max_lookups)
