import os
from agents.information_agent import InformationAgent
from services.poi_fallback import POIManager
info_agent = InformationAgent()
user_prefs = {
    '_dest_lat': 27.1751, 
    '_dest_lng': 78.0421,
    'city': 'Agra',
    'hobbies': 'history, architecture',
    'budget': 'medium',
    'duration_days': 2
}
pois = info_agent.get_attractions(27.1751, 78.0421, user_prefs, rerank_with_llm=False)
print(f'\nTotal POIs retrieved: {len(pois)}')
for p in pois[:15]:
    print(f"Name: {p.get('name')} | Score: {p.get('_internal_score', 'N/A')} | Wikidata: {p.get('wikidata_id', 'None')} | RecType: {p.get('recommendation_type', 'N/A')} | Type: {p.get('category')}")
