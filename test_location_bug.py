import os
import sys
import json
sys.path.append(os.path.dirname(os.path.abspath('')))

from workflows.travel_graph import TravelGraph

graph = TravelGraph()
graph.state = {
    "user_info": {"days": "3", "city": "Delhi", "people": "2"},
    "attractions": [
        {"id": "1", "name": "Red Fort", "location": None}, # MISSING LOCATION
        {"id": "2", "name": "India Gate"} # COMPLETELY MISSING LOCATION KEY
    ],
    "daily_plan": {"day1": ["Red Fort"], "day2": ["India Gate"]},
    "should_rent_car": False
}
try:
    res = graph._process_route()
    print("ROUTE GENERATED SUCCESSFULLY EVEN WITH MISSING LOCATIONS!")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
