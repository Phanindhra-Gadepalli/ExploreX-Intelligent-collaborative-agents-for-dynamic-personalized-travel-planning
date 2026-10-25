import json
from workflows.travel_graph import TravelGraph

graph = TravelGraph()
s_id = 'test_session_1'

graph.process_step('initial_preferences', session_id=s_id, user_info={'city': 'Delhi', 'origin_city': 'Nellore', 'days': 5, 'people': 5, 'budget': 'medium'})

# After initial, it goes to communication to recommend attractions
res_comm1 = graph.process_step('communication', session_id=s_id)
attractions = graph.state['attractions']
print("Attractions len:", len(attractions))

# Select some attractions
selected = attractions[:15]
graph.state['selected_attractions'] = selected
res_comm2 = graph.process_step('communication', session_id=s_id, user_input="yes", selected_attractions=selected)

res_strat = graph.process_step('strategy', session_id=s_id)
print("Strategy daily plan:", graph.state.get("daily_plan"))

res_route = graph.process_step('route', session_id=s_id, start_date='2026-07-25')
itinerary = res_route.get('itinerary', [])

for day in itinerary:
    print(f"Day {day['day']}: {len(day['spots'])} spots")

print("Budget:", graph.state.get('budget'))
print("Transit:", graph.state.get('transit_options'))
