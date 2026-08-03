import sys
import os
sys.path.append('d:/project/explorex_main_india')
from agents.information_agent import InformationAgent

agent = InformationAgent()
res = agent.gmaps.places(query="top tourist attractions forts temples in Maharashtra")
print("Text Search Results:")
for r in res.get('results', []):
    print(r.get('name'), r.get('formatted_address'))
