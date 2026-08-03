import json
import random
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

class RecommendAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize RecommendAgent with AI model for personalized recommendations"""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7)
        
    def recommend_core_attractions(self, user_prefs, attractions, retrieved_knowledge=None):
        """Recommend core attractions based on user preferences"""
        # Extract preferences
        budget = user_prefs.get('budget', 'medium').lower()
        people = int(user_prefs.get('people', 1))
        has_kids = user_prefs.get('kids', 'no').lower() == 'yes'
        health = user_prefs.get('health', 'good').lower()
        hobbies = user_prefs.get('hobbies', '').lower()
        
        # Filter attractions based on preferences
        filtered_attractions = []
        for attraction in attractions:
            # Skip if category is completely missing as we need it for hobby matching
            if attraction.get('category') is None:
                continue
                
            price_level = attraction.get('price_level') or 2
            
            # Budget filter
            if budget == 'low' and price_level > 2:
                continue
            elif budget == 'medium' and price_level > 3:
                continue
            elif budget == 'high' and price_level > 4:
                continue
            
            # Family-friendly filter
            if has_kids and not attraction.get('family_friendly', False):
                continue
            
            # Health considerations
            if health == 'limited' and attraction.get('accessibility') == 'limited':
                continue
            
            filtered_attractions.append(attraction)
        
        # Sort by rating and duration
        filtered_attractions.sort(
            key=lambda x: (
                x.get('rating', 0) or 0,  # Handle None values for rating
                -(x.get('estimated_duration', 0) or 0)  # Handle None values for duration, sort descending
            ),
            reverse=True
        )
        
        # Return top recommendations
        return filtered_attractions[:10]
    
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
    
