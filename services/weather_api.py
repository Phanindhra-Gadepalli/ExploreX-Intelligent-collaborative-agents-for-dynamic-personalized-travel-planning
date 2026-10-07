"""
This module provides a service for retrieving weather forecast and historical data using the Open-Meteo API.

Open-Meteo API: https://open-meteo.com/, requires no API key.

You should input the latitude and longitude of the location, and the start date and duration of the forecast.

Duration can be up to 16 days. If the end date is beyond 16 days, the module will return the historical average weather.

The returned data is in the following format:
[
    {
        "date": "2024-01-01",
        "max_temp": "20 °C",
        "min_temp": "10 °C",
        "precipitation": "10 mm",
        "wind_speed": "10 km/h",
        "precipitation_probability": "10%", not available for historical data
        "uv_index": "10", not available for historical data
    },
    ...
]


"""

import requests
from datetime import datetime, timedelta
import json
import os

class WeatherService:
    def __init__(self):
        """Initialize the weather service"""
        self.forecast_url = "https://api.open-meteo.com/v1/forecast"
        self.historical_url = "https://archive-api.open-meteo.com/v1/archive"
        self.cache_file = "weather_cache.json"
        self.cache = self._load_cache()

    def _load_cache(self):
        """Load weather cache from file"""
        if os.path.exists(self.cache_file):
            with open(self.cache_file, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    def _save_cache(self):
        """Save weather cache to file"""
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f)

    def _cache_key(self, lat, lng, date):
        """Generate a cache key for a location and date"""
        return f"{lat}_{lng}_{date}"

    def get_weather(self, lat, lng, start_date, duration):
        """Get weather forecast or historical data for a location and date range"""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = start + timedelta(days=duration - 1)
        today = datetime.now()

        if end <= today + timedelta(days=15):
            # Use forecast data
            return self._get_forecast_data(lat, lng, start, end)
        else:
            # Use historical data
            return self._get_historical_estimate(lat, lng, start, end)

    def _get_forecast_data(self, lat, lng, start, end):
        """Retrieve forecast data from Open-Meteo API"""
        params = {
            "latitude": lat,
            "longitude": lng,
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max", "precipitation_probability_mean", "uv_index_max", "weather_code", "relative_humidity_2m_mean"],
            "timezone": "auto"
        }
        try:
            response = requests.get(self.forecast_url, params=params)
            response.raise_for_status()
            data = response.json()
            return self._format_weather_data(data)
        except requests.RequestException as e:
            print(f"Error fetching forecast data: {e}")
            return {"error": "Unable to fetch forecast data"}

    def _get_historical_estimate(self, lat, lng, start, end):
        """Estimate future weather based on historical data"""
        historical_data = []
        # Get historical data from past years, ensuring we only request data that's actually in the past
        today = datetime.now().date()
        for year_offset in range(1, 5):
            past_start = start - timedelta(days=365 * year_offset)
            past_end = end - timedelta(days=365 * year_offset)
            
            # Skip this year if the end date isn't in the past yet
            if past_end.date() >= today:
                print(f"Skipping year offset {year_offset} as data isn't available yet")
                continue
            params = {
                "latitude": lat,
                "longitude": lng,
                "start_date": past_start.strftime("%Y-%m-%d"),
                "end_date": past_end.strftime("%Y-%m-%d"),
                "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max", "weather_code", "relative_humidity_2m_mean"],
                "timezone": "auto"
            }
            try:
                response = requests.get(self.historical_url, params=params)
                response.raise_for_status()
                data = response.json()
                historical_data.append(data)
            except requests.RequestException as e:
                print(f"Error fetching historical data for {past_start.year}: {e}")
                continue

        if not historical_data:
            return {"error": "Unable to fetch sufficient historical data"}

        return self._average_historical_data(historical_data)

    def _map_weather_code(self, code):
        if code is None: return "Unknown"
        mapping = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            56: "Light freezing drizzle", 57: "Dense freezing drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            66: "Light freezing rain", 67: "Heavy freezing rain", 71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
            77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            85: "Slight snow showers", 86: "Heavy snow showers", 95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
        }
        return mapping.get(code, "Unknown")

    def _generate_packing_advice(self, max_temp, rain, uv, condition):
        advice = []
        if rain and rain > 1.0 or "rain" in condition.lower() or "drizzle" in condition.lower():
            advice.append("Umbrella")
        if uv and uv > 5.0:
            advice.append("Sunscreen")
        if "snow" in condition.lower():
            advice.append("Winter coat")
        elif max_temp:
            if max_temp > 25: advice.append("Light clothing")
            elif max_temp < 15: advice.append("Jacket")
        if not advice:
            advice.append("Comfortable shoes")
        return ", ".join(advice)

    def _format_weather_data(self, data):
        """Format weather data into a user-friendly structure"""
        formatted_data = []
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        precipitations = daily.get("precipitation_sum", [])
        wind_speeds = daily.get("wind_speed_10m_max", [])
        precip_probabilities = daily.get("precipitation_probability_mean", [])
        uv_indices = daily.get("uv_index_max", [])
        weather_codes = daily.get("weather_code", [])
        humidities = daily.get("relative_humidity_2m_mean", [])

        for i in range(len(dates)):
            wcode = weather_codes[i] if i < len(weather_codes) else None
            cond = self._map_weather_code(wcode)
            uv = uv_indices[i] if i < len(uv_indices) and uv_indices[i] is not None else 0
            max_t = max_temps[i] if i < len(max_temps) else None
            precip = precipitations[i] if i < len(precipitations) else 0

            formatted_data.append({
                "date": dates[i],
                "max_temp": f"{max_temps[i]} °C" if max_t is not None else None,
                "min_temp": f"{min_temps[i]} °C" if i < len(min_temps) else None,
                "precipitation": f"{precipitations[i]} mm" if precip is not None else None,
                "wind_speed": f"{wind_speeds[i]} km/h" if i < len(wind_speeds) else None,
                "precipitation_probability": f"{precip_probabilities[i]}%" if i < len(precip_probabilities) else None,
                "uv_index": f"{uv}" if uv else "0",
                "condition": cond,
                "humidity": f"{humidities[i]}%" if i < len(humidities) else None,
                "packing_advice": self._generate_packing_advice(max_t, precip, uv, cond)
            })

        return formatted_data

    def _average_historical_data(self, historical_data):
        """Calculate average weather metrics from historical data"""
        aggregated_data = {}
        count = 0

        for data in historical_data:
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            max_temps = daily.get("temperature_2m_max", [])
            min_temps = daily.get("temperature_2m_min", [])
            precipitations = daily.get("precipitation_sum", [])
            wind_speeds = daily.get("wind_speed_10m_max", [])

            for i in range(len(dates)):
                date = dates[i]
                if date not in aggregated_data:
                    aggregated_data[date] = {
                        "max_temp": 0,
                        "min_temp": 0,
                        "precipitation": 0,
                        "wind_speed": 0,
                        "count": 0
                    }
                aggregated_data[date]["max_temp"] += max_temps[i]
                aggregated_data[date]["min_temp"] += min_temps[i]
                aggregated_data[date]["precipitation"] += precipitations[i]
                if i < len(wind_speeds):
                    aggregated_data[date]["wind_speed"] += wind_speeds[i]
                aggregated_data[date]["count"] += 1

        averaged_data = []
        for date, values in aggregated_data.items():
            averaged_data.append({
                "date": date,
                "max_temp": f"{values['max_temp'] / values['count']:.1f} °C",
                "min_temp": f"{values['min_temp'] / values['count']:.1f} °C",
                "precipitation": f"{values['precipitation'] / values['count']:.1f} mm",
                "wind_speed": f"{values['wind_speed'] / values['count']:.1f} km/h",
            })

        return averaged_data
        
    def test_get_weather(self):
        """Test the get_weather method with real API call"""
        # Test with specific coordinates (New York City)
        lat = 40.7128
        lng = -74.0060
        start_date = datetime.now().strftime("%Y-%m-%d")
        duration = 7
        
        print("\n=== Testing get_weather() for forecast ===")
        print(f"Location: New York City (lat: {lat}, lng: {lng})")
        print(f"Start date: {start_date}, Duration: {duration} days")
        
        forecast = self.get_weather(lat, lng, start_date, duration)
        
        # Print the raw forecast data
        print("\nForecast Results:")
        print(forecast)
        
        return forecast
    
    def test_get_historical_estimate(self):
        """Test the historical estimate functionality with real API call"""
        # Test with specific coordinates (London)
        lat = 51.5074
        lng = -0.1278
        
        # Use a future date (1 year from now)
        start_date = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
        duration = 10
        
        print("\n=== Testing get_weather() for historical estimate ===")
        print(f"Location: London (lat: {lat}, lng: {lng})")
        print(f"Start date: {start_date}, Duration: {duration} days")
        
        historical_estimate = self.get_weather(lat, lng, start_date, duration)
        
        # Print the raw historical estimate data
        print("\nHistorical Estimate Results:")
        print(historical_estimate)
        
        return historical_estimate


# Main function to run the tests
if __name__ == "__main__":
    
    
    # Create an instance of the WeatherService
    weather_service = WeatherService()
    
    # Run the tests
    print("Starting Weather API Tests...")
    
    # Test forecast
    weather_service.test_get_weather()
    
    # Test historical estimate
    weather_service.test_get_historical_estimate()
    

    print("\nWeather API Tests completed.")
