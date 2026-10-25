import json
from workflows.travel_graph import TravelGraph

graph = TravelGraph()
initial_state = {
    "current_step": "chat",
    "user_input": "Hyderabad -> Agra, 6 days, 5 people, 2 kids, health: good, hobbies: photography, viewpoints, start date: flexible, budget: ₹70,000",
    "history": [],
    "user_info": {},
    "attractions": [],
    "budget": {},
    "ai_recommendation_generated": False
}

state = initial_state
for i in range(10):  # limit steps just in case
    print(f"\n--- Running step: {state.get('current_step')} ---")
    state = graph.process_step(state.get("current_step"), **state)
    
    if not state:
        print("State is None. Exiting.")
        break
        
    print(f"Next step will be: {state.get('next_step')}")
    
    if state.get("next_step") == state.get("current_step"):
        print("Done or blocked on user input")
        break
    
    if not state.get("next_step"):
        break
        
    state["current_step"] = state.get("next_step")

print("\n\nFINAL STATE KEYS:")
print(list(state.keys()))
print("Budget:", state.get("budget"))
if state.get("optimal_route"):
    print("Optimal Route days:", len(state["optimal_route"]))
else:
    print("No optimal route found.")
