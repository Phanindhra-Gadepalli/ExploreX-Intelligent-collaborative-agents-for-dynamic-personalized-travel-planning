from agents.retrieval_agent import RetrievalAgent

agent = RetrievalAgent()
user_prefs = {
    'city': 'Agra',
    'hobbies': 'photography, viewpoints',
    'kids': 2,
    'health': 'good',
    'budget': 70000
}

try:
    print("Testing retrieve_context...")
    context = agent.retrieve_context(user_prefs, "Agra")
    print("\n[SUCCESS] RAG retrieval completed successfully!")
    print("Context length:", len(context))
except Exception as e:
    import traceback
    print(f"\n[ERROR] Crash occurred: {type(e).__name__}: {str(e)}")
    traceback.print_exc()
