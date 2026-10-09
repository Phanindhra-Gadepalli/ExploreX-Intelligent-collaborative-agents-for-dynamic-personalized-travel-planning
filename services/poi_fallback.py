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
        
        # 1. Try Geoapify first as it is often more stable
        if self.geoapify_key:
            try:
                geo_results = self._fetch_geoapify_attractions(lat, lng, radius, hobbies)
                if geo_results:
                    print(f"[PROVIDER][GEOAPIFY][FALLBACK] Geoapify returned {len(geo_results)} POIs.")
                    candidates.extend(geo_results)
            except Exception as e:
                print(f"[PROVIDER][GEOAPIFY][WARN] Failed: {e}")

        # 2. Try Overpass if Geoapify didn't return enough (e.g. less than 10)
        if len(candidates) < 10:
            try:
                osm_results = self._fetch_osm_attractions(lat, lng, radius, hobbies)
                if osm_results:
                    print(f"[PROVIDER][OVERPASS][WARN] OSM returned {len(osm_results)} POIs.")
                    candidates.extend(osm_results)
            except Exception as e:
                print(f"[PROVIDER][OVERPASS][WARN] Failed: {e}")

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
                    
                    id_match = (poi.get('id') and poi.get('id') == seen_poi.get('id'))
                    name_match = (name in seen_name and len(name) > 5) or (seen_name in name and len(seen_name) > 5) or (name == seen_name)
                    dist = calculate_distance(lat, lng, seen_lat, seen_lng)
                    dist_match = dist < 0.5  # 500 meters for similar names
                    duplicate_loc = dist < 0.01 # 10 meters for duplicate locations regardless of name
                    
                    if id_match or (name_match and dist_match) or duplicate_loc:
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
        cache_key = self._generate_cache_key(lat, lng, radius, f"accom_{budget}")
        
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < 3600 * 24:
                print(f"[POI_MANAGER] Cache hit for {cache_key}.")
                return cache_entry['data'][:number]

        results = []
        try:
            results = self._fetch_osm_accommodations(lat, lng, radius, budget, number)
        except Exception as e:
            print(f"[PROVIDER][OVERPASS][WARN] Accommodations failed: {e}")

        if not results and self.geoapify_key:
            try:
                results = self._fetch_geoapify_accommodations(lat, lng, radius, budget, number)
            except Exception as e:
                print(f"[PROVIDER][GEOAPIFY][WARN] Accommodations failed: {e}")
                
        if results:
            self._cache[cache_key] = {
                'timestamp': time.time(),
                'data': results
            }
            return results[:number]

        if cache_key in self._cache:
            print(f"[PROVIDER][ACCOMMODATION][DEGRADED] Using expired cache data.")
            return self._cache[cache_key]['data'][:number]

        print(f"[PROVIDER][ACCOMMODATION][DEGRADED] All accommodation providers failed. Returning safe empty list.")
        return []

    def get_restaurants(self, lat: float, lng: float, radius: int = 5000, number: int = 20):
        print(f"[POI_MANAGER] Fetching restaurants for lat={lat}, lng={lng}")
        cache_key = self._generate_cache_key(lat, lng, radius, "restaurants")
        
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < 3600 * 24:
                print(f"[POI_MANAGER] Cache hit for {cache_key}.")
                return cache_entry['data'][:number]
                
        all_results = []
        try:
            osm_results = self._fetch_osm_restaurants(lat, lng, radius, number)
            if osm_results:
                all_results.extend(osm_results)
        except Exception as e:
            print(f"[PROVIDER][OVERPASS][WARN] Restaurants failed: {e}")

        if len(all_results) < number and self.geoapify_key:
            try:
                geo_results = self._fetch_geoapify_restaurants(lat, lng, radius, number)
                if geo_results:
                    all_results.extend(geo_results)
            except Exception as e:
                print(f"[PROVIDER][GEOAPIFY][WARN] Restaurants failed: {e}")
        
        # Deduplicate
        seen = set()
        deduped = []
        for r in all_results:
            name_val = r.get('name', '')
            name_key = str(name_val).lower().strip() if name_val is not None else ""
            if name_key and name_key not in seen:
                seen.add(name_key)
                deduped.append(r)
                if len(deduped) >= number: break
                
        if deduped:
            self._cache[cache_key] = {
                'timestamp': time.time(),
                'data': deduped
            }
            return deduped

        if cache_key in self._cache:
            print(f"[PROVIDER][RESTAURANT][DEGRADED] Using expired cache data.")
            return self._cache[cache_key]['data'][:number]
            
        print("[PROVIDER][RESTAURANT][DEGRADED] All providers failed. Returning safe empty list.")
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
        
        if not hasattr(self, 'overpass_cooldowns'):
            self.overpass_cooldowns = {}
            
        now = time.time()
        available_endpoints = [e for e in self.overpass_endpoints if now > self.overpass_cooldowns.get(e, 0)]
        
        if not available_endpoints:
            print("[PROVIDER][OVERPASS][WARN] All endpoints in cooldown. Failing fast.")
            return []
            
        for attempt in range(max_retries):
            endpoint = available_endpoints[attempt % len(available_endpoints)]
            try:
                resp = requests.post(
                    endpoint, 
                    data={"data": query}, 
                    headers=headers, 
                    timeout=10
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    print(f"[PROVIDER][OVERPASS][WARN] Recoverable error {resp.status_code} on {endpoint}. Backing off.")
                    self.overpass_cooldowns[endpoint] = time.time() + 30
                    time.sleep(2 ** attempt)
                    continue
                    
                resp.raise_for_status()
                return resp.json().get('elements', [])
            except requests.exceptions.Timeout as e:
                print(f"[PROVIDER][OVERPASS][WARN] Timeout on {endpoint}: {e}")
                self.overpass_cooldowns[endpoint] = time.time() + 60
                last_exception = e
                time.sleep(2 ** attempt)
            except Exception as e:
                last_exception = e
                print(f"[PROVIDER][OVERPASS][WARN] Attempt {attempt+1} failed on {endpoint}: {e}")
                self.overpass_cooldowns[endpoint] = time.time() + 15
                time.sleep(2 ** attempt)
        
        print(f"[PROVIDER][OVERPASS][WARN] All Overpass attempts exhausted. Returning safe empty list.")
        return []

    def _get_category_filters(self, hobbies, radius, lat, lng):
        import math
        earth_radius = 6371000
        lat_delta = (radius / earth_radius) * (180 / math.pi)
        lng_delta = (radius / earth_radius) * (180 / math.pi) / math.cos(lat * math.pi / 180)
        s = lat - lat_delta
        w = lng - lng_delta
        n = lat + lat_delta
        e = lng + lng_delta
        bbox = f"{s},{w},{n},{e}"
        
        filters = []
        h = hobbies.lower() if hobbies else ""
        
        if "history" in h or "architecture" in h or "heritage" in h:
            filters.append(f'nwr["historic"]({bbox});')
        if "nature" in h or "adventure" in h:
            filters.append(f'nwr["leisure"~"park|nature_reserve"]({bbox});')
            filters.append(f'nwr["natural"~"waterfall|beach|peak"]({bbox});')
        if "spiritual" in h or "temple" in h or "religion" in h:
            filters.append(f'nwr["amenity"~"place_of_worship"]({bbox});')
        if "art" in h or "culture" in h or "museum" in h:
            filters.append(f'nwr["tourism"~"museum|gallery"]({bbox});')
            
        if not filters or "shopping" in h or "food" in h:
            filters.append(f'nwr["tourism"~"attraction|museum|viewpoint|gallery|theme_park"]({bbox});')
            
        return "".join(filters)

    def _fetch_osm_attractions(self, lat, lng, radius, hobbies):
        cat_filters = self._get_category_filters(hobbies, min(radius, 15000), lat, lng)
        # Using [out:json][timeout:15] and bounding box for elements, limited via Python logic rather than broken out limits
        query = f"[out:json][timeout:15];({cat_filters});out center;"
        
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
                'image_url': None
            }
            poi = {k: v for k, v in poi.items() if v is not None}
            normalized.append(poi)
            
        return normalized

    def _fetch_osm_accommodations(self, lat, lng, radius, budget, number):
        import math
        earth_radius = 6371000
        radius = min(radius, 15000)
        lat_delta = (radius / earth_radius) * (180 / math.pi)
        lng_delta = (radius / earth_radius) * (180 / math.pi) / math.cos(lat * math.pi / 180)
        s = lat - lat_delta
        w = lng - lng_delta
        n = lat + lat_delta
        e = lng + lng_delta
        bbox = f"{s},{w},{n},{e}"
        
        if budget == 'budget':
            acc_type = "hostel|guest_house|motel"
        elif budget == 'luxury':
            acc_type = "resort|hotel"
        else:
            acc_type = "hotel|apartment"

        query = f'[out:json][timeout:15];(node["tourism"~"{acc_type}"]({bbox}););out center;'
        
        elements = self._execute_overpass_query(query)
        accommodations = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            latitude = el.get('lat') or el.get('center', {}).get('lat')
            longitude = el.get('lon') or el.get('center', {}).get('lon')
            
            amenities = []
            if "internet_access" in tags: amenities.append("Wi-Fi")
            if "wheelchair" in tags: amenities.append("Wheelchair Accessible")
            if "swimming_pool" in tags: amenities.append("Pool")
            if not amenities: amenities = ["Wi-Fi", "Air Conditioning"] # fallback basics

            accommodations.append({
                'id': f"osm_{el.get('id')}",
                'name': name,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Local Accommodation",
                'latitude': latitude,
                'longitude': longitude,
                'rating': 4.0 if budget == 'budget' else (4.5 if budget == 'medium' else 4.8),
                'category': 'Accommodation',
                'price_per_night': None,
                'currency': 'USD',
                'price_status': 'unavailable',
                'amenities': amenities,
                'image_url': self._fetch_pexels_image(f"{name} {tags.get('tourism', 'hotel')}"),
                'booking_url': tags.get('website', ''),
                'budget_tier': budget
            })
            if len(accommodations) >= number:
                break
            
        return accommodations

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
                'image_url': None,
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
        
        if budget == 'budget':
            cat = "accommodation.hostel,accommodation.motel,accommodation.guest_house"
        elif budget == 'luxury':
            cat = "accommodation.hotel"
        else:
            cat = "accommodation.hotel,accommodation.apartment"

        params = {
            "categories": cat,
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
                'name': props.get('name'),
                'address': props.get('formatted', 'Local Accommodation'),
                'latitude': props.get('lat'),
                'longitude': props.get('lon'),
                'rating': 4.0 if budget == 'budget' else (4.5 if budget == 'medium' else 4.8),
                'category': 'Accommodation',
                'price_per_night': None,
                'currency': 'USD',
                'price_status': 'unavailable',
                'amenities': ["Wi-Fi", "Air Conditioning", "Breakfast Available"],
                'image_url': self._fetch_pexels_image(f"{props.get('name')} hotel"),
                'booking_url': props.get('website', ''),
                'budget_tier': budget
            })
            if len(accommodations) >= number:
                break
            
        return accommodations

    def _fetch_osm_restaurants(self, lat, lng, radius, number):
        import math
        earth_radius = 6371000
        radius = min(radius, 10000)
        lat_delta = (radius / earth_radius) * (180 / math.pi)
        lng_delta = (radius / earth_radius) * (180 / math.pi) / math.cos(lat * math.pi / 180)
        s = lat - lat_delta
        w = lng - lng_delta
        n = lat + lat_delta
        e = lng + lng_delta
        bbox = f"{s},{w},{n},{e}"
        
        query = f'[out:json][timeout:15];nwr["amenity"~"restaurant|cafe|fast_food|food_court"]({bbox});out center;'
        elements = self._execute_overpass_query(query)
        results = []
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name') or tags.get('name:en')
            if not name: continue
            
            cuisine = tags.get('cuisine', 'Local').replace('_', ' ').title()
            amenity = tags.get('amenity', 'restaurant')
            
            meal_types = []
            if 'cafe' in amenity:
                meal_types.extend(['breakfast', 'cafe'])
            elif 'fast_food' in amenity or 'street_food' in cuisine.lower():
                meal_types.extend(['lunch', 'street_food'])
            else:
                meal_types.extend(['lunch', 'dinner'])
                
            results.append({
                'id': f"osm_rest_{el.get('id')}",
                'name': name,
                'cuisine': cuisine,
                'category': 'Food',
                'meal_types': meal_types,
                'address': f"{tags.get('addr:street', '')} {tags.get('addr:city', '')}".strip() or "Local Restaurant",
                'latitude': el.get('lat') or el.get('center', {}).get('lat'),
                'longitude': el.get('lon') or el.get('center', {}).get('lon'),
                'rating': 4.3,
                'price_level': 2,
                'image_url': self._fetch_pexels_image(f"{name} food {cuisine}"),
                'description': f"{cuisine} {amenity.replace('_', ' ').title()}",
                'source': 'osm'
            })
            if len(results) >= number: break
        return results

    def _fetch_geoapify_restaurants(self, lat, lng, radius, number):
        url = f"https://api.geoapify.com/v2/places?categories=catering.restaurant,catering.cafe,catering.fast_food&filter=circle:{lng},{lat},{radius}&limit={number * 3}&apiKey={self.geoapify_key}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            features = resp.json().get('features', [])
            results = []
            for feat in features:
                props = feat.get('properties', {})
                name = props.get('name')
                if not name: continue
                
                cats = props.get('categories', [])
                cat_str = ' '.join(cats).lower()
                
                meal_types = []
                if 'cafe' in cat_str or 'coffee' in cat_str:
                    meal_types.extend(['breakfast', 'cafe'])
                elif 'fast_food' in cat_str or 'street_food' in cat_str:
                    meal_types.extend(['lunch', 'street_food'])
                else:
                    meal_types.extend(['lunch', 'dinner'])
                
                cuisine = props.get('cuisine', 'Local').replace('_', ' ').title()
                
                results.append({
                    'id': f"geoapify_rest_{props.get('place_id')}",
                    'name': name,
                    'cuisine': cuisine,
                    'category': 'Food',
                    'meal_types': meal_types,
                    'address': props.get('formatted', 'Local Restaurant'),
                    'latitude': props.get('lat'),
                    'longitude': props.get('lon'),
                    'rating': 4.2,
                    'price_level': 2,
                    'image_url': self._fetch_pexels_image(f"{name} food {cuisine}"),
                    'description': cats[0].replace('catering.', '').replace('_', ' ').title() if cats else "Restaurant",
                    'source': 'geoapify'
                })
                if len(results) >= number: break
            return results
        except Exception:
            return []
