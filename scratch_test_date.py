import os
import sys

from datetime import datetime
from agents.route_agent import RouteAgent

if __name__ == "__main__":
    agent = RouteAgent()
    daily_plan = {
        "day1": ["Spot A", "Spot B"],
        "day2": ["Spot C"],
        "day3": ["Spot D"]
    }
    all_spots = {
        "Spot A": {"name": "Spot A", "estimated_duration": 1},
        "Spot B": {"name": "Spot B", "estimated_duration": 1},
        "Spot C": {"name": "Spot C", "estimated_duration": 1},
        "Spot D": {"name": "Spot D", "estimated_duration": 1}
    }
    
    itinerary = agent.format_daily_plan_to_itinerary(daily_plan, all_spots, "2026-07-23")
    for day in itinerary:
        print(f"Day {day['day']} - {day['date']}")
