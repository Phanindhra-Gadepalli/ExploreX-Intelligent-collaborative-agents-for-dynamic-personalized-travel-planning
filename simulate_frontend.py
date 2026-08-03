import requests
import json
import sseclient

def test_flow():
    session = requests.Session()
    # 1. Start chat
    url = "http://127.0.0.1:8000/api/stream?step=chat&user_input=Hi,%20I%20am%20Alice,%20want%20to%20go%20to%20Hyderabad%20from%20Delhi%20for%203%20days,%20budget%20is%20500,%202%20adults,%20no%20kids,%20good%20health,%20like%20nature,%20start%20date%202024-10-01"
    
    print(">>> CALLING CHAT STEP")
    resp = session.get(url, stream=True)
    client = sseclient.SSEClient(resp)
    
    next_step = None
    session_id = None
    for event in client.events():
        data = json.loads(event.data)
        if data['type'] == 'chunk':
            print("CHUNK:", data['content'])
        elif data['type'] == 'complete':
            print("COMPLETE:", data)
            next_step = data.get('next_step')
            session_id = data.get('session_id')
            
    print(f"Next step is {next_step}, Session ID: {session_id}")
    
    # Simulating the frontend auto-trigger logic
    
    prevStep = 'chat'
    state_step = next_step
    
    if prevStep == 'chat' and state_step in ['retrieval', 'information']:
        print(">>> AUTO-TRIGGERING RETRIEVAL/INFORMATION")
        url = f"http://127.0.0.1:8000/api/stream?step={state_step}&user_input=continue&session_id={session_id}"
        resp = session.get(url, stream=True)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            data = json.loads(event.data)
            if data['type'] == 'chunk':
                print("CHUNK:", data['content'])
            elif data['type'] == 'complete':
                print("COMPLETE:", data)
                next_step = data.get('next_step')
                
        prevStep = state_step
        state_step = next_step
        print(f"Next step is {next_step}")

    if prevStep == 'retrieval' and state_step == 'recommend':
        print(">>> AUTO-TRIGGERING RECOMMEND")
        url = f"http://127.0.0.1:8000/api/stream?step={state_step}&user_input=continue&session_id={session_id}"
        resp = session.get(url, stream=True)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            data = json.loads(event.data)
            if data['type'] == 'chunk':
                print("CHUNK:", data['content'])
            elif data['type'] == 'complete':
                print("COMPLETE:", data)
                next_step = data.get('next_step')
                
        prevStep = state_step
        state_step = next_step
        print(f"Next step is {next_step}")
        
    print(f"FINAL STATE: prevStep={prevStep}, state_step={state_step}")
    
if __name__ == '__main__':
    test_flow()
