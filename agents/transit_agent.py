import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

class TransitAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize the TransitAgent with specified model."""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7)
    
    def get_transit_options(self, origin, destination, start_date, budget_level="medium", is_international=False):
        """Generate mocked transit options using Gemini."""
        location_context = "internationally" if is_international else "in India"
        currency_context = "Make the prices realistic for the local currency or USD." if is_international else "Make the prices realistic for Indian Rupees (INR)."
        
        prompt = f"""
        Generate realistic but mocked travel options from {origin} to {destination} {location_context} for a trip starting on {start_date}.
        If the origin or destination contains extra descriptive text, please infer the actual city name.
        The budget level is {budget_level}. {currency_context}
        
        Provide the options strictly as a JSON object with the following structure:
        {{
            "origin": "Inferred origin city",
            "destination": "Inferred destination city",
            "intercity_transport": [
                {{
                    "mode": "flight",
                    "provider": "IndiGo",
                    "duration": "2h 30m",
                    "estimated_cost": 5000,
                    "currency": "INR",
                    "departure_info": "08:00 AM",
                    "arrival_info": "10:30 AM",
                    "recommendation_reason": "Fastest option for this distance",
                    "is_estimate": true
                }}
            ],
            "local_transport": [
                {{
                    "mode": "metro",
                    "estimated_cost": 200,
                    "currency": "INR",
                    "recommendation_reason": "Convenient for city travel",
                    "is_estimate": true
                }}
            ],
            "recommended_mode": "flight"
        }}
        
        For intercity_transport, provide 2 to 3 options (e.g., flight, train, bus, or car) suitable for {location_context}.
        If providing flight options, do NOT fabricate exact flight availability, flight number, or booking data. Provide general estimated flight costs and clearly label it as estimated by setting "is_estimate": true.
        For local_transport, provide 2 to 3 options (e.g., metro, taxi, rental car, bus, ride-hailing) suitable for {destination}. Since there are travelers, consider group transportation if applicable.
        Even if you are unsure of the exact real-world schedule, you MUST invent highly realistic, plausible mock options based on typical travel routes. Do NOT leave an array empty unless the mode of transport is absolutely physically impossible.
        CRITICAL: Set the "recommended_mode" field to either "flight", "train", "bus", or "car" based on the most practical and cost-effective option for the given distance and budget.
        Ensure the output is ONLY valid JSON and contains NO markdown formatting like ```json or ```. Return the raw JSON object.
        """
        
        messages = [
            SystemMessage(content="You are a travel API that returns strictly valid JSON data representing transit schedules. Do not return any conversational text."),
            HumanMessage(content=prompt)
        ]
        try:
            response = self.model.invoke(messages)
            if isinstance(response.content, str):
                content = response.content.strip()
            elif isinstance(response.content, list):
                # Langchain sometimes returns a list of parts
                parts = []
                for p in response.content:
                    if isinstance(p, dict) and 'text' in p:
                        parts.append(p['text'])
                    elif isinstance(p, str):
                        parts.append(p)
                content = "".join(parts).strip()
            else:
                content = str(response.content)
            
            # Robust JSON extraction
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL | re.IGNORECASE)
            if json_match:
                content = json_match.group(1)
            else:
                # Fallback: find first { and last }
                start_idx = content.find('{')
                end_idx = content.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    content = content[start_idx:end_idx+1]
                
            return json.loads(content.strip())
        except Exception as e:
            import traceback
            print(f"[ERROR] Failed to generate transit options: {e}")
            traceback.print_exc()
            return {"flights": [], "trains": [], "buses": []}
