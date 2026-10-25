import json
from workflows.travel_graph import TravelGraph

graph = TravelGraph()
try:
    print(json.dumps(graph.state))
    print("Serialization SUCCESS")
except Exception as e:
    import traceback
    traceback.print_exc()
