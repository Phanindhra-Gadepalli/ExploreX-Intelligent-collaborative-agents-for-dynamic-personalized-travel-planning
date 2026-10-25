import os
from dotenv import load_dotenv
load_dotenv()
import googlemaps

gmaps = googlemaps.Client(key=os.getenv("MAPS_API_KEY"))
print("Geocoding 'Good':")
coords = gmaps.geocode("Good")
print(coords)

def validate_indian_location(gmaps, location_name: str) -> bool:
    try:
        geocode_result = gmaps.geocode(location_name)
        if not geocode_result:
            places_result = gmaps.places(query=location_name)
            if not places_result or not places_result.get('results'):
                return False
            first_result = places_result['results'][0]
        else:
            first_result = geocode_result[0]

        lat = first_result['geometry']['location']['lat']
        lng = first_result['geometry']['location']['lng']
        if 6.4 <= lat <= 36.0 and 68.0 <= lng <= 97.5:
            return True
            
        return False
    except:
        return False

print("Is 'Good' in India?", validate_indian_location(gmaps, "Good"))
