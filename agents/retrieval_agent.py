import os
from langchain.vectorstores import Chroma
from langchain.embeddings import HuggingFaceEmbeddings

class RetrievalAgent:
    def __init__(self, persist_directory="data/vector_db"):
        self.persist_directory = persist_directory
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        
        # Load ChromaDB
        self.vector_db = Chroma(
            collection_name="travel_knowledge",
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def retrieve_context(self, user_prefs: dict, city: str, k: int = 3) -> str:
        """
        Retrieves relevant contextual information from the Vector Database based on user preferences.
        """
        hobbies = user_prefs.get("hobbies", "")
        health = user_prefs.get("health", "")
        budget = user_prefs.get("budget", "")
        
        # Formulate query
        query_parts = [f"travel guides for {city}"]
        if hobbies:
            query_parts.append(f"attractions related to {hobbies}")
        if health == "limited":
            query_parts.append("wheelchair accessibility and easy terrain")
        if budget:
            query_parts.append(f"{budget} budget options")
            
        query = " ".join(query_parts)
        print(f"[RETRIEVAL AGENT] Querying Vector DB with: '{query}'")
        
        try:
            results = self.vector_db.similarity_search(query, k=k)
            if not results:
                return "No additional background knowledge found."
                
            context = "\n".join([f"- {doc.page_content}" for doc in results])
            print(f"[RETRIEVAL AGENT] Retrieved {len(results)} documents.")
            return context
        except Exception as e:
            print(f"[ERROR] RetrievalAgent failed: {str(e)}")
            return ""

if __name__ == "__main__":
    agent = RetrievalAgent()
    context = agent.retrieve_context({"hobbies": "history", "health": "limited"}, "Jaipur")
    print("\nRetrieved Context:\n", context)
