import json
import math
from datetime import datetime

# Load test_itinerary.json from the previous test_flow
try:
    with open('test_itinerary.json', 'r') as f:
        itinerary = json.load(f)
    print("ITINERARY LOADED.")
except Exception as e:
    print("No test_itinerary.json found.")
    itinerary = None
    
try:
    with open('test_budget.json', 'r') as f:
        budget = json.load(f)
    print("BUDGET LOADED.")
except Exception as e:
    print("No test_budget.json found.")
    budget = None

try:
    with open('test_transit.json', 'r') as f:
        transit_options = json.load(f)
    print("TRANSIT LOADED.")
except Exception as e:
    print("No test_transit.json found.")
    transit_options = None

def scrub_floats(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: scrub_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [scrub_floats(i) for i in obj]
    return obj

completion_data = {
    'type': 'complete',
    'session_id': '123',
    'next_step': 'complete',
    'itinerary': itinerary,
    'budget': budget,
    'transit_options': transit_options,
}

completion_data = scrub_floats(completion_data)
try:
    json_str = json.dumps(completion_data)
    print("JSON SERIALIZATION SUCCESS.")
    print("JSON length:", len(json_str))
    
    # Check if itinerary is empty
    parsed = json.loads(json_str)
    print("Itinerary type:", type(parsed['itinerary']))
    print("Itinerary length:", len(parsed['itinerary']) if parsed['itinerary'] else "NONE/EMPTY")
    
except Exception as e:
    print("JSON SERIALIZATION FAILED:", e)

