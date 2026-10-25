import json
import random
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
# langchain_google_genai only recognises langchain_core.pydantic_v1.BaseModel
# schemas in convert_to_genai_function_declarations; a pydantic v2 model falls
# through to the callable branch and raises "Value not declarable with JSON Schema".
from langchain_core.pydantic_v1 import BaseModel, Field
from typing import List

class POILocation(BaseModel):
    lat: float
    lng: float

class POIExtraction(BaseModel):
    id: str = ""
    name: str
    rating: float = 4.5
    user_ratings_total: int = 100
    price_level: int = 2
    address: str = ""
    location: POILocation
    category: str = "tourist_attraction"
    types: List[str] = ["tourist_attraction"]
    description: str = ""
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
            if attraction.get('category') and attraction.get('category').lower() in hobbies:
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
            
            # If the score is high and it has decent semantic alignment (more than just popularity), it's interest_based
            if score >= 50.0 and (self.embedder is None or score > popularity_score + 10.0):
                attr['recommendation_type'] = 'interest_based'
            else:
                attr['recommendation_type'] = 'popular'
                
            final_attractions.append(attr)
            seen_ids.add(attr['id'])
            
        # Return top 50 recommendations to give a mix
        return final_attractions[:50]
    
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
        """Extracts structured POI dictionaries from RAG text using LLM."""
        if not text or "No additional background knowledge" in text or len(text) < 20:
            return []
            
        prompt = f"""
        Extract all tourist attractions, points of interest, and famous places mentioned in the following text about {city}.
        
        Text:
        {text}
        
        Extract the attractions and return them as a JSON list.
        """
        
        pois = []
        try:
            # The installed google-genai converter cannot express array fields
            # inside a Gemini function declaration (it omits `items`, producing
            # "properties[pois].items: missing field"), so with_structured_output
            # fails for any schema that contains a list. Native JSON mode
            # (response_mime_type=application/json + response_schema) accepts the
            # same schema reliably, so we use it here and parse the returned JSON
            # into the Pydantic model ourselves.
            json_model = ChatGoogleGenerativeAI(
                model=self.model_name,
                temperature=0,
                model_kwargs={
                    "response_mime_type": "application/json",
                    "response_schema": POIExtractionList,
                },
            )
            raw = json_model.invoke([HumanMessage(content=prompt)]).content
            if isinstance(raw, list):  # some versions return multimodal parts
                raw = "".join(
                    part.get("text", "") for part in raw
                    if isinstance(part, dict) and "text" in part
                )
            text = (raw or "").strip()
            # Strip markdown code fences (```json ... ```) if present.
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            data = json.loads(text)
            items = data.get("pois", []) if isinstance(data, dict) else data
            for item in items:
                if not isinstance(item, dict):
                    continue
                loc = item.get("location")
                if not isinstance(loc, dict) or loc.get("lat") is None or loc.get("lng") is None:
                    continue  # skip POIs the route/map cannot place
                try:
                    pois.append(POIExtraction(**item).dict())
                except Exception:
                    continue
        except Exception as e2:
            print(f"[RECOMMEND_AGENT] RAG POI extraction failed (non-fatal): {e2}. Falling back to empty list.")
            pois = []

        if isinstance(pois, list):
            import uuid
            for p in pois:
                if "id" not in p or not p["id"]:
                    p["id"] = f"rag_{uuid.uuid4().hex[:8]}"
            print(f"[RECOMMEND_AGENT] Successfully extracted {len(pois)} POIs from RAG knowledge.")
            return pois
            
        return []
