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
        
        input1 = "I want to explore Kerala for 5 days with 10 people, 0 kids and I have strictly 80000 budget and I like viewpoints and photography and I am in good health"
        input2 = "start date is 22 November 2026 and origin city is Tirupati"
        
        url1 = f"/api/stream?step=chat&user_input={input1.replace(' ', '%20')}&session_id={session_id}"
        print(f"Sending message 1: {input1}")
        res0 = client.get(url1)
        for line in res0.get_data(as_text=True).split('\n'):
            pass # Consume stream to trigger processing
        
        url2 = f"/api/stream?step=chat&user_input={input2.replace(' ', '%20')}&session_id={session_id}"
        print(f"Sending message 2: {input2}")
        res1 = client.get(url2)
        
        # Parse SSE stream to find the complete data
        lines = res1.get_data(as_text=True).split('\n')
        complete_data = None
        for line in lines:
            if line.startswith('data: {"type": "complete"'):
                complete_data = json.loads(line[6:])
        
        if complete_data:
            print("Message 2 Complete data:", complete_data.get('next_step'), complete_data.get('ui_action'))
        else:
            print("No complete data found in stream")

        # Check session state
        res_state = client.get(f'/api/itinerary?session_id={session_id}')
        state = res_state.get_json()
        if not state:
            print(f"Failed to get state: {res_state.text}")
            return
            
        if 'budget' in state:
            b = state['budget']
            print(f"BUDGET INFO: Limit={b.get('budget_limit')}, Total={b.get('estimated_total')}, Infeasible={b.get('budget_infeasible')}")
            if b.get('budget_infeasible'):
                print(f"Budget Warning: {b.get('budget_warning')}")
        else:
            print("Budget not found in state yet")
            
        # We need to trigger the rest of the steps if it paused at recommend
        if complete_data and complete_data.get('next_step') == 'recommend':
            print("Paused at recommend, let's submit attraction selection")
            # submit attractions
            data = {
                "action": "submit_attractions",
                "attractions": state.get('attractions', [])[:5],
                "session_id": session_id
            }
            res2 = client.post('/api/action', json=data)
            print("Action response:", res2.get_json())
            
            # Now trigger the route generation stream
            url2 = f"/api/stream?step=route&session_id={session_id}"
            res_route = client.get(url2)
            
            lines = res_route.get_data(as_text=True).split('\n')
            for line in lines:
                if line.startswith('data: {"type": "complete"'):
                    route_data = json.loads(line[6:])
                    print("Route Complete data:", route_data)
                    
            res_state2 = client.get('/api/itinerary')
            state2 = res_state2.get_json()
            if 'budget' in state2:
                b = state2['budget']
                print(f"FINAL BUDGET INFO: Limit={b.get('budget_limit')}, Total={b.get('estimated_total')}, Infeasible={b.get('budget_infeasible')}")
                if b.get('budget_infeasible'):
                    print(f"Budget Warning: {b.get('budget_warning')}")
                
            print(f"Itinerary length: {len(state2.get('itinerary', []))}")
            if state2.get('budget_infeasible'):
                print("SUCCESS: Budget correctly flagged as infeasible and itinerary not finalized.")
            else:
                print("Hmm, budget was feasible?")

if __name__ == "__main__":
    run_test()
