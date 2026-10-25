import http.client
import json
import os
import time
from datetime import datetime, timedelta
from urllib.parse import quote # For URL encoding location names
from urllib.parse import urlencode # For building query strings

class CarRentalService:
    """
    Service class for interacting with 'booking-com-api5.p.rapidapi.com' API via RapidAPI.
    Gets car rental data, processes to extract key information, sorts by price by default,
    returns top 10 results.

    A failed key/quota check (401/403/429) disables this provider for a cooldown
    window instead of being retried on every request: the workflow then keeps the
    self-drive recommendation and prices it from a documented fallback estimate
    rather than hammering an endpoint that is known not to answer.
    """

    # host -> unix timestamp until which calls are skipped
    _DISABLED_UNTIL = {}

    @classmethod
    def _cooldown_seconds(cls) -> int:
        try:
            return int(os.getenv("CAR_RENTAL_COOLDOWN_SECONDS", "900"))
        except (TypeError, ValueError):
            return 900

    @classmethod
    def disabled_until(cls, host=None):
        return cls._DISABLED_UNTIL.get(host or "booking-com-api5.p.rapidapi.com")

    @classmethod
    def _disable(cls, host, reason):
        until = time.time() + cls._cooldown_seconds()
        cls._DISABLED_UNTIL[host] = until
        print(f"[PROVIDER][CAR_RENTAL][UNAVAILABLE] {host} disabled for "
              f"{cls._cooldown_seconds()}s ({reason}). Rental costs will be estimated.")
        return until

    def __init__(self, rapidapi_key: str):
        """
        Initialize the service with a RapidAPI key.

        Args:
            rapidapi_key (str): Your RapidAPI key.
        """
        if not rapidapi_key or rapidapi_key == "YOUR_RAPIDAPI_KEY" or len(rapidapi_key) < 30:
            raise ValueError("A valid RapidAPI key is required.")

        self.api_key = rapidapi_key
        self.api_host = "booking-com-api5.p.rapidapi.com"
        self.endpoint = "/car/avaliable-car" # Note the API provider's spelling
        self.timeout = float(os.getenv("CAR_RENTAL_TIMEOUT_SECONDS", "12"))
        self.headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.api_host
        }
        print(f"CarRentalService initialized, host: {self.api_host}")

    def _process_response(self, api_response: dict) -> list[dict]:
        """
        (Internal helper method) Process the raw API response, extract simplified information.
        """
        if not api_response or not isinstance(api_response, dict): return [] # Basic check
        search_results = api_response.get("data", {}).get("search_results", [])
        if not isinstance(search_results, list): return [] # Check if it's a list
        if not search_results: return [] # Check if the list is empty

        processed_cars = []

        for offer in search_results:
            if not isinstance(offer, dict): continue # Skip invalid items
            try:
                pricing_info = offer.get("pricing_info", {})
                vehicle_info = offer.get("vehicle_info", {})
                supplier_info = offer.get("supplier_info", {})
                route_info = offer.get("route_info", {})
                pickup_info = route_info.get("pickup", {})

                price = pricing_info.get("drive_away_price")
                if price is None: continue # Skip items without price

                car_data = {
                    "car_model": vehicle_info.get("v_name", "N/A"),
                    "car_group": vehicle_info.get("group", "N/A"),
                    "price": price,
                    "currency": pricing_info.get("currency", ""),
                    "image_url": vehicle_info.get("image_url"),
                    "pickup_location_name": pickup_info.get("name", "N/A"),
                    "supplier_name": supplier_info.get("name", "N/A"),
                }
                processed_cars.append(car_data)
            except Exception as e:
                print(f"Warning: Error processing individual offer: {e}")
                continue

        print(f"Processed {len(processed_cars)} valid car rental options.")
        return processed_cars

    def _sort_and_limit(self, processed_cars: list[dict], sort_by='price', limit=30) -> list[dict]:
        """(Internal helper method) Sort and limit the list."""
        if not processed_cars: return []
        # ... (sorting and limiting logic remains unchanged) ...
        key_func = None; reverse_sort = False
        if sort_by == 'price':
            key_func = lambda x: x.get('price', float('inf')); reverse_sort = False
            print("Sorting by price (low to high)...")
        else: print(f"Warning: Unknown sorting option '{sort_by}'.")
        if key_func:
            try: processed_cars.sort(key=key_func, reverse=reverse_sort)
            except Exception as e: print(f"Sorting error: {e}.")
        final_count = min(len(processed_cars), limit)
        print(f"Limiting results to top {final_count}.")
        return processed_cars[:final_count]

    def find_available_cars(
        self,
        # ... (method parameter definitions remain unchanged) ...
        pickup_lat: float, pickup_lon: float, pickup_date: str, pickup_time: str, # HH:MM:SS
        dropoff_lat: float, dropoff_lon: float, dropoff_date: str, dropoff_time: str, # HH:MM:SS
        currency_code: str, driver_age: int | None = None, language_code: str | None = None,
        pickup_loc_name: str | None = None, dropoff_loc_name: str | None = None,
        pickup_city: str | None = None, dropoff_city: str | None = None
    ) -> list[dict] | None:
        """
        Search for available vehicles, process results, sort by price, and return up to 10 results.

        Args: (same as previously defined)

        Returns:
            List of dictionaries with simplified vehicle information (up to 10 items), sorted by price.
            Returns None if the API call critically fails.
        """
        # --- Build API request parameters ---
        querystring = {
            "pickup_latitude": pickup_lat, "pickup_longtitude": pickup_lon, # API's spelling
            "pickup_date": pickup_date, "pickup_time": pickup_time,      # HH:MM:SS
            "dropoff_latitude": dropoff_lat, "dropoff_longtitude": dropoff_lon, # API's spelling
            "drop_date": dropoff_date,        # API's name
            "drop_time": dropoff_time,        # API's name, HH:MM:SS
            "currency_code": currency_code
        }
        if driver_age is not None: querystring["driver_age"] = driver_age
        if language_code is not None: querystring["languagecode"] = language_code
        if pickup_loc_name is not None: querystring["pickup_location"] = pickup_loc_name
        if dropoff_loc_name is not None: querystring["dropoff_location"] = dropoff_loc_name

        # Build the endpoint with query parameters
        endpoint_with_params = f"{self.endpoint}?{urlencode(querystring)}"

        # --- Circuit breaker: don't re-call a provider already known to be down ---
        disabled_until = self._DISABLED_UNTIL.get(self.api_host)
        if disabled_until and time.time() < disabled_until:
            remaining = int(disabled_until - time.time())
            print(f"[PROVIDER][CAR_RENTAL][SKIPPED] Provider disabled for another {remaining}s; "
                  f"no request sent.")
            return None

        print(f"Preparing request: {endpoint_with_params}")

        try:
            # --- Send API request using http.client ---
            conn = http.client.HTTPSConnection(self.api_host, timeout=self.timeout)
            conn.request("GET", endpoint_with_params, headers=self.headers)
            response = conn.getresponse()
            print(f"API request status: {response.status}")
            
            if response.status != 200:
                try:
                    response.read()  # drain so the socket can close cleanly
                except Exception:
                    pass
                if response.status in (401, 403, 429):
                    self._disable(self.api_host, f"HTTP {response.status} (key/quota/rate limit)")
                else:
                    print(f"[PROVIDER][CAR_RENTAL][ERROR] {self.api_host} returned {response.status}")
                return None
                
            data = response.read()
            conn.close()
            
            # Parse JSON response
            raw_data = json.loads(data.decode('utf-8'))
            print("API response successfully parsed")

            # --- Process, sort, limit ---
            processed_data = self._process_response(raw_data)
            final_results = self._sort_and_limit(processed_data, sort_by='price', limit=10)

            return final_results # Return the final results list

        # --- Error handling ---
        except http.client.HTTPException as e: print(f"Error: HTTP connection issue - {e}"); return None
        except json.JSONDecodeError as e: print(f"Error: Failed to parse JSON response - {e}"); print(f"Received raw response text (first 500 chars): {data.decode('utf-8')[:500] if 'data' in locals() else 'No data'}"); return None
        except Exception as e: print(f"Unexpected error occurred: {e}"); return None
