"""
Multi-provider POI (Point of Interest) Fallback Architecture.
Provides a cascade of providers to ensure attractions and accommodations 
are found using 100% free-tier APIs.

Cascade: Geoapify (if key available) -> OpenStreetMap (Overpass)
"""

import os
import requests
import time

class POIManager:
    def __init__(self):
        self.geoapify_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
        self.pexels_key = os.environ.get("PEXELS_API_KEY", "61rwGmkV5QiMaa09spVra8Jtu6itcRovaDurdOiKrRgBLgCsAHAKLzyM").strip()

    from functools import lru_cache

    @lru_cache(maxsize=1024)
    def _fetch_pexels_image(self, query, fallback_query=None):
        if not hasattr(self, 'pexels_key') or not self.pexels_key:
            return None
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": self.pexels_key}
        
        for q in [query, fallback_query]:
            if not q: continue
            params = {"query": q, "per_page": 1}
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('photos'):
                        return data['photos'][0]['src']['medium']
            except Exception:
                pass
        return None
    def get_attractions(self, lat: float, lng: float, radius: int = 10000, hobbies: str = None, poi_type: str = "tourist_attraction"):
        """
        Attempts to fetch attractions using the provider cascade.
        Returns a list of normalized POI dictionaries.
        """
        print(f"[POI_MANAGER] Fetching attractions for lat={lat}, lng={lng}")
        
        # 1. Try OpenStreetMap (Overpass) FIRST
        try:
            results = self._fetch_osm_attractions(lat, lng, radius, hobbies)
            if results:
                print(f"[POI_MANAGER] Overpass OSM succeeded, returning {len(results)} POIs.")
                return results
        except Exception as e:
            print(f"[POI_MANAGER][OSM] Failed: {e}")

        # 2. Try Geoapify (if enabled) as fallback
        if self.geoapify_key:
            try:
                results = self._fetch_geoapify_attractions(lat, lng, radius, hobbies)
                if results:
                    print(f"[POI_MANAGER] Geoapify succeeded, returning {len(results)} POIs.")
                    return results
            except Exception:
                pass # Silenced to avoid console clutter (400/401)
        else:
            print("[POI_MANAGER] Skipping Geoapify (no API key).")

        # All providers failed
        print("[POI_MANAGER] All live POI providers failed to return attractions.")
        return []

    def get_accommodations(self, lat: float, lng: float, budget: str, number: int = 4, radius: int = 15000):
        """
        Attempts to fetch accommodations using the provider cascade.
        """
        print(f"[POI_MANAGER] Fetching accommodations for lat={lat}, lng={lng}, budget={budget}")
        
        # 1. Try OSM Overpass FIRST
        try:
            results = self._fetch_osm_accommodations(lat, lng, radius, budget, number)
            if results:
                return results
        except Exception as e:
            print(f"[POI_MANAGER][OSM] Accommodations failed: {e}")

        # 2. Try Geoapify as fallback
        if self.geoapify_key:
            try:
                results = self._fetch_geoapify_accommodations(lat, lng, radius, budget, number)
                if results:
                    return results
            except Exception:
                pass # Silenced

        return []

    # -------------------------------------------------------------------------
    # GEOAPIFY
    # -------------------------------------------------------------------------

    def _fetch_geoapify_attractions(self, lat, lng, radius, hobbies):
        categories = "tourism.attraction,natural,historic,entertainment"
        url = f"https://api.geoapify.com/v2/places?categories={categories}&filter=circle:{lng},{lat},{radius}&limit=30&apiKey={self.geoapify_key}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            safe_msg = str(e).replace(self.geoapify_key, "****") if self.geoapify_key else str(e)
            raise Exception(f"Geoapify Request Failed: {safe_msg}")
        features = resp.json().get('features', [])
        
        normalized = []
        for f in features:
            props = f.get('properties', {})
            if not props.get('name'): continue
            
            cats = props.get('categories', [])
            primary_cat = cats[0] if cats else "tourist_attraction"
            
            normalized.append({
                'id': props.get('place_id', str(props.get('lat'))),
                'name': props.get('name'),
                'rating': 4.0, # Default since Geoapify doesn't reliably provide rating
                'user_ratings_total': 100,
                'price_level': 2,
                'address': props.get('formatted', ''),
                'location': {'lat': props.get('lat'), 'lng': props.get('lon')},
                'category': primary_cat,
                'types': cats,
                'website': props.get('website', ''),
                'description': props.get('description', f"A beautiful {primary_cat} located in {props.get('city', 'the area')}."),
                'image_url': self._fetch_pexels_image(props.get('name'), fallback_query=primary_cat.replace('_', ' ')),
                'source': 'geoapify'
            })
            
        return normalized

    def _fetch_geoapify_accommodations(self, lat, lng, radius, budget, number):
        url = f"https://api.geoapify.com/v2/places?categories=accommodation&filter=circle:{lng},{lat},{radius}&limit={max(10, number)}&apiKey={self.geoapify_key}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            safe_msg = str(e).replace(self.geoapify_key, "****") if self.geoapify_key else str(e)
            raise Exception(f"Geoapify Request Failed: {safe_msg}")
        features = resp.json().get('features', [])
        
        accommodations = []
        for f in features:
            props = f.get('properties', {})
            if not props.get('name'): continue
            
            accommodations.append({
                'id': props.get('place_id', str(props.get('lat'))),
                'name': props.get('name'),
                'rating': 4.0,
                'user_ratings_total': 100,
                'price_level': 1 if budget == 'low' else (4 if budget == 'high' else 2),
                'address': props.get('formatted', ''),
                'website': props.get('website', ''),
                'image_url': self._fetch_pexels_image(props.get('name')),
                'type': 'accommodation',
                'source': 'geoapify'
            })
            
        return accommodations[:number]

    # -------------------------------------------------------------------------
    # OPENSTREETMAP (OVERPASS)
    # -------------------------------------------------------------------------

    def _execute_overpass_query(self, query: str):
        """Execute query against Overpass API with fallbacks and robust headers."""
        endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://lz4.overpass-api.de/api/interpreter"
        ]
        
        headers = {
            "User-Agent": "ExploreX-Travel-App/1.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        last_exception = None
        for endpoint in endpoints:
            try:
                resp = requests.post(
                    endpoint, 
                    data={"data": query}, 
                    headers=headers, 
                    timeout=8
                )
                resp.raise_for_status()
                return resp.json().get('elements', [])
            except Exception as e:
                last_exception = e
                print(f"[POI_MANAGER][OSM] Failed endpoint {endpoint}: {e}")
                time.sleep(1) # Small delay before trying next fallback
                
        raise Exception(f"All Overpass endpoints failed. Last error: {last_exception}")

    def _fetch_osm_attractions(self, lat, lng, radius, hobbies):
        query = f"""
        [out:json][timeout:15];
        (
          node["tourism"~"attraction|museum|viewpoint|gallery"](around:{radius},{lat},{lng});
          node["historic"](around:{radius},{lat},{lng});
          node["leisure"~"park|nature_reserve"](around:{radius},{lat},{lng});
          node["amenity"~"place_of_worship"](around:{radius},{lat},{lng});
        );
        out 30 tags center;
        """
        
        elements = self._execute_overpass_query(query)
        
        normalized = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            el_type = tags.get('tourism') or tags.get('historic') or tags.get('amenity') or tags.get('leisure') or "attraction"
            
            normalized.append({
                'id': f"osm_{el.get('id')}",
                'name': name,
                'rating': 4.0,
                'user_ratings_total': 50,
                'price_level': 1,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Local Attraction",
                'location': {'lat': el.get('lat') or el.get('center', {}).get('lat'), 'lng': el.get('lon') or el.get('center', {}).get('lon')},
                'category': el_type,
                'types': [el_type],
                'website': tags.get('website', ''),
                'description': tags.get('description', f"A renowned {el_type} in the area, popular among tourists and locals."),
                'image_url': self._fetch_pexels_image(name, fallback_query=el_type),
                'source': 'osm'
            })
            
        return normalized

    def _fetch_osm_accommodations(self, lat, lng, radius, budget, number):
        acc_type = "hostel" if budget == 'low' else "hotel"
        query = f"""
        [out:json][timeout:15];
        (
          node["tourism"~"{acc_type}|guest_house"](around:{radius},{lat},{lng});
        );
        out 15 tags center;
        """
        
        elements = self._execute_overpass_query(query)
        
        accommodations = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            accommodations.append({
                'id': f"osm_{el.get('id')}",
                'name': name,
                'rating': 4.0,
                'user_ratings_total': 50,
                'price_level': 1 if budget == 'low' else 2,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Local Accommodation",
                'website': tags.get('website', ''),
                'image_url': self._fetch_pexels_image(name),
                'type': 'accommodation',
                'source': 'osm'
            })
            
        return accommodations[:number]
