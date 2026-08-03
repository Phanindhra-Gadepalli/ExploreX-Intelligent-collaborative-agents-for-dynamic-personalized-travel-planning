import os
import googlemaps


class POIApi:
    def __init__(self, api_key=None):
        """Initialize Points of Interest API with Google Maps client"""
        self.api_key = api_key or os.environ.get("MAPS_API_KEY")
        self.gmaps = googlemaps.Client(key=self.api_key)
    
    def get_poi(self, location, radius=1000, keyword=None, type=None, language="en", min_price=None, max_price=None):
        """
        Search for points of interest near a location
        
        Args:
            location: The latitude/longitude or address to search around
            radius: Distance in meters within which to search
            keyword: Term to search for (e.g. "museum", "restaurant")
            type: Restricts results to places matching the specified type (e.g. "tourist_attraction")
            language: The language in which to return results
            min_price: Minimum price level (0-4)
            max_price: Maximum price level (0-4)
            
        Returns:
            Dictionary containing search results
        """
        params = {
            'location': location,
            'radius': radius,
            'language': language
        }
        
        if keyword:
            params['query'] = keyword  # Changed from 'keyword' to 'query'
        if type:
            params['type'] = type
        if min_price is not None:
            params['min_price'] = min_price
        if max_price is not None:
            params['max_price'] = max_price
            
        return self.gmaps.places(**params)
    
    def get_poi_details(self, place_id, language="en", fields=None):
        """
        Get detailed information about a specific place
        
        Args:
            place_id: The Google Place ID
            language: The language in which to return results
            fields: List of fields to include in the response
            
        Returns:
            Dictionary containing place details
        """
        params = {
            'place_id': place_id,
            'language': language
        }
        
        if fields:
            params['fields'] = fields
            
        return self.gmaps.place(**params)
    
    def get_poi_reviews(self, place_id, language="en", max_reviews=5):
        """
        Get reviews for a specific place
        
        Args:
            place_id: The Google Place ID
            language: The language in which to return results
            max_reviews: Maximum number of reviews to return
            
        Returns:
            Dictionary containing place reviews
        """
        result = self.gmaps.place(
            place_id=place_id,
            language=language,
            fields=['review'],
            reviews_sort="newest"
        )
        
        # Limit the number of reviews returned
        if 'result' in result and 'reviews' in result['result']:
            result['result']['reviews'] = result['result']['reviews'][:max_reviews]
            
        return result
    
    def get_nearby_places(self, location, type, radius=1000, language="en"):
        """
        Find places of a specific type near a location
        
        Args:
            location: The latitude/longitude or address to search around
            type: Type of place to search for (e.g. "restaurant", "museum")
            radius: Distance in meters within which to search
            language: The language in which to return results
            
        Returns:
            List of nearby places
        """
        return self.gmaps.places_nearby(
            location=location,
            radius=radius,
            type=type,
            language=language
        )
    
    def get_distance_matrix(self, origins, destinations, mode="driving", language="en", units="metric"):
        """
        Calculate distance and duration between multiple origins and destinations
        
        Args:
            origins: List of addresses or lat/lng values
            destinations: List of addresses or lat/lng values
            mode: Travel mode (driving, walking, bicycling, transit)
            language: The language in which to return results
            units: Unit system for distances (metric, imperial)
            
        Returns:
            Distance matrix results
        """
        return self.gmaps.distance_matrix(
            origins=origins,
            destinations=destinations,
            mode=mode,
            language=language,
            units=units
        )
    
    def get_place_photos(self, photo_reference, max_width=400, max_height=400):
        """
        Get photos for a place
        
        Args:
            photo_reference: Photo reference from a Place Search or Details response
            max_width: Maximum width of the image
            max_height: Maximum height of the image
            
        Returns:
            URL to the photo
        """
        return self.gmaps.places_photo(
            photo_reference=photo_reference,
            max_width=max_width,
            max_height=max_height
        )