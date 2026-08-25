# ExploreX Technical Documentation

## 1. Project Overview
ExploreX is an intelligent, multi-agent travel planning system designed to generate dynamic, personalized itineraries. It leverages large language models (LLMs), a Retrieval-Augmented Generation (RAG) pipeline, and a cascade of geospatial APIs to provide tailored recommendations, route planning, budget estimation, and travel strategies. The system focuses heavily on Indian tourism data while supporting international queries via web fallbacks.

## 2. Abstract
The project proposes an autonomous, multi-agent system (ExploreX) that orchestrates various specialized AI agents to solve the complex task of travel planning. Using LLMs combined with factual retrieval (RAG) and external APIs (geocoding, weather, POI, routing), the system curates personalized travel plans. The core functionality avoids rigid, rule-based itinerary generation by utilizing a graph-based state machine (`TravelGraph`) where multiple agents (Chat, Information, Retrieval, Recommend, Strategy, Route, Budget, Communication, Transit) collaborate sequentially to collect user preferences, validate locations, retrieve factual data, recommend points of interest (POIs), and compile a holistic travel itinerary.

## 3. Objectives
- **Primary Objective:** To build an intelligent, multi-agent system capable of dynamically generating highly personalized, practical travel itineraries.
- **Secondary Objectives:** 
  - To eliminate dependency on paid geospatial APIs (like Google Maps) by utilizing a robust fallback cascade of free-tier services (Geoapify, Nominatim, Overpass API).
  - To ensure factual accuracy of recommendations using a RAG pipeline loaded with comprehensive travel guides.
  - To handle complex constraints such as weather, travel days, group size, and budget.

## 4. Problem Statement
**Problem:** Traditional travel planning is time-consuming, requiring users to manually research destinations, map out routes, cross-reference weather, and estimate budgets across fragmented platforms.
**Limitations of Conventional Approaches:** Existing platforms either provide generic, one-size-fits-all itineraries or require substantial manual input to build custom plans. They often lack real-time adaptability (e.g., weather-based recommendations) or contextual awareness of local nuances.
**Solution:** ExploreX addresses these limitations by employing collaborative AI agents that autonomously handle information gathering, factual retrieval, geospatial routing, and budget estimation, presenting a unified and personalized travel plan in a single conversational interface.

## 5. Key Features
- **Conversational Entity Extraction:** Automatically extracts user preferences (city, days, group size, kids, budget) from natural language inputs. (Implemented in `ChatAgent`).
- **Free-Tier Geospatial Cascade:** Validates destinations, geocodes locations, and fetches POIs using a highly resilient, cost-free cascade of providers (Geoapify → Nominatim → Photon → Static Fallback).
- **Factual RAG Retrieval:** Augments LLM knowledge with curated, localized travel guides to ensure recommendations are accurate, culturally relevant, and up-to-date. (Implemented in `RetrievalAgent` and `RecommendAgent`).
- **Weather-Aware Recommendations:** Fetches real-time weather forecasts to dynamically adjust POI recommendations (e.g., suggesting indoor activities during extreme heat/pollution). (Implemented in `InformationAgent`).
- **Dynamic Route Planning:** Generates optimized daily itineraries with logical routing and transit options. (Implemented in `RouteAgent` and `TransitAgent`).
- **Holistic Strategy & Budgeting:** Evaluates the overall trip feasibility, estimates costs based on group demographics, and recommends additional activities or car rentals if needed. (Implemented in `StrategyAgent` and `BudgetAgent`).

## 6. Technology Stack
- **Backend Framework:** **Flask (2.3.3)** - Used to serve the frontend templates and expose the `/api/process` and `/api/stream` endpoints that interface with the agent workflow.
- **AI & LLMs:**
  - **LangChain (0.0.267):** Framework for constructing the LLM chains, managing prompts, and handling the RAG pipeline (vector stores and embeddings).
  - **Gemini (via Google Generative AI):** The primary LLM used for natural language understanding, entity extraction, recommendation generation, and conversational responses.
- **Vector Database & Embeddings:**
  - **ChromaDB (0.4.14):** Local vector database used to store and retrieve embedded travel documents for the RAG pipeline.
  - **HuggingFaceEmbeddings:** Uses `sentence-transformers/all-MiniLM-L6-v2` to convert travel documents into dense vector representations for semantic search.
- **Geospatial & APIs:**
  - **Geoapify & Nominatim (via Geopy):** Used for forward/reverse geocoding and location validation without incurring Google Maps billing.
  - **Overpass API (OSM):** Used for querying detailed Points of Interest (POIs) with a multi-instance fallback strategy to ensure reliability.
  - **RapidAPI:** Used for fetching weather data and fuel prices.
- **Frontend Technologies:** HTML, CSS, JavaScript (Vanilla) - Provides the user interface for the chat-based travel planner, rendering dynamic maps and itineraries.
- **Mapping:** **Folium (0.14.0)** - Used for backend map generation (if applicable) or formatting map data for the frontend.
- **Data Processing:** Pandas, Numpy - For data manipulation, especially in transit or budget calculations.

## 7. System Architecture
The system follows a multi-agent orchestrated architecture pattern, facilitated by the `TravelGraph` class which acts as a state machine.
- **Frontend Layer:** The user interacts via a web interface, sending natural language queries.
- **Orchestration Layer (`TravelGraph`):** Receives the input and manages the state (`self.state`). It determines which agent should process the data next based on the current context.
- **Agent Layer:**
  - `ChatAgent`: Extracts entities.
  - `InformationAgent`: Validates locations, fetches weather, and retrieves broad destination data.
  - `RetrievalAgent`: Queries ChromaDB for contextual RAG data.
  - `RecommendAgent`: Filters and curates specific POIs based on retrieved context and user preferences.
  - `StrategyAgent`: Formulates a daily plan, calculates time requirements, and assesses the need for car rentals.
  - `CommunicationAgent`: Intermediary summarizing the plan.
  - `RouteAgent` & `TransitAgent`: Map out logical routes and transit methods.
  - `BudgetAgent`: Estimates trip costs.
- **Data & API Layer:** Connects to ChromaDB, Geoapify, Nominatim, OSM Overpass, and RapidAPI to ground the agents' decisions in real-world data.

## 8. Complete Project Structure
- `main.py`: The entry point for the Flask application. Manages routes, session state, and initializes the `TravelGraph`.
- `workflows/travel_graph.py`: The core state machine orchestrating the sequence of agents.
- `agents/`: Contains the specific logic for each AI agent.
  - `chat_agent.py`, `information_agent.py`, `retrieval_agent.py`, `recommend_agent.py`, `strategy_agent.py`, `route_agent.py`, `budget_agent.py`, `communication_agent.py`, `transit_agent.py`.
- `services/`: Contains integrations with external APIs.
  - `geocoding.py`: Manages the Geoapify/Nominatim/Static provider cascade.
  - `poi_fallback.py`: Manages the Overpass API queries with fallback instances.
  - `document_ingestion.py`: Defines the foundational travel knowledge documents and initializes the ChromaDB vector store.
  - `weather_api.py`, `fuel_price_api.py`, `car_rental_api.py`: Integrations for specific data points.
- `data/`: Contains static data sets (e.g., `attractions.json`, `global_fuel_prices.csv`) and the local ChromaDB storage (`vector_db/`).
- `frontend/`: Contains the web assets.
  - `templates/`: HTML templates (`index.html`, etc.).
  - `static/`: CSS styling, client-side JavaScript, and images.
- `evaluation/`: Contains scripts and outputs for evaluating the RAG/LLM performance (e.g., `eval.json`).
- `requirements.txt`: Python dependency list.
- `.env`: Environment variable configuration.

## 9. Application Flow
1. **User Input:** User enters a travel request in the chat interface.
2. **Entity Extraction (`ChatAgent`):** The system extracts city, days, group size, and preferences. If information is missing, it prompts the user.
3. **Location Validation (`InformationAgent` & `Geocoding`):** The requested city is validated. Indian cities are mapped to coordinates; international cities trigger web fallbacks.
4. **Context Gathering (`InformationAgent`):** Weather is fetched for the destination. Initial attractions are fetched via the POI cascade.
5. **RAG Retrieval (`RetrievalAgent`):** Destination-specific documents are queried from ChromaDB.
6. **Recommendation (`RecommendAgent`):** The system curates a finalized list of attractions based on weather, RAG context, and POI data. The frontend displays these for user selection.
7. **User Selection:** The user selects desired attractions.
8. **Strategy & Planning (`StrategyAgent`):** The system plans the time required and suggests additional attractions to fill the itinerary.
9. **Routing & Budgeting (`RouteAgent`, `TransitAgent`, `BudgetAgent`):** An optimal route is calculated, transit options are evaluated, and costs are estimated.
10. **Final Output:** The frontend renders the complete, personalized itinerary, budget, and map data.

## 10. Detailed Module Explanation
- **`TravelGraph` (`workflows/travel_graph.py`):** The central state manager. It defines `_process_chat`, `_process_information`, `_process_retrieval`, etc., maintaining a `self.state` dictionary per user session to persist data across API calls.
- **`GeocodingManager` (`services/geocoding.py`):** Replaces Google Maps. Implements a robust `city2geocode` function that cascades through Geoapify, Nominatim, Photon, and a hardcoded static dictionary of Indian cities to guarantee a response.
- **`POIManager` (`services/poi_fallback.py`):** Queries OSM using the Overpass API. To prevent rate-limiting (HTTP 429) or unacceptable headers (HTTP 406), it rotates through multiple public Overpass endpoints (Kumi Systems, Main, LZ4) with proper `User-Agent` headers.
- **`document_ingestion.py` (`services/document_ingestion.py`):** Defines `INDIA_KNOWLEDGE_BASE`, a massive list of `Document` objects containing localized travel info. It initializes a `Chroma` vector store using `sentence-transformers`.

## 11. AI/ML Architecture
- **LLM Used:** Google Gemini (via `langchain.chat_models.ChatOpenAI` or native Gemini integrations depending on the specific agent implementation, referencing the `GEMINI_API_KEY`).
- **Why it is used:** Gemini is used for its strong natural language understanding, fast inference, and ability to parse complex JSON structures out of unstructured user text.
- **RAG Architecture:** 
  - The system utilizes a local RAG pipeline to ground the LLM's recommendations in verified travel data, mitigating hallucinations (e.g., suggesting a beach in a landlocked city).
  - Documents are chunked (if large) and embedded using `HuggingFaceEmbeddings`.
  - At runtime, `RetrievalAgent` embeds the user's destination query, performs a similarity search against ChromaDB, and injects the top-K results into the `RecommendAgent`'s context window.

## 12. RAG Pipeline
1. **Data Collection:** Factual data is hardcoded in `document_ingestion.py` as `Document` objects.
2. **Embedding Generation:** Uses `sentence-transformers/all-MiniLM-L6-v2` to create embeddings.
3. **Vector Storage:** Embeddings are persisted locally in `data/vector_db` using ChromaDB.
4. **Retrieval:** `RetrievalAgent` queries the DB using the target city as the search string.
5. **Context Construction:** Retrieved texts are appended to the system prompt of the `RecommendAgent`.
6. **Response Generation:** The LLM curates the final POI list by cross-referencing the retrieved factual data with the live POI data from the OSM cascade.

## 13. API Documentation
- **Geoapify:**
  - **Purpose:** Primary geocoding and reverse geocoding provider.
  - **Endpoint:** `https://api.geoapify.com/v1/geocode/search`
  - **Authentication:** `GEOAPIFY_API_KEY` (freemium).
- **Nominatim (OpenStreetMap):**
  - **Purpose:** Fallback geocoding.
  - **Endpoint:** `https://nominatim.openstreetmap.org/search`
  - **Authentication:** None, but requires a descriptive `User-Agent` header to avoid HTTP 406 errors.
- **Overpass API (OSM):**
  - **Purpose:** Fetching specific points of interest (attractions, restaurants).
  - **Endpoints:** `https://overpass.kumi.systems/api/interpreter`, `https://overpass-api.de/api/interpreter`.
  - **Method:** POST with `data={'data': query}`.
- **RapidAPI (Weather/Fuel):**
  - **Purpose:** Fetching current weather data and regional fuel prices.
  - **Authentication:** `RAPIDAPI_KEY`.

## 14. Data Flow
1. User natural language input arrives at Flask `/api/process`.
2. Passed to `TravelGraph.process_step()`.
3. `ChatAgent` parses entities and updates `self.state['user_info']`.
4. `InformationAgent` uses `GeocodingManager` to get lat/lng, updating state.
5. `RetrievalAgent` pulls context from ChromaDB.
6. `RecommendAgent` merges RAG context, POI data, and Weather API data to yield a JSON array of attractions.
7. Flask returns this to the frontend.
8. User selects attractions (POST to `/api/stream`).
9. `StrategyAgent`, `RouteAgent`, and `BudgetAgent` compute the final itinerary.
10. Streamed back to the UI for rendering.

## 15. Database / Storage Architecture
- **Vector Database:** ChromaDB.
  - **Storage:** Local persistent directory (`data/vector_db/`).
  - **Schema:** Contains embedded document vectors and metadata (source, category, region, destination).
  - **Why:** Selected for its ease of use in local, serverless environments and seamless integration with LangChain.
- **Session Storage:** Flask-Session using local file system (`flask_session/`) to manage user states (`workflows` dictionary) across HTTP requests.

## 16. Environment Variables & Configuration
- `GEMINI_API_KEY`: Required. Used for LLM generation. (e.g., `AQ.Ab8RN...`)
- `RAPIDAPI_KEY`: Required. Used for Weather and Fuel APIs.
- `GEOAPIFY_API_KEY`: Optional but highly recommended. Provides stable, free-tier geocoding.
- `FLASK_SECRET_KEY`: Required. Used for securing Flask sessions. (e.g., `travel-ai-secret`)
- `PYTHONIOENCODING`: Recommended `utf-8` to prevent Unicode errors on Windows.

## 17. Installation & Setup
### Prerequisites
- Python 3.9+
- Git

### Setup
1. Clone the repository.
2. Create a virtual environment: `python -m venv venv`
3. Activate the virtual environment:
   - Windows: `venv\Scripts\activate`
   - Mac/Linux: `source venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`
5. Configure environment variables: Create a `.env` file in the root directory and populate it with the required keys (see Section 16).
6. Initialize the Vector Database (runs automatically on first ingestion/query if configured).

## 18. Running the Project
To run the development server locally:
```bash
python main.py
```
The application will be accessible at `http://127.0.0.1:8000`.

## 19. Deployment
*(No specific deployment configuration like Dockerfiles or CI/CD pipelines are currently implemented in the repository. Standard WSGI deployment via Gunicorn/Waitress is recommended for production.)*

## 20. Security
- **Secret Management:** API keys are managed via the `.env` file and `python-dotenv` and are never hardcoded in the primary logic.
- **Session Security:** Flask sessions are configured with `SESSION_COOKIE_HTTPONLY = True` and `SESSION_COOKIE_SAMESITE = 'Lax'` to mitigate XSS and CSRF.
- **Rate Limiting:** The POI fallback mechanism explicitly implements delays and `User-Agent` rotation to respect OSM's public infrastructure limits.

## 21. Error Handling
- **Geospatial Failures:** If Geoapify fails or hits a rate limit, the system gracefully falls back to Nominatim, then Photon, and finally a static hardcoded dictionary of major Indian cities.
- **POI API Failures:** `poi_fallback.py` implements a `try/except` loop over multiple Overpass endpoints to catch HTTP 429/406 errors.
- **LLM Parse Errors:** Agents are designed to handle non-compliant LLM outputs (e.g., stripping markdown code blocks from JSON strings in `RecommendAgent`).
- **Frontend:** The UI gracefully handles streaming interruptions and API errors by displaying user-friendly alerts.

## 22. Performance Considerations
- **Caching:** The application leverages Flask-Session for state persistence, preventing redundant LLM calls for previously processed steps.
- **API Optimization:** The geospatial cascade prioritizes fast, high-limit providers (Geoapify) before hitting restrictive public APIs (Nominatim).

## 23. Testing
- `pytest` is used for testing.
- Important test files: `test_geocode_resilience.py`, `test_poi_fallback.py` to ensure the API fallback cascades function correctly.
- Run tests using: `pytest`

## 24. Limitations
- The system heavily optimizes for Indian destinations; international travel planning relies on more generic web/LLM knowledge without dense RAG support.
- Public APIs (OSM, Nominatim) have strict rate limits, which may cause latency during high concurrency if a commercial API key (Geoapify) is exhausted.
- ChromaDB runs locally, meaning horizontal scaling requires migrating to a cloud vector database (e.g., Pinecone).

## 25. Future Enhancements
- Migrate local ChromaDB to a managed cloud vector database for production scaling.
- Integrate real-time flight and hotel booking APIs.
- Add user accounts for saving itineraries long-term.
- Implement a Dockerfile for standardized deployment.

## 26. Troubleshooting
- **UnicodeEncodeError on Windows:** Ensure `PYTHONIOENCODING=utf-8` is set in the `.env` file or environment.
- **Overpass HTTP 406 Error:** Ensure the `User-Agent` header in `poi_fallback.py` is descriptive and the payload is sent as `data={'data': query}`.
- **Session State Loss:** Ensure the `flask_session` directory has read/write permissions.

## 27. Development & Maintenance Guide
- **Updating RAG Data:** Add new `Document` objects to the `INDIA_KNOWLEDGE_BASE` array in `services/document_ingestion.py`. Re-run the script to update the ChromaDB store.
- **Adding a Geocoding Provider:** Implement the API call in `services/geocoding.py` within the `GeocodingManager.city2geocode` cascade loop.
- **Modifying Prompts:** Navigate to the specific agent in the `agents/` directory and update the `SystemMessage` or `HumanMessage` templates.

## 28. Conclusion
ExploreX represents a highly resilient, cost-effective approach to AI-driven travel planning. By utilizing a free-tier API cascade and a robust RAG architecture, it provides deep, personalized itineraries without the overhead of enterprise API billing, making it a scalable platform for intelligent travel orchestration.
