import os
import sys
sys.path.append(os.path.dirname(os.path.abspath('')))

from workflows.travel_graph import TravelGraph

graph = TravelGraph()
graph.state = {
    "user_info": {"days": "3", "city": "Delhi", "people": "2"},
    "attractions": [
        {"id": "1", "name": "Red Fort", "location": {"lat": 28.656, "lng": 77.241}},
        {"id": "2", "name": "India Gate", "location": {"lat": 28.612, "lng": 77.229}}
    ],
    "daily_plan": {"day1": ["Red Fort"], "day2": ["India Gate"]},
    "should_rent_car": True
}
try:
    res = graph._process_route()
    print("Success")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
