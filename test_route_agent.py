from agents.route_agent import RouteAgent
from agents.information_agent import InformationAgent
import json

agent = RouteAgent()
spots = [
    {"id": "1", "location": {"lat": 27.1751, "lng": 78.0421}, "name": "Taj Mahal"},
    {"id": "2", "location": {"lat": 27.1795, "lng": 78.0211}, "name": "Agra Fort"},
    {"id": "3", "location": {"lat": 27.2000, "lng": 78.0000}, "name": "Fake Spot"}
]
try:
    res = agent.optimize_daily_route(spots)
    print(json.dumps(res, indent=2))
except Exception as e:
    print(f"FAILED: {e}")
