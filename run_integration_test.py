import requests
import json
import sseclient
import time
import sys
import io

# Force UTF-8 encoding for stdout
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

s = requests.Session()
BASE = "http://127.0.0.1:8000"

def get_state():
    r = s.get(f"{BASE}/api/state")
    return r.json()

def run_stream(step, user_input, extra_params=None):
    url = f"{BASE}/api/stream?step={step}&user_input={requests.utils.quote(user_input)}"
    if extra_params:
        for k,v in extra_params.items():
            url += f"&{k}={requests.utils.quote(str(v))}"
    
    response = s.get(url, stream=True)
    client = sseclient.SSEClient(response)
    last_data = None
    for event in client.events():
        if event.data:
            try:
                data = json.loads(event.data)
                print(f"[STREAM EVENT] {data.get('type')}: {data.get('agent', '')}")
                last_data = data
                if data.get('type') == 'error':
                    print(f"[ERROR] {data.get('error')}")
                    break
            except Exception as e:
                print("Error parsing SSE:", e, event.data)
    return last_data

print("1. Login / Set Destination")
r = s.post(f"{BASE}/api/auth/google", json={
    "token": "fake_token", 
    "email": "test@test.com", 
    "name": "Test User", 
    "picture": ""
})
print("Auth:", r.status_code)

print("\n2. Submit trip details via Chat step")
prompt = "I want to go to Dubai from Hyderabad for 8 days with 10 people, 0 kids. My budget is 8080000 INR strict. Health is good, flexible dates. Interests are Photography, Nature, Adventure, Nightlife."
res = run_stream('chat', prompt)

print("\n3. Check State")
state = get_state().get('trip', {})
print("Destination:", state.get('destination'))
print("Origin:", state.get('origin'))
print("Days:", state.get('days'))
print("People:", state.get('people'))
print("Kids:", state.get('kids'))
print("Budget Strictness:", state.get('budget_strictness'))

print("\n4. Trigger Recommendations (assuming some selected attractions)")
attractions = state.get('attractions', [])
print(f"Total attractions: {len(attractions)}")
selected_ids = [a.get('id') for a in attractions[:5]] if attractions else []
print(f"Selected attractions: {selected_ids}")
res = run_stream('recommend', 'Here are my selected attractions', {'selected_attraction_ids': json.dumps(selected_ids), 'force_continue': 'true'})

print("\n4.1 Trigger Strategy")
res = run_stream('strategy', 'plan the route')

print("\n4.2 Trigger Communication")
res = run_stream('communication', 'proceed')

print("\n5. Check Final State")
final_state = get_state().get('trip', {})
budget = final_state.get('budget', {})
transit = final_state.get('transit_options', [])
itinerary = final_state.get('itinerary', [])

print(f"Final Budget total: {budget.get('total')}")
print(f"Transit Options length: {len(transit) if transit else 0}")
if transit and isinstance(transit, dict):
    print(f"Transit has 'international': {'international' in transit}")
    print(f"Transit has 'local': {'local' in transit}")
print(f"Itinerary Days: {len(itinerary) if itinerary else 0}")

with open('test_results.json', 'w') as f:
    json.dump(final_state, f, indent=2)
print("Done.")
