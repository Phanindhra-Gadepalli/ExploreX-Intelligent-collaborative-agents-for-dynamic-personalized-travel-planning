import sys
sys.path.append('.')
from dotenv import load_dotenv
load_dotenv()
from agents.retrieval_agent import RetrievalAgent
from agents.recommend_agent import RecommendAgent
import json
import io

# Ensure UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ret_agent = RetrievalAgent()
print(f"Vector DB collection count: {ret_agent.vector_db._collection.count()}")
user_prefs = {'hobbies': 'history, architecture', 'budget': 'medium', '_dest_lat': 27.1751, '_dest_lng': 78.0421}
context = ret_agent.retrieve_context(user_prefs, 'Agra')
print('Context length:', len(context))
print('First 100 chars of context:', context[:100])

rec_agent = RecommendAgent()
pois = rec_agent.extract_pois_from_text(context, 'Agra', 27.1751, 78.0421)
print(f'Extracted {len(pois)} POIs')
print(json.dumps(pois, indent=2))
