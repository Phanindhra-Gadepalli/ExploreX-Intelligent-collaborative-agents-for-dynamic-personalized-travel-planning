import os
import sys
import json
from datetime import datetime
sys.path.append(os.path.dirname(os.path.abspath('')))

from workflows.travel_graph import TravelGraph

graph = TravelGraph()
graph.state = {
    "user_info": {"days": "3", "city": "Delhi", "people": "2", "start_date": "next week"},
    "attractions": [
        {"id": "1", "name": "Red Fort", "location": {"lat": 28.656, "lng": 77.241}},
    ],
    "daily_plan": {"day1": ["Red Fort"]},
    "should_rent_car": False
}
try:
    res = graph._process_route()
    print("RESULT:")
    print(json.dumps(res, indent=2))
except Exception as e:
    print(f"Global Error: {e}")
