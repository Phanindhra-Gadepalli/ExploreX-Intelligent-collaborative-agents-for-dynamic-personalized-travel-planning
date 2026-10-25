import json
import os
import time

def run_test():
    os.environ['TESTING'] = 'true'
    import main
    main.app.testing = True
    
    with main.app.test_client() as client:
        # Step 1: Initialize session
        print("Initializing session...")
        res = client.post('/login', json={'provider': 'guest'})
        assert res.status_code == 200, res.text
        session_id = res.get_json()['session_id'] if 'session_id' in res.get_json() else None
        
        # We need to hit stream endpoint directly. TestClient requires special handling for streaming.
        
        input1 = "I want to explore Kerala for 5 days with 10 people, 0 kids and I have strictly 80000 budget and I like viewpoints and photography and I am in good health"
        input2 = "start date is 22 November 2026 and origin city is Tirupati"
        
        url1 = f"/api/stream?step=chat&user_input={input1.replace(' ', '%20')}"
        print(f"Sending message 1: {input1}")
        res1 = client.get(url1)
        # Find completion data
        lines = res1.get_data(as_text=True).split('\n')
        complete_data = None
        for line in lines:
            if line.startswith('data: {"type": "complete"'):
                complete_data = json.loads(line[6:])
                break
        assert complete_data is not None
        print("Message 1 Complete data:", complete_data['next_step'])
        
        url2 = f"/api/stream?step=chat&user_input={input2.replace(' ', '%20')}"
        print(f"Sending message 2: {input2}")
        res2 = client.get(url2)
        lines = res2.get_data(as_text=True).split('\n')
        complete_data = None
        for line in lines:
            if line.startswith('data: {"type": "complete"'):
                complete_data = json.loads(line[6:])
                break
        
        assert complete_data is not None
        print("Message 2 Complete data:", complete_data.get('next_step'), complete_data.get('ui_action'))
        if complete_data.get('ui_action') == 'OPEN_EXPLORE':
            print("SUCCESS: UI action OPEN_EXPLORE received!")
        else:
            print("FAILURE: UI action OPEN_EXPLORE not found.")
            print(complete_data)

if __name__ == "__main__":
    run_test()
