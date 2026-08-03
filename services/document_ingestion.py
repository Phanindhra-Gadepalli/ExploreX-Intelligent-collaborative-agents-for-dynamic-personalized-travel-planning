import os
import json
from langchain.vectorstores import Chroma
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.schema import Document

class DocumentIngestor:
    def __init__(self, persist_directory="data/vector_db"):
        self.persist_directory = persist_directory
        # Use a lightweight embedding model
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        
        # Ensure the directory exists
        os.makedirs(self.persist_directory, exist_ok=True)
        
        # Initialize ChromaDB
        self.vector_db = Chroma(
            collection_name="travel_knowledge",
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def load_mock_data(self):
        """Load some mock travel data to demonstrate RAG."""
        mock_documents = [
            Document(
                page_content="Jaipur, known as the Pink City, is famous for its stunning architecture. The City Palace and Amer Fort are incredibly popular. The best time to visit is during winter. Amer Fort has uneven terrain and many steps, making it less suitable for those with mobility issues.",
                metadata={"source": "rajasthan_guide.txt", "category": "history_and_accessibility"}
            ),
            Document(
                page_content="Kerala is famous for its backwaters in Alleppey. Houseboat cruises are a must-do. Most houseboats are wheelchair accessible, making it great for elderly travelers. The monsoon season (June to August) is beautiful but means outdoor activities might be rained out.",
                metadata={"source": "kerala_guide.txt", "category": "weather_and_accessibility"}
            ),
            Document(
                page_content="Goa's beaches are bustling during December and January, which are the peak crowd times. For a quiet trip, consider South Goa. Popular vegetarian restaurants include 'Bean Me Up' and 'Navtara'.",
                metadata={"source": "goa_guide.txt", "category": "food_and_crowds"}
            ),
            Document(
                page_content="Delhi has severe air pollution issues in November. Historical sites like the Red Fort and Qutub Minar are completely outdoors. If the weather is bad, the National Museum is an excellent indoor alternative that takes 3-4 hours to explore.",
                metadata={"source": "delhi_guide.txt", "category": "weather_and_indoor_alternatives"}
            ),
            Document(
                page_content="In Rajasthan, vegetarian food is widely available, with Dal Baati Churma being a local specialty. However, many forts require significant walking. Typical visit duration for large forts is 2 to 3 hours.",
                metadata={"source": "rajasthan_food_culture.txt", "category": "food_and_duration"}
            )
        ]

        print("Ingesting mock documents into ChromaDB...")
        self.vector_db.add_documents(mock_documents)
        self.vector_db.persist()
        print(f"Successfully ingested {len(mock_documents)} documents into {self.persist_directory}")

    def query(self, query_text, k=2):
        """Query the vector database."""
        results = self.vector_db.similarity_search(query_text, k=k)
        return results

if __name__ == "__main__":
    ingestor = DocumentIngestor()
    ingestor.load_mock_data()
    
    # Test query
    print("\nTesting Query: 'vegetarian food in goa'")
    results = ingestor.query("vegetarian food in goa", k=1)
    for r in results:
        print(f"- {r.page_content}")
