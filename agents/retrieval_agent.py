"""
Hybrid Retrieval Agent — ExploreX
==================================
Routes retrieval between two sources based on destination geography:

  Indian destination  →  ChromaDB RAG vector search (rich India knowledge base)
  International dest  →  DuckDuckGo live web search (structured queries)

The context string returned by retrieve_context() is in the SAME format
regardless of source, so all downstream agents work without modification.
"""

import os
import time
import hashlib
from langchain.vectorstores import Chroma
from langchain.embeddings import HuggingFaceEmbeddings

# ---------------------------------------------------------------------------
# Known Indian cities / states for instant O(1) lookup — derived from
# INDIA_STATES_MAP in travel_graph.py plus 200 major Indian cities.
# This avoids any external API call inside the retrieval agent itself.
# ---------------------------------------------------------------------------
INDIA_KNOWN_NAMES = {
    # States and common aliases (from INDIA_STATES_MAP keys)
    "rajasthan", "kerala", "goa", "himachal pradesh", "himachal",
    "uttarakhand", "uttaranchal", "tamil nadu", "tamilnadu", "karnataka",
    "maharashtra", "uttar pradesh", "up", "west bengal", "bengal",
    "gujarat", "punjab", "madhya pradesh", "mp", "andhra pradesh", "ap",
    "telangana", "jammu and kashmir", "jammu kashmir", "kashmir", "j&k",
    "ladakh", "sikkim", "meghalaya", "assam", "odisha", "orissa",
    "bihar", "jharkhand", "chhattisgarh", "manipur", "nagaland",
    "arunachal pradesh", "mizoram", "tripura", "haryana",
    "delhi", "andaman and nicobar", "andaman", "nicobar",
    "lakshadweep", "pondicherry", "puducherry", "chandigarh",
    "daman and diu", "dadra and nagar haveli", "jammu",

    # Major cities (50+)
    "jaipur", "udaipur", "jodhpur", "jaisalmer", "pushkar",
    "kochi", "munnar", "alleppey", "alappuzha", "thiruvananthapuram",
    "wayanad", "kovalam", "varkala", "thrissur",
    "panaji", "calangute", "vasco da gama", "margao", "anjuna", "palolem",
    "shimla", "manali", "dharamshala", "mcleod ganj", "dalhousie", "kasauli",
    "rishikesh", "haridwar", "mussoorie", "nainital", "auli",
    "chennai", "madurai", "ooty", "mahabalipuram", "thanjavur", "coimbatore",
    "bangalore", "bengaluru", "mysore", "mysuru", "hampi", "coorg", "badami",
    "mumbai", "pune", "aurangabad", "nashik", "mahabaleshwar", "lonavala",
    "agra", "varanasi", "lucknow", "mathura", "prayagraj", "vrindavan",
    "kolkata", "darjeeling", "siliguri", "shantiniketan",
    "ahmedabad", "vadodara", "surat", "somnath", "dwarka", "gir",
    "amritsar", "chandigarh", "ludhiana",
    "bhopal", "indore", "gwalior", "khajuraho", "ujjain",
    "visakhapatnam", "vizag", "tirupati", "vijayawada",
    "hyderabad", "warangal",
    "srinagar", "gulmarg", "pahalgam", "sonamarg",
    "leh", "nubra valley", "pangong lake", "kargil",
    "gangtok", "pelling", "yuksom",
    "shillong", "cherrapunji", "mawlynnong", "dawki",
    "guwahati", "kaziranga", "majuli",
    "bhubaneswar", "puri", "konark",
    "patna", "bodh gaya", "nalanda",
    "ranchi", "jamshedpur",
    "port blair", "havelock island", "neil island",
    "agartala", "kohima", "imphal", "aizawl", "itanagar", "tawang",
    "ranthambore", "sawai madhopur", "jim corbett", "corbett",
    "new delhi", "old delhi", "gurugram", "gurgaon", "noida", "faridabad",
    "surat", "rajkot", "jamnagar", "bhavnagar",
    "kavaratti", "agatti", "minicoy",
    "daman", "diu", "silvassa",
    "pondicherry", "auroville",
    "nathu la", "ziro", "pasighat",
    "varkala", "alappuzha", "kollam",
    "tiruchirapalli", "trichy", "madurai", "kanchipuram", "thanjavur",
    "raipur", "jagdalpur",
    "deoghar", "ranchi", "bokaro",
}

# Bounding box for India (approximate)
INDIA_LAT_MIN, INDIA_LAT_MAX = 6.4, 36.0
INDIA_LNG_MIN, INDIA_LNG_MAX = 68.0, 97.5


class RetrievalAgent:
    """
    Hybrid Retrieval Agent that automatically routes context retrieval to:
      - ChromaDB RAG (for Indian destinations)
      - DuckDuckGo web search (for international destinations)

    The return format is identical in both cases — a plain-text context string
    that downstream agents consume unchanged.
    """

    def __init__(self, persist_directory="data/vector_db"):
        self.persist_directory = persist_directory
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

        # Load ChromaDB (India knowledge base)
        self.vector_db = Chroma(
            collection_name="travel_knowledge",
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

        # In-memory cache for international destination web results
        # Structure: { cache_key: {"result": str, "timestamp": float} }
        self._web_cache: dict = {}
        self._cache_ttl_seconds = 3600  # 1 hour TTL

    # ─────────────────────────────────────────────────────────────────────
    # Public Interface
    # ─────────────────────────────────────────────────────────────────────

    def retrieve_context(self, user_prefs: dict, city: str, k: int = 5) -> str:
        """
        Main entry point. Detects whether `city` is in India and routes accordingly.

        Args:
            user_prefs: User preference dict (hobbies, health, budget, etc.)
            city:       Destination name (city, state, or country)
            k:          Number of RAG documents to retrieve (India only)

        Returns:
            Context string in identical format regardless of source.
        """
        if not city:
            return "No destination provided."

        city_clean = city.strip()

        # Step 1: Determine routing
        if self._is_indian_destination(city_clean, user_prefs):
            print(f"[RETRIEVAL AGENT] 🇮🇳 Indian destination detected: '{city_clean}' → Using RAG retrieval")
            return self._retrieve_from_rag(city_clean, user_prefs, k)
        else:
            print(f"[RETRIEVAL AGENT] 🌍 International destination detected: '{city_clean}' → Using web retrieval")
            return self._retrieve_from_web(city_clean, user_prefs)

    # ─────────────────────────────────────────────────────────────────────
    # Destination Detection
    # ─────────────────────────────────────────────────────────────────────

    def _is_indian_destination(self, city: str, user_prefs: dict) -> bool:
        """
        Determine whether a destination is within India.

        Uses a two-stage strategy:
        1. Fast O(1) lookup against INDIA_KNOWN_NAMES set
        2. Lat/lng bounding box check if coordinates are already in user_prefs state

        No external API calls are made here — detection uses pre-computed data only.
        """
        city_lower = city.lower().strip()

        # Stage 1a: Exact match in known names set
        if city_lower in INDIA_KNOWN_NAMES:
            print(f"[RETRIEVAL AGENT] India detection: '{city}' found in known India names set")
            return True

        # Stage 1b: Whole-word containment check (city_lower contains an Indian name as a word)
        # Only check multi-character names (length >= 4) to avoid short abbreviations
        # like 'ap', 'up', 'mp' matching international cities ('singapore', 'new york', etc.)
        for known in INDIA_KNOWN_NAMES:
            if len(known) >= 4 and known in city_lower:
                # Ensure it's a word boundary match, not a partial substring
                # e.g., "goa, india" should match "goa" but "singapore" should NOT match "goa"
                import re as _re
                if _re.search(r'\b' + _re.escape(known) + r'\b', city_lower):
                    print(f"[RETRIEVAL AGENT] India detection: '{city}' contains Indian name '{known}'")
                    return True

        # Stage 2: Lat/lng bounding box — uses coordinates already geocoded upstream
        # The state carries lat/lng from city2geocode() in InformationAgent
        lat = user_prefs.get("_dest_lat")
        lng = user_prefs.get("_dest_lng")
        if lat is not None and lng is not None:
            try:
                in_india = (INDIA_LAT_MIN <= float(lat) <= INDIA_LAT_MAX and
                            INDIA_LNG_MIN <= float(lng) <= INDIA_LNG_MAX)
                print(f"[RETRIEVAL AGENT] India detection by coords ({lat},{lng}): {in_india}")
                return in_india
            except (ValueError, TypeError):
                pass

        # Stage 3: Check city_validated flag set by travel_graph
        if user_prefs.get("city_validated") and user_prefs.get("is_international") is not True:
            return True

        # Stage 4: If flagged explicitly as international
        if user_prefs.get("is_international") is True:
            return False

        # Default: treat as India if not explicitly flagged (conservative fallback)
        # This prevents false web calls for any city not in our list but might still be Indian
        print(f"[RETRIEVAL AGENT] India detection: '{city}' unknown — defaulting to RAG (conservative)")
        return True

    # ─────────────────────────────────────────────────────────────────────
    # RAG Retrieval (Indian Destinations)
    # ─────────────────────────────────────────────────────────────────────

    def _retrieve_from_rag(self, city: str, user_prefs: dict, k: int = 5) -> str:
        """
        Retrieves contextual knowledge from the ChromaDB India knowledge base.
        Builds a rich semantic query from city name and user preferences.
        """
        hobbies = user_prefs.get("hobbies", "")
        health = user_prefs.get("health", "")
        budget = user_prefs.get("budget", "")
        kids = user_prefs.get("kids", "")

        # Build a rich semantic query
        query_parts = [f"travel guide attractions things to do in {city}"]
        if hobbies:
            query_parts.append(f"places for {hobbies} in {city}")
        if health == "limited":
            query_parts.append("wheelchair accessibility easy terrain mobility")
        if budget == "low":
            query_parts.append("budget travel cheap affordable")
        elif budget == "high":
            query_parts.append("luxury premium high-end travel")
        if kids and kids.lower() == "yes":
            query_parts.append("family friendly kids children")

        query = " ".join(query_parts)
        print(f"[RETRIEVAL AGENT] RAG query: '{query}'")

        try:
            results = self.vector_db.similarity_search(query, k=k)
            if not results:
                print(f"[RETRIEVAL AGENT] No RAG results for '{city}'. Returning empty context.")
                return "No additional background knowledge found for this destination."

            context_lines = [f"- {doc.page_content}" for doc in results]
            context = "\n".join(context_lines)
            print(f"[RETRIEVAL AGENT] Retrieved {len(results)} RAG documents for '{city}'.")
            return context

        except Exception as e:
            print(f"[ERROR] RetrievalAgent RAG search failed for '{city}': {str(e)}")
            return ""

    # ─────────────────────────────────────────────────────────────────────
    # Web Retrieval (International Destinations)
    # ─────────────────────────────────────────────────────────────────────

    def _retrieve_from_web(self, city: str, user_prefs: dict) -> str:
        """
        Retrieves travel information for international destinations using
        DuckDuckGo web search. Results are cached by (city, hobbies, budget)
        to avoid repeated web calls.

        Returns a context string in the same format as RAG results.
        """
        hobbies = user_prefs.get("hobbies", "")
        budget = user_prefs.get("budget", "medium")
        health = user_prefs.get("health", "")
        kids = user_prefs.get("kids", "")

        # Build cache key
        cache_key = self._make_cache_key(city, hobbies, budget)
        cached = self._get_from_cache(cache_key)
        if cached:
            print(f"[RETRIEVAL AGENT] ✅ Cache hit for international destination: '{city}'")
            return cached

        print(f"[RETRIEVAL AGENT] 🔍 Fetching web information for: '{city}'")

        # Build targeted search queries
        queries = self._build_web_queries(city, hobbies, budget, health, kids)

        all_snippets = []
        for query in queries:
            snippets = self._ddg_search(query, max_results=3)
            all_snippets.extend(snippets)
            if len(all_snippets) >= 10:
                break  # Enough data

        if not all_snippets:
            print(f"[RETRIEVAL AGENT] No web results for '{city}'. Returning fallback.")
            fallback = self._build_fallback_context(city, user_prefs)
            self._save_to_cache(cache_key, fallback)
            return fallback

        # Normalise web snippets into context string (same format as RAG)
        context = self._normalize_web_results(all_snippets, city, user_prefs)
        print(f"[RETRIEVAL AGENT] Web retrieval complete: {len(all_snippets)} snippets for '{city}'.")

        # Cache the result
        self._save_to_cache(cache_key, context)
        return context

    def _build_web_queries(self, city: str, hobbies: str, budget: str,
                           health: str, kids: str) -> list:
        """Build a list of targeted search queries for the international destination."""
        queries = [
            f"top tourist attractions to visit in {city}",
            f"best travel tips for visiting {city}",
            f"best time to visit {city} weather seasons",
            f"accommodation hotels in {city} for tourists",
            f"transportation getting around {city}",
        ]
        if hobbies:
            queries.append(f"{hobbies} activities experiences in {city}")
        if health == "limited":
            queries.append(f"wheelchair accessible attractions {city} mobility")
        if budget == "low":
            queries.append(f"budget travel tips cheap things to do in {city}")
        elif budget == "high":
            queries.append(f"luxury hotels fine dining premium experiences in {city}")
        if kids and kids.lower() == "yes":
            queries.append(f"family friendly activities kids things to do {city}")
        return queries

    def _ddg_search(self, query: str, max_results: int = 3) -> list:
        """
        Perform a DuckDuckGo text search and return a list of snippet dicts.
        Falls back gracefully if the library is unavailable.
        """
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "body": r.get("body", ""),
                        "href": r.get("href", ""),
                    })
            return results
        except ImportError:
            print("[WARN] duckduckgo_search library not available. Install it with: pip install duckduckgo-search")
            return []
        except Exception as e:
            print(f"[WARN] DuckDuckGo search failed for query '{query}': {e}")
            return []

    def _normalize_web_results(self, snippets: list, city: str, user_prefs: dict) -> str:
        """
        Convert raw web search snippets into the same structured context string
        format as ChromaDB RAG results. This ensures downstream agents consume
        both sources identically.
        """
        hobbies = user_prefs.get("hobbies", "")
        budget = user_prefs.get("budget", "medium")

        lines = [
            f"Travel information for {city} (sourced from web retrieval):",
            "",
        ]

        seen_bodies = set()
        attraction_lines = []
        tip_lines = []
        accommodation_lines = []
        transport_lines = []
        other_lines = []

        for snippet in snippets:
            title = snippet.get("title", "").strip()
            body = snippet.get("body", "").strip()

            if not body or body in seen_bodies:
                continue
            seen_bodies.add(body)

            # Classify snippet by title keywords
            title_lower = title.lower()
            body_lower = body.lower()
            combined_lower = title_lower + " " + body_lower

            entry = f"- {title}: {body}" if title else f"- {body}"

            if any(kw in combined_lower for kw in ["attraction", "must see", "visit", "landmark", "sight", "museum", "park", "temple", "beach", "monument"]):
                attraction_lines.append(entry)
            elif any(kw in combined_lower for kw in ["hotel", "accommodation", "stay", "hostel", "resort", "airbnb"]):
                accommodation_lines.append(entry)
            elif any(kw in combined_lower for kw in ["transport", "getting around", "metro", "bus", "taxi", "train", "airport"]):
                transport_lines.append(entry)
            elif any(kw in combined_lower for kw in ["tip", "advice", "best time", "season", "weather", "when to visit", "safety", "currency"]):
                tip_lines.append(entry)
            else:
                other_lines.append(entry)

        # Assemble context with sections
        if attraction_lines:
            lines.append(f"Key Attractions in {city}:")
            lines.extend(attraction_lines[:5])
            lines.append("")

        if accommodation_lines:
            lines.append(f"Accommodation Options in {city}:")
            lines.extend(accommodation_lines[:3])
            lines.append("")

        if transport_lines:
            lines.append(f"Getting Around {city}:")
            lines.extend(transport_lines[:2])
            lines.append("")

        if tip_lines:
            lines.append(f"Travel Tips for {city}:")
            lines.extend(tip_lines[:4])
            lines.append("")

        if other_lines:
            lines.extend(other_lines[:3])

        context = "\n".join(lines).strip()

        if not context or len(context) < 50:
            return self._build_fallback_context(city, user_prefs)

        return context

    def _build_fallback_context(self, city: str, user_prefs: dict) -> str:
        """
        Return a minimal fallback context string when web retrieval produces
        no usable results. This prevents downstream agents from receiving an
        empty context.
        """
        budget = user_prefs.get("budget", "medium")
        hobbies = user_prefs.get("hobbies", "general sightseeing")
        return (
            f"Travel information for {city}: "
            f"{city} is an international destination. "
            f"Popular tourist attractions, local experiences, and cultural highlights are available for visitors. "
            f"Budget level: {budget}. Key interests: {hobbies}. "
            f"For the most accurate and up-to-date travel information about {city}, "
            f"consult travel portals such as Lonely Planet, TripAdvisor, or the official tourism website."
        )

    # ─────────────────────────────────────────────────────────────────────
    # Caching Utilities
    # ─────────────────────────────────────────────────────────────────────

    def _make_cache_key(self, city: str, hobbies: str, budget: str) -> str:
        raw = f"{city.lower().strip()}|{hobbies.lower().strip()}|{budget.lower().strip()}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_from_cache(self, key: str):
        entry = self._web_cache.get(key)
        if not entry:
            return None
        if time.time() - entry["timestamp"] > self._cache_ttl_seconds:
            del self._web_cache[key]
            return None
        return entry["result"]

    def _save_to_cache(self, key: str, result: str):
        self._web_cache[key] = {"result": result, "timestamp": time.time()}


# ─────────────────────────────────────────────────────────────────────────────
# Manual test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    agent = RetrievalAgent()

    test_cases = [
        # Indian destinations
        ({"hobbies": "history", "health": "limited"}, "Jaipur"),
        ({"hobbies": "beaches", "budget": "low"}, "Goa"),
        ({"hobbies": "culture", "budget": "medium"}, "Mumbai"),
        ({"hobbies": "food temples", "budget": "medium"}, "Hyderabad"),
        # International destinations
        ({"hobbies": "art museums", "budget": "high", "is_international": True}, "Paris"),
        ({"hobbies": "temples", "budget": "medium", "is_international": True}, "Tokyo"),
        ({"hobbies": "beaches shopping", "budget": "high", "is_international": True}, "Dubai"),
        ({"hobbies": "history", "budget": "medium", "is_international": True}, "Singapore"),
    ]

    print("\n" + "="*70)
    print("HYBRID RETRIEVAL AGENT — TEST CASES")
    print("="*70)
    for prefs, city in test_cases:
        print(f"\n📍 Destination: {city}")
        print(f"   Preferences: {prefs}")
        context = agent.retrieve_context(prefs, city, k=3)
        preview = context[:200].replace("\n", " ") + ("..." if len(context) > 200 else "")
        print(f"   Context: {preview}")
        print("-"*70)
