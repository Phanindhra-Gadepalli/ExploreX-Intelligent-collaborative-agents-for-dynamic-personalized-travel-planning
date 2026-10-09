import os
import json
os.environ['OVERPASS_ENDPOINTS'] = 'http://localhost:9999'

from workflows.travel_graph import TravelGraph
graph = TravelGraph()
initial_state = {
    'current_step': 'chat',
    'user_input': 'I am Nithin from Kerala. Give me an itinerary for a religious and culture trip to Varanasi for me and my wife for 4 days',
    'history': [],
    'user_info': {},
    'attractions': [],
    'budget': {},
    'ai_recommendation_generated': False
}

state = initial_state
for i in range(10):
    print(f'\n--- Running step: {state.get("current_step")} ---')
    try:
        state = graph.process_step(state.get('current_step'), **state)
        
        if not state:
            print('State is None. Exiting.')
            break
            
        print(f'Next step will be: {state.get("next_step")}')
        
        if state.get('next_step') == state.get('current_step'):
            if state.get('current_step') == 'recommend':
                state['user_input'] = 'continue with recommended attractions'
                state['force_continue'] = 'true'
            else:
                state['user_input'] = 'continue'
        else:
            state['user_input'] = 'continue'
        
        if not state.get('next_step'):
            break
            
        state['current_step'] = state.get('next_step')
    except Exception as e:
        print('Exception caught:', e)
        break

print('\n\nFINAL STATE KEYS:')
print(list(state.keys()))
if state.get('optimal_route'):
    print('Route generated!')
if state.get('itinerary'):
    print('Itinerary generated!')
