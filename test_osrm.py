import urllib.request
import json

coords = "78.0081,27.1767;78.0421,27.1751"
url = f"http://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"
req = urllib.request.Request(url)
try:
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read())
        print(data['code'])
        print("Distance:", data['routes'][0]['distance'])
        print("Geometry type:", data['routes'][0]['geometry']['type'])
except Exception as e:
    print(e)
