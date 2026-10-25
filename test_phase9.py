import os
import json
os.environ['TESTING'] = 'true'
from workflows.travel_graph import TravelGraph

def run_test():
    graph = TravelGraph()
    
    # 1. Chat Phase
    input1 = "I want to explore Kerala for 5 days with 10 people, 0 kids and I have strictly 80000 budget and I like viewpoints and photography and I am in good health"
    print("--- CHAT STEP 1 ---")
    res1 = graph.process_step('chat', user_input=input1)
    session_id = res1.get('session_id')
    
    input2 = "start date is 22 November 2026 and origin city is Tirupati"
    print("--- CHAT STEP 2 ---")
    res2 = graph.process_step('chat', user_input=input2, session_id=session_id)
    
    # consume the stream
    for chunk in res2['stream']:
        print("CHUNK:", chunk)
        
    print("Chat Step 2 Response:", res2)
    
    # Check if information step executed correctly
    if res2.get('next_step') == 'recommend' or res2.get('next_step') == 'information':
        print("Information stage successfully triggered.")
        
    if res2.get('next_step') == 'information':
        print("--- INFORMATION STEP ---")
        res2_info = graph.process_step('information', session_id=session_id)
        for chunk in res2_info['stream']:
            print("INFO CHUNK:", chunk)
        if res2_info.get('next_step') == 'recommend':
            print("--- RECOMMEND STEP ---")
            res2_rec = graph.process_step('recommend', session_id=session_id)
            for chunk in res2_rec['stream']:
                print("REC CHUNK:", chunk)
                
    print(json.dumps(graph.state['user_info'], indent=2))
    
    # Simulate user selection
    print("--- SUBMIT ATTRACTIONS ---")
    if 'attractions' in graph.state:
        # User selected 5 attractions
        graph.state['selected_attractions'] = graph.state['attractions'][:5]
    else:
        print("No attractions found in state to select!")
        return
        
    # Proceed to strategy
    print("--- STRATEGY STEP ---")
    res3 = graph.process_step('strategy', user_input="plan the route", session_id=session_id)
    for chunk in res3['stream']:
        print("STRAT CHUNK:", chunk)
    print("Strategy Response:", res3.get('next_step'))
    
    # Proceed to communication
    print("--- COMMUNICATION STEP ---")
    res4 = graph.process_step('communication', user_input="proceed", session_id=session_id)
    for chunk in res4['stream']:
        print("COMM CHUNK:", chunk)
    print("Communication Response:", res4.get('next_step'))
    
    # Proceed to route
    print("--- ROUTE STEP ---")
    res5 = graph.process_step('route', user_input="proceed", session_id=session_id)
    if 'stream' in res5 and res5['stream']:
        for chunk in res5.get('stream', []):
            print("ROUTE CHUNK:", chunk)
    print("Route Response:", res5.get('next_step'))
    
    # Final check
    print("--- FINAL ITINERARY ---")
    if 'itinerary' in res5:
        print(f"Itinerary length: {len(res5['itinerary'])}")
        for day in res5['itinerary']:
            print(f"Day: {day.get('day')} -> Date: {day.get('date')}")
            for act in day.get('activities', []):
                print(f"  - {act.get('time', '')} {act.get('name')}")
    else:
        print("No itinerary generated.")
        
    if 'budget' in res5:
        b = res5['budget']
        print(f"BUDGET INFO: Limit={b.get('budget_limit')}, Total={b.get('estimated_total')}, Infeasible={b.get('budget_infeasible')}")
        if b.get('budget_infeasible'):
            print(f"Budget Warning: {b.get('budget_warning')}")
    else:
        print("No budget info.")

if __name__ == '__main__':
    run_test()
