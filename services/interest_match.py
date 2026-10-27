"""Deterministic interest matching for ExploreX attractions.

Why this module exists
----------------------
The October Japan run reported ``interest_based = 0`` for the interests
"viewpoints and photography" even though the destination had plenty of
observation decks and scenic spots.  The old implementation compared the raw
hobby words against an English-only keyword list and the enrichment layer had
already deleted the structured tags it needed.

This module is the single, testable implementation used by both
``InformationAgent`` and ``RecommendationAgent``.  It:

* normalises free-text interests (``"Viewpoints and photography"``) into
  canonical groups,
* merges the user's requested groups with their synonyms,
* inspects the attraction's *structured* evidence (category, OSM tags, types,
  description, Japanese names) rather than only the display name,
* returns a traceable list of reasons for every match.

No network, no LLM, no randomness: the same inputs always produce the same
matches.  Popularity is deliberately not part of this module.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple


# ---------------------------------------------------------------------------
# Canonical interest groups
# ---------------------------------------------------------------------------
# Each group has user-facing aliases (what a traveller may type) and evidence
# terms (what an attraction record may contain).  Evidence is matched against a
# lower-cased text pool, so a term such as "viewpoint" also matches the OSM tag
# ``tourism=viewpoint`` and a description that says "great views".
INTEREST_GROUPS: Dict[str, Dict[str, Sequence[str]]] = {
    "viewpoints": {
        "aliases": (
            "viewpoint", "viewpoints", "view point", "view points", "scenic viewpoint",
            "observation deck", "lookout", "vista", "panorama", "scenic", "views",
        ),
        "evidence": (
            "viewpoint", "view point", "observation", "observatory", "lookout",
            "panorama", "panoramic", "vista", "scenic", "overlook", "skyline",
            "observation deck", "展望台", "展望", "眺望", "絶景", "夜景", "night view",
        ),
    },
    "photography": {
        "aliases": (
            "photography", "photo", "photos", "photographer", "landscape photography",
            "photo walk", "instagram", "instagrammable",
        ),
        "evidence": (
            "photography", "photographer", "photo", "photogenic", "picturesque",
            "instagrammable", "scenic", "viewpoint", "observation", "panorama",
            "sunset", "sunrise", "illumination", "night view", "skyline", "landscape",
            "写真", "撮影", "絶景", "風景", "夕日", "日の出", "ライトアップ",
        ),
    },
    "nature": {
        "aliases": (
            "nature", "outdoors", "outdoor", "parks", "park", "garden", "gardens",
            "wildlife", "forest", "lake", "waterfall", "beach", "beaches", "island",
            "seaside", "coast", "backwaters",
        ),
        "evidence": (
            "nature", "park", "garden", "forest", "woodland", "wildlife", "sanctuary",
            "national park", "lake", "river", "waterfall", "beach", "beaches", "coast",
            "coastline", "seaside", "shore", "island", "backwater", "backwaters",
            "mountain", "valley", "meadow", "woods", "自然", "公園", "庭園", "森",
            "湖", "滝", "川", "海", "島", "山",
        ),
    },
    "mountains": {
        "aliases": (
            "mountain", "mountains", "mountain range", "peak", "peaks", "summit",
            "alpine", "highland", "highlands", "hill", "hills", "hill station",
            "hill stations", "hiking trail",
        ),
        "evidence": (
            "mountain", "mountains", "mount", "peak", "summit", "alpine", "highland",
            "highlands", "ridge", "volcano", "crater", "hill", "hills", "hill station",
            "tea garden", "tea plantation", "plantation", "ghat", "山", "岳", "峰",
            "連峰", "高原",
        ),
    },
    "temples": {
        "aliases": (
            "temple", "temples", "shrine", "shrines", "spiritual", "spirituality",
            "religious", "pilgrimage", "buddhist", "buddhism", "shinto",
        ),
        "evidence": (
            "temple", "shrine", "pagoda", "buddhist", "buddhism", "shinto", "monastery",
            "place of worship", "religious", "spiritual", "pilgrimage", "寺", "神社",
            "寺院", "お寺", "塔", "仏閣", "参拝",
        ),
    },
    "history": {
        "aliases": (
            "history", "historical", "historic", "heritage", "castle", "ruins",
            "monument", "old town", "samurai",
        ),
        "evidence": (
            "historic", "historical", "heritage", "castle", "ruin", "ruins", "monument",
            "archaeological", "ancient", "old town", "samurai", "edo", "memorial",
            "fort", "palace", "城", "史跡", "遺跡", "旧", "歴史", "古民家",
        ),
    },
    "museums": {
        "aliases": (
            "museum", "museums", "art gallery", "galleries", "art", "exhibition",
            "science museum",
        ),
        "evidence": (
            "museum", "gallery", "art gallery", "exhibition", "collection", "science",
            "planetarium", "博物館", "美術館", "資料館", "展覧会", "記念館",
        ),
    },
    "food": {
        "aliases": (
            "food", "foodie", "culinary", "cuisine", "restaurant", "restaurants",
            "street food", "market food", "dining",
        ),
        "evidence": (
            "food", "restaurant", "cafe", "coffee", "cuisine", "culinary", "market",
            "street food", "dining", "ramen", "sushi", "izakaya", "bakery", "食",
            "料理", "市場", "寿司", "ラーメン", "居酒屋", "食堂", "グルメ",
        ),
    },
    "adventure": {
        "aliases": (
            "adventure", "adventurous", "hiking", "trek", "trekking", "climbing",
            "camping", "safari", "water sports", "rafting", "paragliding",
        ),
        "evidence": (
            "adventure", "hiking", "hike", "trek", "trekking", "climbing", "camping",
            "safari", "rafting", "kayak", "paragliding", "diving", "snorkel",
            "登山", "ハイキング", "トレッキング", "キャンプ", "ラフティング",
        ),
    },
    "shopping": {
        "aliases": (
            "shopping", "shop", "shops", "mall", "markets", "boutique", "souvenir",
        ),
        "evidence": (
            "shopping", "mall", "market", "bazaar", "boutique", "souvenir", "craft",
            "department store", "商店街", "市場", "買い物", "ショッピング", "百貨店",
        ),
    },
    "culture": {
        "aliases": (
            "culture", "cultural", "festival", "festivals", "theatre", "theater",
            "traditional", "performing arts", "local life",
        ),
        "evidence": (
            "culture", "cultural", "festival", "theatre", "theater", "traditional",
            "performing arts", "art", "craft", "tea ceremony", "文化", "祭", "祭り",
            "伝統", "芸能", "茶道",
        ),
    },
    "architecture": {
        "aliases": (
            "architecture", "architectural", "building", "buildings", "bridge",
            "tower", "skyscraper",
        ),
        "evidence": (
            "architecture", "architectural", "building", "bridge", "tower", "temple",
            "shrine", "castle", "modernist", "skyscraper", "建築", "橋", "タワー",
            "建造物",
        ),
    },
    "nightlife": {
        "aliases": ("nightlife", "night life", "bar", "bars", "club", "clubs", "izakaya"),
        "evidence": (
            "nightlife", "bar", "pub", "club", "lounge", "izakaya", "night market",
            "evening", "居酒屋", "バー", "ナイトライフ", "夜",
        ),
    },
    "family": {
        "aliases": (
            "family", "kids", "children", "zoo", "aquarium", "theme park",
            "amusement park", "water park",
        ),
        "evidence": (
            "family", "zoo", "aquarium", "theme park", "amusement park", "water park",
            "planetarium", "children", "kids", "動物園", "水族館", "遊園地",
        ),
    },
    "offbeat": {
        "aliases": ("offbeat", "hidden gem", "hidden gems", "local secret", "quiet", "secluded"),
        "evidence": (
            "offbeat", "hidden gem", "unexplored", "local secret", "quiet", "secluded",
            "less crowded", "穴場", "隠れ",
        ),
    },
}

# OSM tag values that are strong evidence even when the name is non-English.
_OSM_TAG_EVIDENCE: Dict[Tuple[str, str], Set[str]] = {
    ("tourism", "viewpoint"): {"viewpoints", "photography"},
    ("tourism", "museum"): {"museums", "culture"},
    ("tourism", "gallery"): {"museums", "culture"},
    ("tourism", "attraction"): {"culture"},
    ("tourism", "artwork"): {"culture", "architecture"},
    ("tourism", "zoo"): {"family"},
    ("tourism", "aquarium"): {"family"},
    ("tourism", "theme_park"): {"family", "adventure"},
    ("natural", "peak"): {"viewpoints", "mountains", "nature", "photography"},
    ("natural", "volcano"): {"viewpoints", "mountains", "nature"},
    ("natural", "waterfall"): {"nature", "photography"},
    ("natural", "lake"): {"nature", "photography"},
    ("natural", "wood"): {"nature"},
    ("natural", "beach"): {"nature", "photography"},
    ("natural", "hill"): {"mountains", "nature", "viewpoints"},
    ("leisure", "beach_resort"): {"nature", "photography"},
    ("natural", "water"): {"nature", "photography"},
    ("historic", "castle"): {"history", "architecture"},
    ("historic", "temple"): {"temples", "history"},
    ("historic", "shrine"): {"temples", "history"},
    ("historic", "monument"): {"history"},
    ("historic", "ruins"): {"history"},
    ("amenity", "place_of_worship"): {"temples"},
    ("leisure", "park"): {"nature", "family"},
    ("leisure", "garden"): {"nature", "photography"},
    ("shop", "mall"): {"shopping"},
    ("shop", "department_store"): {"shopping"},
}

_CATEGORY_GROUP_HINTS: Dict[str, Set[str]] = {
    "viewpoint": {"viewpoints", "photography"},
    "viewpoints": {"viewpoints", "photography"},
    "observation": {"viewpoints", "photography"},
    "nature": {"nature"},
    "park": {"nature", "family"},
    "garden": {"nature", "photography"},
    "mountain": {"mountains", "nature", "viewpoints"},
    "hill": {"mountains", "nature", "viewpoints"},
    "beach": {"nature", "photography"},
    "waterfall": {"nature", "photography"},
    "backwater": {"nature"},
    "lake": {"nature", "photography"},
    "view point": {"viewpoints", "photography"},
    "temple": {"temples", "history"},
    "shrine": {"temples", "history"},
    "spiritual": {"temples"},
    "heritage": {"history"},
    "historic": {"history"},
    "history": {"history"},
    "castle": {"history", "architecture"},
    "museum": {"museums", "culture"},
    "gallery": {"museums", "culture"},
    "art": {"museums", "culture"},
    "food": {"food"},
    "restaurant": {"food"},
    "market": {"food", "shopping", "culture"},
    "adventure": {"adventure"},
    "shopping": {"shopping"},
    "culture": {"culture"},
    "architecture": {"architecture"},
    "nightlife": {"nightlife"},
    "family": {"family"},
    "offbeat": {"offbeat"},
}

# Words that add no signal and can make short aliases match by accident.
_STOPWORDS = {
    "and", "or", "with", "plus", "the", "a", "an", "in", "of", "to", "for",
    "my", "our", "trip", "travel", "travelling", "traveling", "interested",
    "interest", "interests", "like", "love", "enjoy", "including", "include",
}

_TOKEN_RE = re.compile(r"[a-z0-9\u3040-\u30ff\u3400-\u9fff]+", re.IGNORECASE)
_WEIGHT_RE = re.compile(r"([a-zA-Z\u3040-\u30ff\u3400-\u9fff ]+?)\s*[:\-]\s*(\d{1,3})")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_clean_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(f"{k} {_clean_text(v)}" for k, v in value.items())
    return str(value)


def _terms(value: Any) -> List[str]:
    text = _clean_text(value).lower()
    return [t for t in _TOKEN_RE.findall(text) if t and t not in _STOPWORDS]


def interests_from_preferences(user_prefs: Any) -> Dict[str, int]:
    """Return ``{canonical_group: weight}`` parsed from user preferences.

    ``user_prefs["hobbies"]`` may be a comma-separated string, a list, or a
    weighted string such as ``"photography:90, temples:60"``.  Unknown interests
    are ignored for scoring, but their raw text remains available to the caller
    if it wants to display it.
    """
    if not isinstance(user_prefs, dict):
        return {}
    raw = user_prefs.get("interests", user_prefs.get("hobbies", ""))
    text = _clean_text(raw)
    if not text:
        return {}

    weights: Dict[str, int] = {}
    for match in _WEIGHT_RE.finditer(text):
        label = match.group(1).strip().lower()
        try:
            weight = max(0, min(100, int(match.group(2))))
        except ValueError:
            weight = 50
        for group in groups_for_interest(label):
            weights[group] = max(weights.get(group, 50), weight)

    # Remove the "category:90" fragments before free-text matching.
    plain = _WEIGHT_RE.sub(" ", text)
    tokens = _terms(plain)
    joined = " ".join(tokens)

    for group, spec in INTEREST_GROUPS.items():
        aliases = spec["aliases"]
        if any(_term_present(alias, joined) for alias in aliases):
            weights.setdefault(group, 50)

    return weights


def groups_for_interest(label: str) -> Set[str]:
    """Map a free-text interest label to one or more canonical groups."""
    text = str(label or "").strip().lower()
    if not text:
        return set()
    groups: Set[str] = set()
    for group, spec in INTEREST_GROUPS.items():
        if text == group or any(text == a or _term_present(a, text) for a in spec["aliases"]):
            groups.add(group)
    return groups


def _term_present(term: str, haystack: str) -> bool:
    term = str(term).strip().lower()
    if not term:
        return False
    # Substring match for CJK (no spaces) keeps working without word boundaries.
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", term):
        return term in haystack

    # Accept the common plural forms so "beaches" matches the "beach" group and
    # "galleries" matches "gallery" without maintaining every spelling by hand.
    variants = {term}
    if term.endswith("y") and len(term) > 2:
        variants.add(term[:-1] + "ies")
    elif term.endswith(("ch", "sh", "x", "z", "ss")):
        variants.add(term + "es")
    elif term.endswith("s") and len(term) > 3:
        variants.add(term[:-1])
    else:
        variants.add(term + "s")

    for variant in variants:
        pattern = r"(?<![a-z0-9])" + re.escape(variant) + r"(?![a-z0-9])"
        if re.search(pattern, haystack):
            return True
    return False


def attraction_text_pool(attr: Dict[str, Any]) -> str:
    """Build the evidence pool from structured fields, not just the name."""
    if not isinstance(attr, dict):
        return ""
    fields = [
        attr.get("name"), attr.get("category"), attr.get("type"), attr.get("description"),
        attr.get("source"), attr.get("tags"), attr.get("osm_tags"), attr.get("types"),
        attr.get("metadata"), attr.get("wikidata_id"), attr.get("wikipedia"),
    ]
    return _clean_text(fields).lower()


def osm_groups(attr: Dict[str, Any]) -> Set[str]:
    """Return groups implied by structured OSM tags/types."""
    groups: Set[str] = set()
    if not isinstance(attr, dict):
        return groups

    tags: Dict[str, Any] = {}
    for key in ("osm_tags", "metadata", "tags"):
        raw = attr.get(key)
        if isinstance(raw, dict):
            tags.update({str(k).lower(): str(v).lower() for k, v in raw.items()})

    for (key, value), implied in _OSM_TAG_EVIDENCE.items():
        if tags.get(key) == value:
            groups.update(implied)

    types = attr.get("types")
    type_values = []
    if isinstance(types, (list, tuple, set)):
        type_values = [str(t).lower() for t in types]
    elif types:
        type_values = [str(types).lower()]
    for value in type_values:
        if "=" in value:
            k, v = value.split("=", 1)
            for (key, expected), implied in _OSM_TAG_EVIDENCE.items():
                if k.strip() == key and v.strip() == expected:
                    groups.update(implied)
        for hint, implied in _CATEGORY_GROUP_HINTS.items():
            if _term_present(hint, value):
                groups.update(implied)

    for field in ("category", "type"):
        value = str(attr.get(field) or "").lower()
        for hint, implied in _CATEGORY_GROUP_HINTS.items():
            if _term_present(hint, value):
                groups.update(implied)

    return groups


def match_interests(attr: Dict[str, Any], user_prefs: Any) -> Tuple[List[str], List[Dict[str, str]]]:
    """Return ``(matched_groups, reasons)`` for one attraction.

    ``reasons`` is a list of ``{"interest": group, "evidence": term}`` entries,
    which lets the API/UI show *why* something was recommended instead of an
    unexplained percentage.
    """
    requested = interests_from_preferences(user_prefs)
    if not requested:
        return [], []

    pool = attraction_text_pool(attr)
    structured = osm_groups(attr)
    matched: List[str] = []
    reasons: List[Dict[str, str]] = []

    for group in requested:
        spec = INTEREST_GROUPS.get(group)
        if not spec:
            continue
        evidence_term = None
        if group in structured:
            evidence_term = "structured category/tag"
        else:
            for term in spec["evidence"]:
                if _term_present(term, pool):
                    evidence_term = term
                    break
        if evidence_term:
            matched.append(group)
            reasons.append({"interest": group, "evidence": evidence_term})

    return matched, reasons


def interest_score(attr: Dict[str, Any], user_prefs: Any) -> Tuple[float, List[str], List[Dict[str, str]]]:
    """Deterministic 0–40 interest component used by the ranking pipeline.

    * No stated interests  -> a neutral 20 (discovery mode, not a match).
    * One of two interests -> up to 30, weighted by the user's weights.
    * Both interests       -> up to 40.
    * No evidence          -> 0, regardless of popularity.
    """
    requested = interests_from_preferences(user_prefs)
    if not requested:
        return 20.0, [], []

    matched, reasons = match_interests(attr, user_prefs)
    if not matched:
        return 0.0, [], []

    total_weight = sum(max(1, w) for w in requested.values())
    matched_weight = sum(max(1, requested[g]) for g in matched if g in requested)
    coverage = matched_weight / float(total_weight or 1)
    # 40 points max; a single high-weight match still earns a strong score.
    score = 15.0 + 25.0 * coverage
    return round(min(40.0, score), 2), matched, reasons


def normalize_interests(raw: Any) -> List[str]:
    """Public helper used by tests/UI: canonical groups for a hobby string."""
    return sorted(interests_from_preferences({"hobbies": raw}).keys())
