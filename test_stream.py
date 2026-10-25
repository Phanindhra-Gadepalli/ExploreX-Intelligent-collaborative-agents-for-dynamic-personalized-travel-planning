import requests
import json
import urllib.parse
import sseclient

session_id = "test-session-123456"

def send_chat(step, user_input, **kwargs):
    url = f"http://127.0.0.1:8000/api/stream?step={step}&user_input={urllib.parse.quote(user_input)}&session_id={session_id}"
    for k, v in kwargs.items():
        if isinstance(v, list) or isinstance(v, dict):
            v = json.dumps(v)
        url += f"&{k}={urllib.parse.quote(str(v))}"
        
    print(f"\n--- Stream for step: {step} ---")
    response = requests.get(url, stream=True)
    client = sseclient.SSEClient(response)
    for event in client.events():
        data_str = event.data
        try:
            data = json.loads(data_str)
            print(f"Type: {data.get('type')} | Next Step: {data.get('next_step')}")
            if data.get('type') == 'complete':
                print("KEYS IN COMPLETE:", list(data.keys()))
                if 'itinerary' in data:
                    print("ITINERARY LEN:", len(data['itinerary']) if data['itinerary'] else "NONE/EMPTY")
                if 'budget' in data:
                    print("BUDGET:", "EXISTS" if data['budget'] else "EMPTY")
                if 'transit_options' in data:
                    print("TRANSIT OPTIONS:", "EXISTS" if data['transit_options'] else "EMPTY")
        except Exception as e:
            print(f"Error parsing JSON: {e} | Line: {data_str[:50]}...")

print("Sending chat...")
send_chat("chat", "Hi, my name is Vamsi.")
send_chat("chat", "Travelling from Nellore to Thanjavur.")
send_chat("chat", "Thanjavur and Madurai for 4 days with medium budget and 5 people, no kids, I am in good health, I enjoy history, start date is 2026-10-26.")
# Now retrieval auto-triggers, but we need to call it if it doesn't? No, chat returns next_step="information"
# Wait, let's just use test_flow.py to trigger the flow and see the final result json from python!
