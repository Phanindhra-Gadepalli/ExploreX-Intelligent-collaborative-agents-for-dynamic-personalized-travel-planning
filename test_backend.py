import requests
import json

base_url = "http://localhost:8000/api/stream"
session_id = "test-session"

def send_chat(msg, step="chat", kwargs=None):
    if kwargs is None: kwargs = {}
    params = {
        "user_input": msg,
        "step": step,
        "session_id": session_id,
        **kwargs
    }
    r = requests.get(base_url, params=params, stream=True)
    complete_data = None
    for line in r.iter_lines():
        if line:
            line_str = line.decode('utf-8')
            print("STREAM:", line_str)
            if line_str.startswith('data: '):
                try:
                    data = json.loads(line_str[6:])
                    if data.get('type') == 'complete':
                        complete_data = data
                except: pass
    return complete_data

print("Step 1: Init")
d1 = send_chat("Hi, my name is Vamsi.")
print(d1.get('response'))

print("\nStep 2: Origin")
d2 = send_chat("Travelling from Nellore to Thanjavur.")
print(d2.get('response'))

print("\nStep 3: Details")
d3 = send_chat("Thanjavur and Madurai for 4 days with medium budget and 5 people, no kids, I am in good health, I enjoy history, old temples and palaces, start date is 2026-10-26.")
print("Next step:", d3.get('next_step'))

print("\nStep 4: Strategy (Trigger route)")
d4 = send_chat("Please plan the route for me", step="strategy", kwargs={"force_continue": "true"})
print("Final Next Step:", d4.get('next_step'))
if 'itinerary' in d4:
    print("Itinerary length:", len(d4['itinerary']) if isinstance(d4['itinerary'], list) else d4['itinerary'])
else:
    print("NO ITINERARY IN RESPONSE")

if 'error' in d4:
    print("ERROR:", d4['error'])
