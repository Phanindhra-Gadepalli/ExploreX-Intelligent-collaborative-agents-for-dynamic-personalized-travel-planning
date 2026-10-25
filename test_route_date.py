import os
from workflows.travel_graph import TravelGraph

graph = TravelGraph()
graph.state = {
    "user_info": {
        "start_date": "2026-07-23",
        "days": 3,
        "city": "Mumbai"
    },
    "attractions": [
        {"name": "Gateway of India", "estimated_duration": 2},
        {"name": "Elephanta Caves", "estimated_duration": 4},
        {"name": "Marine Drive", "estimated_duration": 2}
    ],
    "daily_plan": {
        "day1": ["Gateway of India"],
        "day2": ["Elephanta Caves"],
        "day3": ["Marine Drive"]
    },
    "should_rent_car": False
}

res = graph._process_route()
itinerary = res.get("itinerary", [])
for day in itinerary:
    print(f"Day {day.get('day')} - {day.get('date')}")
