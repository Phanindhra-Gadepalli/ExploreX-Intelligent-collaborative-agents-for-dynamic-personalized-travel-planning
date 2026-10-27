import json
import random
from typing import List, Optional

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
# langchain-google-genai 2.x converts pydantic v2 schemas for Gemini function
# declarations. The old langchain_core.pydantic_v1 shim is deprecated in
# langchain-core 0.3 and removed in 1.0, so use the native v2 models.
from pydantic import BaseModel, Field

from services.structured_parse import records_from_text, clamp_rating, clamp_int, clean_name
from services.interest_match import match_interests

class POILocation(BaseModel):
    lat: float
    lng: float

class POIExtraction(BaseModel):
    """Honest RAG POI schema.

    Unknown values are ``None`` — the previous schema defaulted every record to
    ``rating=4.5`` / ``user_ratings_total=100`` / ``price_level=2``, which made
    model output look like verified provider data.
    """
    id: str = ""
    name: str
    category: str = "tourist_attraction"
    description: str = ""
    address: str = ""
    types: List[str] = Field(default_factory=list)
    rating: Optional[float] = None
    user_ratings_total: Optional[int] = None
    price_level: Optional[int] = None
    location: Optional[POILocation] = None
    source: str = "rag_extraction"

class POIExtractionList(BaseModel):
    pois: List[POIExtraction]

class RecommendAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize RecommendAgent with AI model for personalized recommendations"""
        self.model_name = model_name
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7)
        try:
            from sentence_transformers import SentenceTransformer
            import torch
            # Use CPU by default to avoid CUDA issues in some envs unless specified
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.embedder = SentenceTransformer('all-MiniLM-L6-v2', device=device)
        except ImportError:
            print("Warning: sentence_transformers not installed. Semantic scoring will fallback to keyword matching.")
            self.embedder = None
            
        self.taxonomy = {
            'heritage': 'history, monuments, architecture, forts, palaces, ancient ruins, tombs, heritage site, historical landmark',
            'nature': 'parks, gardens, wildlife, waterfalls, mountains, lakes, beaches, outdoors, scenic, viewpoint, flora, fauna',
            'spirituality': 'temples, churches, mosques, shrines, religious, meditation, ashram, ghat, pilgrimage, holy',
            'sightseeing': 'general sightseeing, landmarks, iconic places, tourist attraction, popular spot',
            'adventure': 'trekking, hiking, water sports, camping, thrill, adventurous activities',
            'culture': 'museums, art galleries, local markets, cultural centers, traditional, performing arts'
        }

    def recommend_destinations(self, interests, weight=80):
        """Recommend global destinations based on user interests"""
        import random
        base_destinations = [
            {
                "id": "paris_fr",
                "name": "Paris",
                "country": "France",
                "image": "https://images.unsplash.com/photo-1502602898657-3e9076113801?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                "tags": ["History", "Architecture", "Art & Culture", "Photography"],
                "base_cost": 1850,
                "rating": 4.8,
                "weather_snippet": "18°C, Partly Cloudy"
            },
            {
                "id": "agra_in",
                "name": "Agra",
                "country": "India",
                "image": "https://images.unsplash.com/photo-1564507592333-c60657eea523?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                "tags": ["History", "Architecture", "Spiritual"],
                "base_cost": 950,
                "rating": 4.7,
                "weather_snippet": "32°C, Sunny"
            },
            {
                "id": "tokyo_jp",
                "name": "Tokyo",
                "country": "Japan",
                "image": "https://images.unsplash.com/photo-1540959733332-eab4deabeeaf?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                "tags": ["Technology", "Food", "Nightlife", "Shopping"],
                "base_cost": 1200,
                "rating": 4.9,
                "weather_snippet": "22°C, Clear Sky"
            },
            {
                "id": "bali_id",
                "name": "Bali",
                "country": "Indonesia",
                "image": "https://images.unsplash.com/photo-1537996194471-e657df975ab4?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                "tags": ["Nature", "Adventure", "Spiritual"],
                "base_cost": 850,
                "rating": 4.8,
                "weather_snippet": "28°C, Tropical"
            },
            {
                "id": "dubai_ae",
                "name": "Dubai",
                "country": "UAE",
                "image": "https://images.unsplash.com/photo-1512453979798-5ea266f8880c?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                "tags": ["Architecture", "Shopping", "Luxury"],
                "base_cost": 2100,
                "rating": 4.7,
                "weather_snippet": "35°C, Hot"
            },
            {
                "id": "new_york_us",
                "name": "New York",
                "country": "USA",
                "image": "https://images.unsplash.com/photo-1496442226666-8d4d0e62e6e9?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                "tags": ["Art & Culture", "Food", "Nightlife"],
                "base_cost": 2500,
                "rating": 4.8,
                "weather_snippet": "15°C, Breezy"
            }
        ]

        interest_lower = [i.lower() for i in (interests or [])]
        results = []
        for dest in base_destinations:
            dest_tags_lower = [t.lower() for t in dest['tags']]
            match_count = sum(1 for tag in dest_tags_lower if tag in interest_lower)
            
            if not interests:
                match_percentage = random.randint(75, 95)
            else:
                if match_count > 0:
                    base_match = 80 + (match_count * 5)
                    weight_factor = (weight - 50) / 100.0 if weight > 50 else 0
                    match_percentage = min(98, int(base_match + (weight_factor * 10)))
                else:
                    match_percentage = random.randint(60, 75)
            
            dest['match_percentage'] = match_percentage
            # Find the first tag that matches interests, or just use the first tag
            matching_tags = [t for t in dest['tags'] if t.lower() in interest_lower]
            dest['tag'] = matching_tags[0] if matching_tags else dest['tags'][0]
            dest['estimated_cost'] = f"${dest['base_cost']}"
            results.append(dest)
            
        results.sort(key=lambda x: x['match_percentage'], reverse=True)
        return results
        
    def recommend_core_attractions(self, user_prefs, attractions, retrieved_knowledge=None):
        """Recommend core attractions based on user preferences"""
        # Extract preferences
        budget = user_prefs.get('budget', 'medium').lower()
        people = int(user_prefs.get('people', 1))
        has_kids = user_prefs.get('kids', 'no').lower() == 'yes'
        health = user_prefs.get('health', 'good').lower()
        hobbies = user_prefs.get('hobbies', '').lower()
        
        # Expand user hobbies using taxonomy
        expanded_hobbies = hobbies
        for key, value in self.taxonomy.items():
            if key in hobbies:
                expanded_hobbies += f" {value}"
                
        user_embedding = None
        if self.embedder and expanded_hobbies.strip():
            user_embedding = self.embedder.encode(expanded_hobbies, convert_to_tensor=True)
        elif self.embedder:
            # If no hobbies provided, use a generic embedding to avoid errors
            user_embedding = self.embedder.encode("general tourist attraction", convert_to_tensor=True)

        scored_attractions = []
        for attraction in attractions:
            # Skip if category is completely missing as we need it for hobby matching
            if attraction.get('category') is None:
                continue
                
            price_level = attraction.get('price_level') or 2
            
            # Hard constraints (budget & health)
            # Budget filter
            if budget == 'low' and price_level > 2:
                continue
            elif budget == 'medium' and price_level > 3:
                continue
            
            # Health considerations (hard filter)
            if health == 'limited' and attraction.get('accessibility') == 'limited':
                continue
                
            # Family-friendly constraint
            # Only exclude if explicitly marked as not family friendly or if category is inappropriate
            if has_kids:
                inappropriate_for_kids = ['nightclub', 'bar', 'pub', 'casino', 'liquor_store', 'adult_entertainment']
                is_explicitly_not_family = attraction.get('family_friendly') is False
                is_inappropriate_category = any(bad in str(attraction.get('category', '')).lower() for bad in inappropriate_for_kids)
                if is_explicitly_not_family or is_inappropriate_category:
                    continue
                    
            # SCORING
            score = 0.0
            
            # 1. Semantic Score (40% weight)
            semantic_score = 0.0
            if user_embedding is not None:
                from sentence_transformers import util
                attr_text = f"{attraction.get('name', '')} {attraction.get('category', '')} {attraction.get('description', '')} {attraction.get('tags', '')}"
                attr_emb = self.embedder.encode(attr_text, convert_to_tensor=True)
                cos_sim = util.cos_sim(user_embedding, attr_emb).item()
                semantic_score = max(0, cos_sim) * 40.0
            else:
                # Fallback heuristic
                if any(h in str(attraction.get('category')).lower() for h in hobbies.split(',')):
                    semantic_score = 40.0
                elif any(h in str(attraction.get('description', '')).lower() for h in hobbies.split(',')):
                    semantic_score = 20.0
            
            score += semantic_score
            
            # 2. Popularity Score (30% weight)
            rating = float(attraction.get('rating', 0) or 0)
            user_ratings_total = float(attraction.get('user_ratings_total', 0) or 0)
            
            # Normalize rating (0 to 5) -> (0 to 1) -> (0 to 30)
            rating_norm = (rating / 5.0) * 15.0 
            # Normalize review count (log scale, assuming 10,000 is a very popular spot)
            import math
            review_norm = min(15.0, (math.log10(user_ratings_total + 1) / 4.0) * 15.0)
            
            popularity_score = rating_norm + review_norm
            score += popularity_score
            
            # 3. Category/Direct Match Bonus (20% weight)
            category = str(attraction.get('category') or '').lower()
            if category and category in hobbies:
                score += 20.0
                
            # 4. Location Context (10% weight)
            if attraction.get('location'):
                score += 10.0
                
            scored_attractions.append((score, popularity_score, attraction))
            
        # Sort by total score descending
        scored_attractions.sort(key=lambda x: x[0], reverse=True)
        
        # Categorize into interest_based vs popular
        final_attractions = []
        seen_ids = set()
        
        # Take top matches that are highly semantically aligned as interest_based
        # and generally high scoring as popular
        for score, popularity_score, attr in scored_attractions:
            if attr['id'] in seen_ids:
                continue
            
            # Label interest alignment with the shared deterministic matcher,
            # never with popularity. A high rating cannot turn a shopping mall
            # into a "photography" match. These labels mirror the workflow's
            # canonical values (interest_based / outside_interests / discovery).
            matched, _reasons = match_interests(attr, user_prefs or {})
            metadata = attr.get('metadata') if isinstance(attr.get('metadata'), dict) else {}
            notable = bool(
                attr.get('source') == 'wikipedia'
                or attr.get('wikipedia_url')
                or metadata.get('wikidata_id')
                or attr.get('wikidata_id')
            )
            if matched:
                attr['recommendation_type'] = 'interest_based'
            elif not str((user_prefs or {}).get('hobbies') or '').strip():
                attr['recommendation_type'] = 'discovery'
            elif notable:
                # Verifiable notable places broaden the trip honestly instead
                # of being discarded as "outside interests".
                attr['recommendation_type'] = 'discovery'
            else:
                attr['recommendation_type'] = 'outside_interests'
                
            final_attractions.append(attr)
            seen_ids.add(attr['id'])
            
        # Return the full ranked set.  Breadth is bounded by the duration-aware
        # discovery target upstream; an extra top-50 cut here silently removed
        # verified attractions from the Explore grid.
        return final_attractions
    
    def _create_recommendation_prompt(self, user_prefs, attractions, retrieved_knowledge=None):
        """Create prompt for the LLM to rank attractions"""
        attractions_str = json.dumps(attractions, indent=2)
        user_prefs_str = json.dumps(user_prefs, indent=2)
        
        rag_context = ""
        if retrieved_knowledge:
            rag_context = f"\nBackground Knowledge to consider for ranking:\n{retrieved_knowledge}\n"
            
        return f"""
        Given the following user preferences and attractions, rank the attractions from most suitable to least suitable.
        
        User preferences:
        {user_prefs_str}
        {rag_context}
        
        Attractions:
        {attractions_str}
        
        Consider the following factors with strong emphasis on user preferences:
        1. Match between user's hobbies and attraction categories - this should be the PRIMARY factor in ranking
        2. Physical accessibility based on user's health status - PRIORITIZE attractions that accommodate the user's health condition
        3. Suitability for children if the user is traveling with kids
        4. Budget constraints
        5. Variety of attraction categories to provide a balanced experience
        
        For users with health considerations, ensure attractions are accessible and not overly strenuous.
        For users with specific hobbies, prioritize attractions that directly match these interests.
        
        Return a list of attraction IDs, ranked from most to least recommended, in this format:
        [
          "attraction_id_1",
          "attraction_id_2",
          ...
        ]
        """
    
    def _score_attractions(self, user_prefs, attractions):
        """Score attractions based on user preferences (fallback method if LLM ranking is not used or fails)"""
        scored_attractions = []
        
        for attraction in attractions:
            score = 0
            
            # Score based on category match
            if "hobbies" in user_prefs and attraction.get("category") in user_prefs["hobbies"]:
                score += 3
            
            # Score based on health considerations
            if "health" in user_prefs:
                if user_prefs["health"] == "excellent":
                    score += 1  # Any attraction is fine
                elif user_prefs["health"] == "good" and attraction.get("estimated_duration", 3) <= 3:
                    score += 1  # Prefer shorter duration attractions
                elif user_prefs["health"] == "limited" and attraction.get("estimated_duration", 3) <= 2:
                    score += 1  # Strongly prefer shorter attractions
            
            # Score based on budget
            if "budget" in user_prefs:
                budget_level = {"low": 1, "medium": 2, "high": 3}.get(user_prefs["budget"], 2)
                if attraction.get("price_level", 2) <= budget_level:
                    score += 1
            
            # Score based on kids
            if user_prefs.get("kids", False) and attraction.get("kid_friendly", False):
                score += 1
            
            scored_attractions.append((attraction["id"], score, attraction))
        

        scored_attractions.sort(key=lambda x: x[1], reverse=True)
        
        # Return sorted attractions (full objects)
        return [item[2] for item in scored_attractions]
    
    def generate_map_data(self, attractions):
        """Generate map data for frontend visualization"""
        map_data = []
        
        for attraction in attractions:
            # Check if location exists and has valid coordinates
            if not attraction.get("location") or not isinstance(attraction["location"], dict):
                continue
                
            map_data.append({
                "id": attraction.get("id"),
                "name": attraction.get("name"),
                "lat": attraction["location"].get("lat"),
                "lng": attraction["location"].get("lng"),
                "category": attraction.get("category", "other")
            })
        
        return map_data
    
    def get_attraction_details(self, attraction_id, attractions):
        """Get detailed information about a specific attraction"""
        for attraction in attractions:
            if attraction["id"] == attraction_id:
                return attraction
        
        return None
        
    def extract_pois_from_text(self, text, city, lat, lng):
        """Extract POIs that are explicitly named in retrieved text.

        Coordinates are optional: when the text does not state them the caller
        (``TravelGraph._process_retrieval``) geocodes the name inside the
        destination and drops anything it cannot verify.  Ratings, review counts
        and prices are only kept when the text states them.
        """
        if not text or "No additional background knowledge" in text or len(text) < 20:
            return []

        prompt = f"""
        Extract every tourist attraction or point of interest that is EXPLICITLY
        named in the text below about {city}. Do not add places from your own
        knowledge and do not invent details.

        Return JSON of the form:
        {{"pois": [
          {{"name": "...", "category": "...", "description": "...",
            "address": "...", "latitude": null, "longitude": null,
            "rating": null, "user_ratings_total": null, "price_level": null}}
        ]}}

        Rules:
        - "description" may only summarise what the text says.
        - Include latitude/longitude (decimal degrees) ONLY if the text states
          coordinates; otherwise leave both null. Never guess a position.
        - Include rating/review count/price only if the text states them.
        - Never return generic entries such as "City Center" or "Local Market".

        Text:
        {text}
        """

        pois = []
        try:
            json_model = ChatGoogleGenerativeAI(
                model=self.model_name,
                temperature=0,
                model_kwargs={"response_mime_type": "application/json"},
            )
            raw = json_model.invoke([HumanMessage(content=prompt)]).content
            if isinstance(raw, list):  # some versions return multimodal parts
                raw = "".join(
                    part.get("text", "") for part in raw
                    if isinstance(part, dict) and "text" in part
                )
            records = records_from_text(raw)
            pois = self._normalize_extracted_pois(records, city, lat, lng)
        except Exception as e2:
            print(f"[RECOMMEND_AGENT] RAG POI extraction failed (non-fatal): {e2}. Falling back to empty list.")
            pois = []

        if pois:
            print(f"[RECOMMEND_AGENT] Successfully extracted {len(pois)} POIs from RAG knowledge.")
        return pois

    @staticmethod
    def _normalize_extracted_pois(records, city, lat, lng):
        """Validate, de-duplicate and normalise raw extracted records."""
        import uuid

        normalized = []
        seen = set()
        for item in records or []:
            if not isinstance(item, dict):
                continue
            name = clean_name(item.get("name") or item.get("title"))
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)

            item_lat = item.get("latitude", item.get("lat"))
            item_lng = item.get("longitude", item.get("lng", item.get("lon")))
            loc = item.get("location") if isinstance(item.get("location"), dict) else {}
            if item_lat is None:
                item_lat = loc.get("lat", loc.get("latitude"))
            if item_lng is None:
                item_lng = loc.get("lng", loc.get("longitude"))
            location = None
            try:
                if item_lat is not None and item_lng is not None:
                    item_lat, item_lng = float(item_lat), float(item_lng)
                    if -90 <= item_lat <= 90 and -180 <= item_lng <= 180:
                        location = {"lat": item_lat, "lng": item_lng}
            except (TypeError, ValueError):
                location = None

            rating = clamp_rating(item.get("rating"))
            review_count = clamp_int(item.get("user_ratings_total", item.get("review_count")))
            price_level = clamp_int(item.get("price_level"))

            poi = {
                "id": str(item.get("id") or f"rag_{uuid.uuid4().hex[:8]}"),
                "name": name,
                "category": str(item.get("category") or "tourist_attraction"),
                "description": str(item.get("description") or "").strip(),
                "address": str(item.get("address") or "").strip(),
                "latitude": location["lat"] if location else None,
                "longitude": location["lng"] if location else None,
                "location": location,
                "coords_verified": bool(location),
                "verified": False,
                "rating": rating,
                "review_count": review_count,
                "rating_status": "reported_by_source" if rating is not None else "unavailable",
                "price_level": price_level,
                "price_status": "reported_by_source" if price_level is not None else "unavailable",
                "types": item.get("types") if isinstance(item.get("types"), list) else [],
                "city": city,
                "source": "rag",
            }
            poi["id"] = poi["id"] if poi["id"] else f"rag_{uuid.uuid4().hex[:8]}"
            normalized.append(poi)
        return normalized
