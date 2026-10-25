import requests
import json
import sseclient
import urllib.parse
import time

def test_flow():
    session = requests.Session()
    msg = "Hi my name is Vamsi, starting from Nellore. Thanjavur and Madurai for 4 days with medium budget and 5 people, no kids, I am in good health, I enjoy history, old temples and palaces, start date is 2026-10-26"
    url = f"http://127.0.0.1:8000/api/stream?step=chat&user_input={urllib.parse.quote(msg)}"
    
    print(">>> CALLING CHAT STEP")
    resp = session.get(url, stream=True)
    client = sseclient.SSEClient(resp)
    
    next_step = None
    session_id = None
    for event in client.events():
        data = json.loads(event.data)
        if data['type'] == 'error':
            print("ERROR:", data.get('error'))
        elif data['type'] == 'chunk':
            print("CHUNK:", str(data.get('content')).encode('utf-8', 'replace').decode('utf-8'))
        elif data['type'] == 'complete':
            print("COMPLETE:", data.get('next_step'))
            next_step = data.get('next_step')
            session_id = data.get('session_id')
            
    print(f"Next step is {next_step}, Session ID: {session_id}")
    
    # Wait for auto-transitions (information -> retrieval -> recommend)
    while next_step in ['information', 'retrieval']:
        print(f">>> AUTO-TRIGGERING {next_step.upper()}")
        url = f"http://127.0.0.1:8000/api/stream?step={next_step}&user_input=continue&session_id={session_id}"
        resp = session.get(url, stream=True)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            data = json.loads(event.data)
            if data['type'] == 'complete':
                print("COMPLETE:", data.get('next_step'))
                next_step = data.get('next_step')
                # Save recommendations to pass back
                if next_step == 'recommend':
                    rec_attr = data.get('recommended_attractions', [])
                    print(f"Got {len(rec_attr)} recommended attractions.")

    if next_step == 'recommend':
        print(">>> SIMULATING USER CLICKING CONFIRM (forcing continue due to length)")
        url = f'http://127.0.0.1:8000/api/stream?step=recommend&user_input=Here+are+my+selected+attractions&session_id={session_id}&selected_attraction_ids=["ChIJ28242MiQyzsR9Y3V1GD5_Qo","dummy_2"]&force_continue=true'
        resp = session.get(url, stream=True)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            data = json.loads(event.data)
            if data['type'] == 'complete':
                print("COMPLETE (After Selection):", data.get('next_step'))
                next_step = data.get('next_step')
        print(f"Next step after selection: {next_step}")
        
    if next_step == 'strategy':
        print(">>> AUTO-TRIGGERING STRATEGY")
        url = f"http://127.0.0.1:8000/api/stream?step=strategy&user_input=Please+plan+the+route+for+me&session_id={session_id}"
        resp = session.get(url, stream=True)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            data = json.loads(event.data)
            if data['type'] == 'complete':
                print("COMPLETE (Strategy):", data.get('next_step'))
            if data.get('itinerary'):
                with open("test_itinerary.json", "w") as f:
                    json.dump(data.get('itinerary'), f, indent=2)
            next_step = data.get('next_step')

    if next_step == 'communication':
        print(">>> AUTO-TRIGGERING COMMUNICATION")
        url = f"http://127.0.0.1:8000/api/stream?step=communication&user_input=continue&session_id={session_id}"
        resp = session.get(url, stream=True)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            data = json.loads(event.data)
            if data['type'] == 'error':
                print("ERROR:", data.get('error'))
            elif data['type'] == 'chunk':
                print("CHUNK:", str(data.get('content')).encode('utf-8', 'replace').decode('utf-8'))
            elif data['type'] == 'complete':
                print("COMPLETE (Communication):", data.get('next_step'))
                print("ITINERARY:", bool(data.get('itinerary')), "BUDGET:", bool(data.get('budget')))
                if data.get('itinerary'):
                    with open("test_itinerary.json", "w") as f:
                        json.dump(data.get('itinerary'), f, indent=2)
                next_step = data.get('next_step')

    if next_step == 'route':
        print(">>> AUTO-TRIGGERING ROUTE")
        url = f"http://127.0.0.1:8000/api/stream?step=route&user_input=continue&session_id={session_id}"
        resp = session.get(url, stream=True)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            data = json.loads(event.data)
            if data['type'] == 'complete':
                print("COMPLETE (Route):", data.get('next_step'))
                print("ITINERARY:", json.dumps(data.get('itinerary'), indent=2))
                print("BUDGET:", json.dumps(data.get('budget'), indent=2))
                print("ROUTE:", json.dumps(data.get('optimal_route'), indent=2))
                next_step = data.get('next_step')

if __name__ == '__main__':
    test_flow()
