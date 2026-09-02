import json
import random
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

class RecommendAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize RecommendAgent with AI model for personalized recommendations"""
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
            
        # Return top 15 recommendations to give a mix
        return final_attractions[:15]
    
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
        
        Format the output EXACTLY as a JSON array of objects. Do not include markdown blocks or any other text.
        Each object must follow this schema:
        {{
            "id": "a unique string identifier based on name (e.g. 'rag_kashi_vishwanath')",
            "name": "Name of the attraction",
            "rating": 4.5,
            "user_ratings_total": 100,
            "price_level": 2,
            "address": "{city}, India",
            "location": {{"lat": {lat}, "lng": {lng}}},
            "category": "tourist_attraction",
            "types": ["tourist_attraction"],
            "description": "A short summary of what this place is based on the text",
            "source": "rag_extraction"
        }}
        """
        
        try:
            response = self.model.invoke([HumanMessage(content=prompt)])
            
            content = response.content
            if isinstance(content, list):
                # Extract text from list of content blocks
                content = " ".join([str(part.get("text", "")) for part in content if isinstance(part, dict)])
            elif not isinstance(content, str):
                content = str(content)
                
            content = content.strip()
            
            # Clean up potential markdown formatting
            import re
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                content = json_match.group(0)
                
            try:
                pois = json.loads(content)
            except json.JSONDecodeError as e:
                print(f"[RECOMMEND_AGENT] JSON decode error: {e}. Attempting ast.literal_eval.")
                import ast
                try:
                    pois = ast.literal_eval(content)
                except Exception as eval_e:
                    print(f"[RECOMMEND_AGENT] ast.literal_eval failed: {eval_e}")
                    return []

            if isinstance(pois, list):
                import uuid
                for p in pois:
                    if "id" not in p or not p["id"]:
                        p["id"] = f"rag_{uuid.uuid4().hex[:8]}"
                print(f"[RECOMMEND_AGENT] Successfully extracted {len(pois)} POIs from RAG knowledge.")
                return pois
        except Exception as e:
            print(f"[RECOMMEND_AGENT] Failed to extract POIs from RAG: {e}")
            
        return []
    
