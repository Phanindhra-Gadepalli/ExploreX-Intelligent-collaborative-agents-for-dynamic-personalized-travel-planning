import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

class TransitAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize the TransitAgent with specified model."""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7)
    
    def get_transit_options(self, origin, destination, start_date, budget_level="medium"):
        """Generate mocked transit options using Gemini."""
        prompt = f"""
        Generate realistic but mocked travel options from {origin} to {destination} in India for a trip starting on {start_date}.
        If the origin or destination contains extra descriptive text (e.g. "from andhra pradesh"), please infer the actual city name.
        The budget level is {budget_level}. Make the prices realistic for Indian Rupees (INR).
        
        Provide the options strictly as a JSON object with the following structure:
        {{
            "recommended_mode": "train",
            "flights": [
                {{
                    "operator": "IndiGo",
                    "departure_time": "08:00",
                    "arrival_time": "10:30",
                    "duration": "2h 30m",
                    "price_inr": 5000,
                    "type": "Non-stop"
                }}
            ],
            "trains": [
                {{
                    "operator": "Rajdhani Express (12951)",
                    "departure_time": "16:30",
                    "arrival_time": "08:30",
                    "duration": "16h 0m",
                    "price_inr": 2500,
                    "type": "AC 2 Tier"
                }}
            ],
            "buses": [
                {{
                    "operator": "IntrCity SmartBus",
                    "departure_time": "21:00",
                    "arrival_time": "09:00",
                    "duration": "12h 0m",
                    "price_inr": 1200,
                    "type": "AC Sleeper"
                }}
            ]
        }}
        
        Provide 2 to 3 options for each category. Even if you are unsure of the exact real-world schedule, you MUST invent highly realistic, plausible mock options based on typical Indian travel routes. Do NOT leave an array empty unless the mode of transport is absolutely physically impossible (like a train across an ocean).
        CRITICAL: Set the "recommended_mode" field to either "flight", "train", or "bus" based on the most practical and cost-effective option for the given distance and budget. For example, nearby destinations should use "bus" or "train", medium-distance should prefer "train" where practical, and long-distance should use "flight" only if it is the most reasonable option for the budget.
        Ensure the output is ONLY valid JSON and contains NO markdown formatting like ```json or ```. Return the raw JSON object.
        """
        
        messages = [
            SystemMessage(content="You are a travel API that returns strictly valid JSON data representing transit schedules in India. Do not return any conversational text."),
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
