# ExploreX - Intelligent Collaborative Agents for Dynamic Personalized Travel Planning

## Overview
ExploreX is an autonomous, multi-agent travel planning system that generates highly personalized, dynamic itineraries. Utilizing Large Language Models (LLMs), a Retrieval-Augmented Generation (RAG) pipeline, and a resilient cascade of free-tier geospatial APIs, ExploreX curates destinations, routes, and budgets based on natural language user preferences.

## Key Features
- **Conversational Planning:** Extract travel preferences seamlessly from natural language chat.
- **100% Free-Tier Architecture:** Built without paid geospatial dependencies (no Google Maps billing). Uses a robust fallback cascade (Geoapify → Nominatim → OSM Overpass).
- **RAG-Powered Accuracy:** Uses a local vector database to ground recommendations in factual, localized travel guides.
- **Weather-Aware:** Dynamically adjusts recommendations based on real-time weather forecasts.
- **Holistic Itineraries:** Automatically generates daily plans, routes, transit methods, and budget estimates, including an honest advisory when a strict budget cannot be met.
- **Guaranteed Imagery:** Attraction cards use provider/Wikipedia/Wikidata/Commons images first, then a relevance-checked Pexels photo. If no verified photo exists, the card still gets a real representative image (category + destination Pexels, a nearby Commons photo, or the destination image) and, as a final guarantee, a generated ExploreX travel card carrying the attraction's name. Representative images are labelled as such; cards with verified photos render first.
- **Nearby Eats:** The attraction modal lists real OSM/Geoapify eateries (restaurants, cafes, food stalls, bakeries) with cuisine, address, a map link and a straight-line (haversine) distance computed from that attraction's own coordinates - never the user's location and never a walking/road estimate.
- **Non-Intrusive UI:** Backend progress never moves the user between tabs; only explicit user actions (tab click, "View Attractions", chat action) change the view.

## Technology Stack
- **Backend:** Flask, Python
- **AI & ML:** LangChain, Google Gemini LLM, HuggingFace Embeddings
- **Vector Database:** ChromaDB
- **Geospatial & APIs:** Geoapify, Nominatim, OpenStreetMap (Overpass API), RapidAPI
- **Frontend:** HTML, CSS, Vanilla JavaScript, Leaflet (Maps)

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

2. **Set up a virtual environment (Python 3.13):**
   ```bash
   # Windows (this repository is verified on CPython 3.13 x64)
   py -3.13 -m venv .venv313
   .\.venv313\Scripts\Activate.ps1
   python --version    # must report Python 3.13.x

   # macOS / Linux
   python3.13 -m venv .venv313
   source .venv313/bin/activate
   ```
   `requirements.txt` is also kept installable on CPython 3.11 (NumPy 2.1+,
   ChromaDB 1.x and the LangChain 0.3 stack all ship wheels for both).

3. **Install dependencies:**
   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Build the local vector database (RAG knowledge base):**
   ```bash
   python services/document_ingestion.py --rebuild
   ```
   ChromaDB's on-disk schema is version-specific. After changing the ChromaDB
   major version (as the Python 3.13 migration does), rebuild it once rather
   than pointing the client at an older `data/vector_db`. The folder is
   gitignored and regenerated from `services/document_ingestion.py`.

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

Start the Flask development server from the Python 3.13 environment:
```powershell
.\.venv313\Scripts\python.exe main.py
# or, with the environment activated:
python main.py
```
Access the application at `http://127.0.0.1:8000`.

Production-style start (Waitress, no debug/reloader):
```powershell
$env:PRODUCTION = "1"
$env:HOST = "127.0.0.1"
$env:PORT = "8000"
.\.venv313\Scripts\python.exe main.py
```

Health check (no credentials or provider calls required):
```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
```

Run the test suite (external providers/LLMs are mocked):
```powershell
.\.venv313\Scripts\python.exe -m pytest tests -q
```

## Usage
1. Open the web interface.
2. Enter a natural language request (e.g., "Plan a 4-day trip to Jaipur for 2 people with a moderate budget").
3. The system will extract your preferences, fetch weather data, and present a curated list of attractions.
4. Select your preferred attractions.
5. ExploreX will compute the optimal daily route, transit options, and overall budget.

## API / External Services
- **Gemini (via the supported `google-genai` SDK):** Core LLM inference.
- **ChromaDB:** Local vector storage for RAG.
- **Geoapify & Nominatim (OSM):** Location validation, geocoding and the primary Places POI source.
- **Overpass API (OSM):** Additional POI coverage (cached, time-budgeted, circuit-broken).
- **Wikipedia / Wikidata:** Optional notability enrichment and verified attraction images.
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
