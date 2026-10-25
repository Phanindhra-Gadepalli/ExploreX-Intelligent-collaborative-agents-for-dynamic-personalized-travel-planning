import requests
query_nwr = """
[out:json][timeout:15];
nwr["tourism"~"attraction"]["name"~"Eiffel"](around:10000, 48.8584, 2.2945);
out 10 tags center;
"""
r = requests.post('https://overpass-api.de/api/interpreter', data={'data': query_nwr})
print(r.status_code)
print(r.text[:500])
