import json
from workflows.travel_graph import TravelGraph

graph = TravelGraph()
initial_state = {
    "current_step": "chat",
    "user_input": "I want to explore Agra and my origin city is Hyderabad for 8 days with 10 people, 0 kids, good health, flexible start date and a strict budget of ₹70,000. I like photography, viewpoints, waterfalls and rivers.",
    "history": [],
    "user_info": {},
    "attractions": [],
    "budget": {},
    "ai_recommendation_generated": False
}

state = initial_state
for i in range(10):
    print(f"\n--- Running step: {state.get('current_step')} ---")
    
    # If step is recommend and it's waiting, simulate a selection
    if state.get("current_step") == "recommend":
        print("Simulating selection of top 4 attractions to proceed...")
        state["selected_attraction_ids"] = [a["id"] for a in state.get("attractions", [])[:16]]
        state["is_confirmation"] = True
        state["force_continue"] = "true"
        
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

print("\n\nFINAL STATE:")
print("Budget:", state.get("budget"))
if state.get("optimal_route"):
    print("Optimal Route length:", len(state["optimal_route"]))
else:
    print("No optimal route found.")
