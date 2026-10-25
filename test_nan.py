import json
from workflows.travel_graph import TravelGraph
import math

def find_nan(obj, path=''):
    if isinstance(obj, float) and math.isnan(obj):
        print(f'NaN found at {path}')
    elif isinstance(obj, dict):
        for k, v in obj.items():
            find_nan(v, path + '.' + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_nan(v, path + f'[{i}]')
    elif isinstance(obj, tuple):
        for i, v in enumerate(obj):
            find_nan(v, path + f'({i})')

graph = TravelGraph()
find_nan(graph.state)
print("Finished checking.")
