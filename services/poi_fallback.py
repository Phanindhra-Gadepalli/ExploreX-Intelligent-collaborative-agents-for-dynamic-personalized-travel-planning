import os
import requests
import time
import math
import hashlib

class POIManager:
    def __init__(self):
        self.geoapify_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
        self.pexels_key = os.environ.get("PEXELS_API_KEY", "61rwGmkV5QiMaa09spVra8Jtu6itcRovaDurdOiKrRgBLgCsAHAKLzyM").strip()
        self._cache = {} # In-memory cache for POIs
        # Read endpoints from env, default to standard ones
        endpoints_env = os.environ.get("OVERPASS_ENDPOINTS", "")
        if endpoints_env:
            self.overpass_endpoints = [e.strip() for e in endpoints_env.split(",") if e.strip()]
        else:
            self.overpass_endpoints = [
                "https://overpass-api.de/api/interpreter",
                "https://lz4.overpass-api.de/api/interpreter",
                "https://z.overpass-api.de/api/interpreter"
            ]

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

    def get_attractions(self, lat: float, lng: float, radius: int = 10000, hobbies: str = None, poi_type: str = "tourist_attraction", retrieved_rag_pois=None):
        print(f"[POI_MANAGER] Fetching attractions for lat={lat}, lng={lng}, hobbies={hobbies}")
        cache_key = self._generate_cache_key(lat, lng, radius, hobbies)
        
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < 3600:
                print(f"[POI_MANAGER] Cache hit for {cache_key}. Returning {len(cache_entry['data'])} POIs.")
                return cache_entry['data']
        
        candidates = []
        
        # 1. Try Overpass
        try:
            osm_results = self._fetch_osm_attractions(lat, lng, radius, hobbies)
            if osm_results:
                print(f"[POI_MANAGER] OSM returned {len(osm_results)} POIs.")
                candidates.extend(osm_results)
        except Exception as e:
            print(f"[POI_MANAGER][OSM] Failed: {e}")
            
        # 2. Try Geoapify as fallback
        if not candidates and self.geoapify_key:
            try:
                geo_results = self._fetch_geoapify_attractions(lat, lng, radius, hobbies)
                if geo_results:
                    print(f"[POI_MANAGER] Geoapify returned {len(geo_results)} POIs.")
                    candidates.extend(geo_results)
            except Exception as e:
                print(f"[POI_MANAGER][Geoapify] Failed: {e}")

        # 3. Fallback to RAG POIs if provided
        if not candidates and retrieved_rag_pois:
            print(f"[POI_MANAGER] Using {len(retrieved_rag_pois)} RAG POIs as fallback.")
            # Normalize RAG POIs to match schema
            for p in retrieved_rag_pois:
                if 'location' in p and not 'latitude' in p:
                    p['latitude'] = p['location'].get('lat')
                    p['longitude'] = p['location'].get('lng')
            candidates.extend(retrieved_rag_pois)
            
        if not candidates:
            print("[POI_MANAGER] All live POI providers failed to return attractions.")
            return []
            
        final_pois = self._deduplicate_pois(candidates)
        print(f"[POI_MANAGER] Merged & Deduplicated: {len(final_pois)} total final POIs.")
        
        self._cache[cache_key] = {
            'timestamp': time.time(),
            'data': final_pois
        }
        
        return final_pois

    def _generate_cache_key(self, lat, lng, radius, hobbies):
        raw = f"{lat:.4f},{lng:.4f},{radius},{hobbies}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _deduplicate_pois(self, pois):
        def calculate_distance(lat1, lon1, lat2, lon2):
            R = 6371  # Radius of the earth in km
            dLat = math.radians(lat2 - lat1)
            dLon = math.radians(lon2 - lon1)
            a = math.sin(dLat/2) * math.sin(dLat/2) + \
                math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
                math.sin(dLon/2) * math.sin(dLon/2)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            return R * c
            
        def poi_score(poi):
            score = 0
            if poi.get('metadata', {}).get('wikidata_id'):
                score += 100
            name = poi.get('name', '').lower()
            if 'viewpoint' in name or 'point de vue' in name:
                score -= 30
            if 'gate' in name or 'entrance' in name or 'parking' in name:
                score -= 50
            score -= (len(name) * 0.1)
            if poi.get('source') == 'osm':
                score += 20
            if poi.get('source') == 'geoapify':
                score += 10
            return score
            
        grouped_pois = []
        for poi in pois:
            name = poi.get('name', '').strip().lower()
            if not name:
                continue
            try:
                lat = float(poi.get('latitude', 0))
                lng = float(poi.get('longitude', 0))
            except (ValueError, TypeError):
                continue
            if lat == 0 and lng == 0:
                continue
                
            matched_group_idx = -1
            for i, group in enumerate(grouped_pois):
                for seen_poi in group:
                    seen_name = seen_poi.get('name', '').strip().lower()
                    try:
                        seen_lat = float(seen_poi.get('latitude', 0))
                        seen_lng = float(seen_poi.get('longitude', 0))
                    except (ValueError, TypeError):
                        continue
                    name_match = (name in seen_name and len(name) > 5) or (seen_name in name and len(seen_name) > 5) or (name == seen_name)
                    dist = calculate_distance(lat, lng, seen_lat, seen_lng)
                    dist_match = dist < 2.0
                    
                    if name_match and dist_match:
                        matched_group_idx = i
                        break
                if matched_group_idx != -1:
                    break
                    
            if matched_group_idx != -1:
                grouped_pois[matched_group_idx].append(poi)
            else:
                grouped_pois.append([poi])
                
        deduplicated = []
        for group in grouped_pois:
            group.sort(key=lambda p: poi_score(p), reverse=True)
            best_poi = group[0]
            for other_poi in group[1:]:
                if not best_poi.get('metadata', {}).get('wikidata_id') and other_poi.get('metadata', {}).get('wikidata_id'):
                    if 'metadata' not in best_poi:
                        best_poi['metadata'] = {}
                    best_poi['metadata']['wikidata_id'] = other_poi['metadata']['wikidata_id']
                if not best_poi.get('address') and other_poi.get('address'):
                    best_poi['address'] = other_poi['address']
            
            # Reconstruct location field for backward compatibility
            best_poi['location'] = {'lat': best_poi['latitude'], 'lng': best_poi['longitude']}
            deduplicated.append(best_poi)
            
        return deduplicated

    def get_accommodations(self, lat: float, lng: float, budget: str, number: int = 4, radius: int = 15000):
        print(f"[POI_MANAGER] Fetching accommodations for lat={lat}, lng={lng}, budget={budget}")
        try:
            results = self._fetch_osm_accommodations(lat, lng, radius, budget, number)
            if results:
                return results
        except Exception as e:
            print(f"[POI_MANAGER][OSM] Accommodations failed: {e}")

        if self.geoapify_key:
            try:
                results = self._fetch_geoapify_accommodations(lat, lng, radius, budget, number)
                if results:
                    return results
            except Exception:
                pass
        return []

    def get_restaurants(self, lat: float, lng: float, radius: int = 5000, number: int = 5):
        print(f"[POI_MANAGER] Fetching restaurants for lat={lat}, lng={lng}")
        try:
            results = self._fetch_osm_restaurants(lat, lng, radius, number)
            if results:
                return results
        except Exception as e:
            print(f"[POI_MANAGER][OSM] Restaurants failed: {e}")

        if self.geoapify_key:
            try:
                results = self._fetch_geoapify_restaurants(lat, lng, radius, number)
                if results:
                    return results
            except Exception:
                pass
        return []

    # -------------------------------------------------------------------------
    # OVERPASS API
    # -------------------------------------------------------------------------
    def _execute_overpass_query(self, query: str):
        headers = {
            "User-Agent": "ExploreX-Travel-App/1.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        last_exception = None
        max_retries = 3
        
        for attempt in range(max_retries):
            endpoint = self.overpass_endpoints[attempt % len(self.overpass_endpoints)]
            try:
                resp = requests.post(
                    endpoint, 
                    data={"data": query}, 
                    headers=headers, 
                    timeout=15
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    print(f"[POI_MANAGER][OSM] Recoverable error {resp.status_code} on {endpoint}. Backing off.")
                    time.sleep(2 * (attempt + 1))
                    continue
                    
                resp.raise_for_status()
                return resp.json().get('elements', [])
            except requests.exceptions.Timeout as e:
                print(f"[POI_MANAGER][OSM] Timeout on {endpoint}: {e}")
                last_exception = e
                time.sleep(2 * (attempt + 1))
            except Exception as e:
                last_exception = e
                print(f"[POI_MANAGER][OSM] Attempt {attempt+1} failed on {endpoint}: {e}")
                time.sleep(2 * (attempt + 1)) # Exponential backoff
        
        raise Exception(f"All Overpass endpoints exhausted. Last error: {last_exception}")

    def _get_category_filters(self, hobbies, radius, lat, lng):
        filters = []
        h = hobbies.lower() if hobbies else ""
        
        if "history" in h or "architecture" in h or "heritage" in h:
            filters.append(f'nwr["historic"](around:{radius},{lat},{lng});')
        if "nature" in h or "adventure" in h:
            filters.append(f'nwr["leisure"~"park|nature_reserve"](around:{radius},{lat},{lng});')
            filters.append(f'nwr["natural"~"waterfall|beach|peak"](around:{radius},{lat},{lng});')
        if "spiritual" in h or "temple" in h or "religion" in h:
            filters.append(f'nwr["amenity"~"place_of_worship"](around:{radius},{lat},{lng});')
        if "art" in h or "culture" in h or "museum" in h:
            filters.append(f'nwr["tourism"~"museum|gallery"](around:{radius},{lat},{lng});')
            
        if not filters or "shopping" in h or "food" in h:
            filters.append(f'nwr["tourism"~"attraction|museum|viewpoint|gallery|theme_park"](around:{radius},{lat},{lng});')
            
        return "".join(filters)

    def _fetch_osm_attractions(self, lat, lng, radius, hobbies):
        cat_filters = self._get_category_filters(hobbies, radius, lat, lng)
        query = f"[out:json][timeout:15];({cat_filters});out 200 tags center;"
        
        elements = self._execute_overpass_query(query)
        normalized = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            if tags.get('amenity') in ['bank', 'atm', 'hospital', 'clinic', 'dentist', 'pharmacy', 'police', 'post_office', 'waste_basket', 'vending_machine', 'parking', 'fuel', 'taxi']:
                continue
            if tags.get('shop') or tags.get('office') or tags.get('highway'):
                continue
            
            el_type = tags.get('tourism') or tags.get('historic') or tags.get('amenity') or tags.get('leisure') or "attraction"
            
            addr_parts = [tags.get('addr:housenumber', ''), tags.get('addr:street', ''), tags.get('addr:city', ''), tags.get('addr:postcode', '')]
            address = ' '.join(p for p in addr_parts if p).strip() or tags.get('addr:full', '') or None

            osm_description = tags.get('description') or tags.get('description:en') or None
            
            latitude = el.get('lat') or el.get('center', {}).get('lat')
            longitude = el.get('lon') or el.get('center', {}).get('lon')

            poi = {
                'id': f"osm_{el.get('id')}",
                'source': 'osm',
                'name': name,
                'latitude': latitude,
                'longitude': longitude,
                'category': el_type,
                'subcategory': tags.get(el_type, ''),
                'rating': 4.0,
                'user_ratings_total': 50,
                'price_level': 1,
                'description': osm_description,
                'opening_hours': tags.get('opening_hours'),
                'entry_fee': tags.get('fee'),
                'duration': 2,
                'address': address,
                'website': tags.get('website'),
                'metadata': {
                    'wikidata_id': tags.get('wikidata'),
                    'wikipedia': tags.get('wikipedia'),
                    'osm_tags': {k: v for k, v in tags.items() if k not in ['name', 'name:en']}
                },
                'verified': True,
                'image_url': self._fetch_pexels_image(name, fallback_query=el_type)
            }
            poi = {k: v for k, v in poi.items() if v is not None}
            normalized.append(poi)
            
        return normalized

    def _fetch_osm_accommodations(self, lat, lng, radius, budget, number):
        acc_type = "hostel" if budget == 'low' else "hotel"
        query = f"[out:json][timeout:15];(node[\"tourism\"~\"{acc_type}|guest_house\"](around:{radius},{lat},{lng}););out 15 tags center;"
        
        elements = self._execute_overpass_query(query)
        accommodations = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            latitude = el.get('lat') or el.get('center', {}).get('lat')
            longitude = el.get('lon') or el.get('center', {}).get('lon')
            
            accommodations.append({
                'id': f"osm_{el.get('id')}",
                'source': 'osm',
                'name': name,
                'latitude': latitude,
                'longitude': longitude,
                'category': 'accommodation',
                'subcategory': acc_type,
                'rating': 4.0,
                'user_ratings_total': 50,
                'price_level': 1 if budget == 'low' else 2,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Local Accommodation",
                'website': tags.get('website', ''),
                'image_url': self._fetch_pexels_image(name),
                'verified': True
            })
            
        return accommodations[:number]

    # -------------------------------------------------------------------------
    # GEOAPIFY API
    # -------------------------------------------------------------------------
    def _fetch_geoapify_attractions(self, lat, lng, radius, hobbies):
        categories = "tourism.sights,entertainment,heritage,natural"
        url = "https://api.geoapify.com/v2/places"
        params = {
            "categories": categories,
            "filter": f"circle:{lng},{lat},{radius}",
            "limit": 50,
            "apiKey": self.geoapify_key
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
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

            latitude = props.get('lat')
            longitude = props.get('lon')

            poi = {
                'id': props.get('place_id', str(latitude)),
                'source': 'geoapify',
                'name': props.get('name'),
                'latitude': latitude,
                'longitude': longitude,
                'category': primary_cat,
                'subcategory': cats[1] if len(cats) > 1 else '',
                'rating': 4.0,
                'user_ratings_total': 100,
                'price_level': 2,
                'address': props.get('formatted'),
                'website': props.get('website'),
                'opening_hours': props.get('opening_hours'),
                'duration': 2,
                'metadata': {},
                'verified': True,
                'image_url': self._fetch_pexels_image(props.get('name'), fallback_query=primary_cat.replace('_', ' ')),
            }
            
            wikidata_id = None
            if 'wiki_and_media' in props and 'wikidata' in props['wiki_and_media']:
                wikidata_id = props['wiki_and_media']['wikidata']
            elif 'datasource' in props and 'raw' in props['datasource'] and 'wikidata' in props['datasource']['raw']:
                wikidata_id = props['datasource']['raw']['wikidata']
            if wikidata_id:
                poi['metadata']['wikidata_id'] = wikidata_id
                
            geo_desc = props.get('description', '')
            if geo_desc and len(geo_desc) > 40:
                poi['description'] = geo_desc

            poi = {k: v for k, v in poi.items() if v is not None}
            normalized.append(poi)
            
        return normalized

    def _fetch_geoapify_accommodations(self, lat, lng, radius, budget, number):
        url = "https://api.geoapify.com/v2/places"
        params = {
            "categories": "accommodation",
            "filter": f"circle:{lng},{lat},{radius}",
            "limit": max(10, number),
            "apiKey": self.geoapify_key
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
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
                'source': 'geoapify',
                'name': props.get('name'),
                'latitude': props.get('lat'),
                'longitude': props.get('lon'),
                'category': 'accommodation',
                'rating': 4.0,
                'user_ratings_total': 100,
                'price_level': 1 if budget == 'low' else (4 if budget == 'high' else 2),
                'address': props.get('formatted', ''),
                'website': props.get('website', ''),
                'image_url': self._fetch_pexels_image(props.get('name')),
                'verified': True
            })
            
        return accommodations[:number]

    def _fetch_osm_restaurants(self, lat, lng, radius, number):
        query = f'[out:json][timeout:15];nwr["amenity"~"restaurant|cafe|fast_food|food_court"](around:{radius},{lat},{lng});out {number * 3} tags center;'
        elements = self._execute_overpass_query(query)
        results = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            cuisine = tags.get('cuisine', 'Local')
            results.append({
                'id': f"osm_rest_{el.get('id')}",
                'source': 'osm',
                'name': name,
                'latitude': el.get('lat') or el.get('center', {}).get('lat'),
                'longitude': el.get('lon') or el.get('center', {}).get('lon'),
                'category': 'Food',
                'description': f"{cuisine.title()} Restaurant",
                'rating': 4.0,
                'price_level': 2,
                'is_meal': True
            })
            if len(results) >= number: break
        return results

    def _fetch_geoapify_restaurants(self, lat, lng, radius, number):
        url = f"https://api.geoapify.com/v2/places?categories=catering.restaurant,catering.cafe&filter=circle:{lng},{lat},{radius}&limit={number * 3}&apiKey={self.geoapify_key}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            features = resp.json().get('features', [])
            results = []
            for feat in features:
                props = feat.get('properties', {})
                name = props.get('name')
                if not name: continue
                
                results.append({
                    'id': f"geoapify_rest_{props.get('place_id')}",
                    'source': 'geoapify',
                    'name': name,
                    'latitude': props.get('lat'),
                    'longitude': props.get('lon'),
                    'category': 'Food',
                    'description': props.get('categories', ['Restaurant'])[0].replace('catering.', '').title(),
                    'rating': 4.0,
                    'price_level': 2,
                    'address': props.get('formatted'),
                    'is_meal': True
                })
                if len(results) >= number: break
            return results
        except Exception:
            return []
