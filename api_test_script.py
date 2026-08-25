import os
import requests
from dotenv import load_dotenv

# Load .env file
load_dotenv('.env')

def check_gemini_key(key, name):
    print(f"Testing {name}...")
    if not key:
        print(f"  [FAIL] {name} is missing")
        return
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            print(f"  [OK] {name} works fine!")
        else:
            print(f"  [FAIL] {name} failed with status {response.status_code}: {response.text}")
    except Exception as e:
        print(f"  [FAIL] {name} encountered an error: {e}")


def check_rapidapi_key(key):
    print("Testing RAPIDAPI_KEY...")
    if not key:
        print("  [FAIL] RAPIDAPI_KEY is missing")
        return
    url = "https://booking-com-api5.p.rapidapi.com/car/avaliable-car"
    querystring = {
        "pickup_latitude": "37.7749",
        "pickup_longtitude": "-122.4194",
        "pickup_date": "2026-08-01",
        "pickup_time": "10:00:00",
        "dropoff_latitude": "37.7749",
        "dropoff_longtitude": "-122.4194",
        "drop_date": "2026-08-05",
        "drop_time": "10:00:00",
        "currency_code": "USD"
    }
    headers = {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": "booking-com-api5.p.rapidapi.com"
    }
    try:
        response = requests.get(url, headers=headers, params=querystring)
        if response.status_code == 200:
            print("  [OK] RAPIDAPI_KEY works fine!")
        elif response.status_code == 403 or response.status_code == 401:
            print(f"  [FAIL] RAPIDAPI_KEY auth failed with status {response.status_code}")
            print(f"  [DEBUG] Error message from RapidAPI: {response.text}")
        else:
            print(f"  [WARN] RAPIDAPI_KEY returned status {response.status_code}. The key might be valid, but the endpoint failed: {response.text}")
    except Exception as e:
        print(f"  [FAIL] RAPIDAPI_KEY encountered an error: {e}")

if __name__ == "__main__":
    gemini_key = os.environ.get("GEMINI_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY")
    rapidapi_key = os.environ.get("RAPIDAPI_KEY")

    check_gemini_key(gemini_key, "GEMINI_API_KEY")
    check_gemini_key(google_key, "GOOGLE_API_KEY")
    check_rapidapi_key(rapidapi_key)
