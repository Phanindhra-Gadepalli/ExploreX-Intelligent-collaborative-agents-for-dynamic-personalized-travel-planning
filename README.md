# ExploreX - Intelligent Collaborative Agents for Dynamic Personalized Travel Planning

## Overview
ExploreX is an autonomous, multi-agent travel planning system that generates highly personalized, dynamic itineraries. Utilizing Large Language Models (LLMs), a Retrieval-Augmented Generation (RAG) pipeline, and a resilient cascade of free-tier geospatial APIs, ExploreX curates destinations, routes, and budgets based on natural language user preferences.

## Key Features
- **Conversational Planning:** Extract travel preferences seamlessly from natural language chat.
- **100% Free-Tier Architecture:** Built without paid geospatial dependencies (no Google Maps billing). Uses a robust fallback cascade (Geoapify → Nominatim → OSM Overpass).
- **RAG-Powered Accuracy:** Uses a local vector database to ground recommendations in factual, localized travel guides.
- **Weather-Aware:** Dynamically adjusts recommendations based on real-time weather forecasts.
- **Holistic Itineraries:** Automatically generates daily plans, routes, transit methods, and budget estimates.

## Technology Stack
- **Backend:** Flask, Python
- **AI & ML:** LangChain, Google Gemini LLM, HuggingFace Embeddings
- **Vector Database:** ChromaDB
- **Geospatial & APIs:** Geoapify, Nominatim, OpenStreetMap (Overpass API), RapidAPI
- **Frontend:** HTML, CSS, Vanilla JavaScript, Folium (Maps)

## Architecture Overview
ExploreX relies on a state machine (`TravelGraph`) to orchestrate a sequence of specialized AI agents:
1. **ChatAgent** - Extracts entities.
2. **InformationAgent** & **RetrievalAgent** - Validates locations, fetches weather, and retrieves factual RAG context.
3. **RecommendAgent** - Curates personalized POIs.
4. **StrategyAgent**, **RouteAgent**, **BudgetAgent** - Formulates the daily itinerary, routes, and cost estimates.

## Project Structure
- `main.py`: Main Flask application entry point.
- `workflows/`: Contains the `TravelGraph` state machine orchestrator.
- `agents/`: Contains specialized AI agents (Chat, Route, Recommend, etc.).
- `services/`: Contains API integrations (Geocoding cascade, POI fetchers, RAG ingestion).
- `data/`: Local storage for the ChromaDB vector database.
- `frontend/`: Web interface assets (HTML, CSS, JS).

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd ExploreX-Intelligent-collaborative-agents-for-dynamic-personalized-travel-planning
   ```

2. **Set up a virtual environment:**
   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # Mac/Linux
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Environment Variables
Create a `.env` file in the root directory. Never commit this file to version control.

```env
GEMINI_API_KEY=your_gemini_api_key_here
RAPIDAPI_KEY=your_rapidapi_key_here
FLASK_SECRET_KEY=your_secure_random_string
# Optional but recommended for stable geocoding
GEOAPIFY_API_KEY=your_geoapify_key_here
PYTHONIOENCODING=utf-8
```

## Running Locally

Execute the following command to start the Flask development server:
```bash
python main.py
```
Access the application in your browser at `http://127.0.0.1:8000`.

## Usage
1. Open the web interface.
2. Enter a natural language request (e.g., "Plan a 4-day trip to Jaipur for 2 people with a moderate budget").
3. The system will extract your preferences, fetch weather data, and present a curated list of attractions.
4. Select your preferred attractions.
5. ExploreX will compute the optimal daily route, transit options, and overall budget.

## API / External Services
- **Gemini (Google Generative AI):** Core LLM inference.
- **ChromaDB:** Local vector storage for RAG.
- **Geoapify & Nominatim (OSM):** Location validation and geocoding.
- **Overpass API (OSM):** POI and attraction fetching.
- **RapidAPI:** Weather and regional fuel data.

## Limitations
- Heavily optimized for Indian tourism; international destinations rely more heavily on zero-shot LLM generation rather than dense local RAG data.
- Public APIs (Nominatim, Overpass) enforce strict rate limits which may impact response times under heavy concurrent load without commercial fallback keys.

## Future Enhancements
- Integration of live flight and hotel booking engines.
- Migration to a cloud-based vector database for horizontal scaling.
- Dockerization for simplified deployment.

## Contributing
Contributions are welcome. Please open an issue first to discuss proposed changes before submitting a pull request. Ensure all tests (`pytest`) pass before submitting.
