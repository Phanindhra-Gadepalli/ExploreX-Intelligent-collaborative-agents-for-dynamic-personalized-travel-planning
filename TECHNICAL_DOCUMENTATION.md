# ExploreX — Technical Documentation

## 1. Project Overview

ExploreX is an intelligent, multi-agent AI travel assistant designed to generate dynamic, personalized itineraries. The application allows users to describe their travel requirements in natural language through a conversational chat interface. By leveraging Large Language Models (LLMs), a Retrieval-Augmented Generation (RAG) pipeline, and a cascade of free geospatial APIs, ExploreX orchestrates the complex task of travel planning—extracting user intent, validating destinations, finding attractions, generating interest-based recommendations, and organizing a trip. While highly optimized for Indian tourism data and state-wide searches (like Kerala), it also supports international queries.

---

## 2. Abstract

Traditional travel planning is fragmented and time-consuming, requiring users to manually coordinate between maps, attraction guides, weather forecasts, and budgeting tools. ExploreX proposes an autonomous, multi-agent orchestrator (`TravelGraph`) that sequentially leverages specialized AI agents (Chat, Information, Retrieval, Recommend, Strategy, Route, Budget, and Communication) to solve this problem. By augmenting LLMs with factual retrieval (RAG) and external geographic services (Nominatim, Overpass API, Pexels), the system curates personalized, context-aware travel recommendations. ExploreX eliminates dependency on expensive, rigid commercial mapping services (like Google Maps) by utilizing a highly resilient cascade of free-tier providers, presenting a unified, dynamic itinerary tailored to the user's explicit interests.

---

## 3. Problem Statement

Planning a personalized trip is inherently difficult because it requires synthesizing vast amounts of disparate information. Traditional travel planning tools suffer from several limitations:
- **Manual Research Overhead:** Users must manually search destinations, cross-reference attractions, and build itineraries.
- **Generic Recommendations:** Most platforms provide one-size-fits-all itineraries that fail to account for a user's specific hobbies, interests, or group demographics (e.g., traveling with children).
- **Geographic Complexity:** Understanding spatial relationships between attractions and planning efficient routes requires specialized mapping tools.
- **Cost Prohibitive Architectures:** Existing automated solutions often rely on paid APIs (e.g., Google Maps, Google Places), making them expensive to run and scale.

ExploreX attempts to solve these issues by providing a unified conversational interface that autonomously handles geographic validation, interest-based recommendation, and spatial planning using cost-effective, open-source data alternatives.

---

## 4. Proposed Solution

ExploreX solves the travel planning problem through an autonomous, state-driven workflow:

User
↓
Natural language input (e.g., "I want to visit Kerala for 3 days with my family and I like beaches.")
↓
Travel information extraction (LLM parses city, days, members, interests)
↓
Travel state (Data is validated; missing info is requested via chat)
↓
Destination detection & validation (Geocoding cascade resolves the destination)
↓
Geographic processing (Bounding boxes and coordinates are generated)
↓
Attraction discovery (OpenStreetMap/Overpass fetches Points of Interest)
↓
Personalized recommendation (AI and heuristic matching filter attractions based on user interests)
↓
User attraction selection (User selects their favorite recommended attractions via the UI)
↓
Travel planning / itinerary (Agents compute routes, transit, and budgets)

---

## 5. Key Features

- **Natural-Language Travel Input:** Users can type their travel requirements naturally. The AI intelligently extracts structured parameters (destination, days, budget, interests).
- **Conversational Missing-Field Detection:** If a user provides an incomplete request, the system detects missing required fields and asks follow-up questions gracefully.
- **Indian State-Wide Search:** Dynamically recognizes Indian states (e.g., "Kerala") and automatically expands the search to encompass major cities and regions within that state.
- **Free-Tier Geospatial Cascade:** Validates destinations and geocodes locations using a resilient, cost-free cascade of providers (Nominatim → Geoapify → Photon → Static Fallback) without requiring Google Maps.
- **Attraction Discovery via OpenStreetMap:** Fetches Points of Interest (POIs) using the Overpass API, rotated across multiple public endpoints to ensure reliability.
- **Personalized Interest-Based Recommendations:** Employs advanced heuristic and substring matching to align user interests with attraction categories, descriptions, and tags, falling back to LLM re-ranking.
- **Popular Attractions:** Displays a secondary list of highly-rated attractions that don't specifically match user interests but are popular in the region.
- **Attraction Image Enrichment:** Automatically fetches high-quality images for attractions using the Pexels API, with an intelligent fallback to category-level searches if specific names yield no results.
- **Interactive Attraction Selection:** Users can visually browse attraction cards, select their favorites, and track their selection count via a reactive frontend UI.
- **Dynamic Session Management:** Maintains user state across the entire conversational session, allowing users to start a "New Trip" and reset context without refreshing the page.

---

## 6. Technology Stack

| Technology | Purpose | Where/How It Is Used |
|---|---|---|
| **Python** | Backend Language | Core application logic, agent implementations, API integrations. |
| **Flask** | Web Framework | Serves the frontend, provides `/api/process` and `/api/stream` endpoints. |
| **JavaScript (Vanilla)** | Frontend Logic | Handles chat UI, attraction selection, EventSource streaming, and map rendering. |
| **HTML5 / CSS3** | Frontend Layout & Styling | UI structure, chat bubbles, attraction cards, animations. |
| **Google Gemini** | AI / LLM | Natural language understanding, entity extraction, recommendation logic. |
| **LangChain** | LLM Orchestration | Framework for chaining prompts and managing the RAG vector store. |
| **ChromaDB** | Vector Database | Stores and retrieves embedded travel documents for the RAG pipeline. |
| **Nominatim / OSM** | Geocoding | Primary free geocoding service for validating destinations. |
| **Geoapify** | Fallback Geocoding | Secondary geocoding provider (requires free API key). |
| **Overpass API** | POI / Attraction Retrieval | Fetches raw spatial data (attractions) from OpenStreetMap. |
| **Pexels API** | Image Enrichment | Fetches high-quality stock photos for attraction cards. |
| **Leaflet** | Maps | Renders interactive maps on the frontend displaying selected attractions. |

---

## 7. Why Each Technology Was Chosen

- **Python & Flask:** Chosen for their simplicity and massive ecosystem for AI integration (LangChain) and data processing.
- **JavaScript/HTML/CSS (Vanilla):** Keeps the frontend lightweight and avoids the overhead of complex build tools (like React or Webpack) while remaining highly interactive.
- **Google Gemini:** Provides fast inference, strong natural language understanding, and highly accurate JSON schema extraction from unstructured text.
- **ChromaDB:** A lightweight, serverless local vector database perfect for rapid prototyping and local RAG retrieval without managing cloud infrastructure.
- **Nominatim & Overpass (OpenStreetMap):** Chosen to completely eliminate dependency on expensive Google Maps billing. They provide comprehensive spatial data globally for free.
- **Leaflet:** A lightweight, open-source mapping library that seamlessly integrates with OSM tiles, avoiding Google Maps SDKs.
- **Pexels API:** Provides a massive library of high-quality, free-to-use stock photos to enrich the UI, essential when OSM data lacks imagery.

---

# 8. Complete System Architecture

User
 ↓
Frontend (HTML/JS/CSS)
 ↓ (HTTP POST / Server-Sent Events)
Backend/API (Flask)
 ↓
Travel State (Session Management)
 ↓
AI Agents (Chat, Information, Strategy, Route, etc.)
 ↓
Geographic Services (Nominatim/Geoapify Cascade)
 ↓
Retrieval (ChromaDB / RAG)
 ↓
POI Provider (Overpass API)
 ↓
Recommendation Engine (Heuristic Matching + LLM)
 ↓
Image Enrichment (Pexels API)
 ↓ (Streamed back)
Frontend (Attraction Rendering & Map)
 ↓
User

The system is orchestrated by a central `TravelGraph` state machine. As the user sends messages, the graph transitions between different agent nodes. Each node performs a specific task (e.g., geocoding, POI fetching, recommendation), updates the shared session `TravelState`, and optionally yields output to be streamed to the frontend.

---

# 9. Frontend Architecture

The frontend is a monolithic Single Page Application (SPA) built with vanilla web technologies, avoiding complex build steps.

- **HTML Structure (`index.html`):** Divided into two main panels. The left panel contains the conversational Chat Interface. The right panel contains the dynamic Content Area (Attractions, Itinerary, Map).
- **CSS Structure (`style.css` / inline):** Utilizes modern CSS variables, flexbox, and grid layouts. Includes animations for chat bubbles and hover states for attraction cards.
- **JavaScript Structure (`main.js`):** 
  - **Chat Interface:** Captures user input, creates chat bubbles, and manages the auto-scrolling chat history.
  - **API Communication:** Uses `fetch` for standard API calls and `EventSource` to listen to Server-Sent Events (SSE) from the backend's `/api/stream` endpoint, allowing real-time UI updates as the backend processes long-running tasks.
  - **Frontend State:** Maintains a local `state` object (tracking `sessionId`, `selected_attractions`, etc.) that mirrors necessary parts of the backend state.
  - **Attraction Interface:** Dynamically generates DOM elements for attraction cards, categorizing them into "Recommended" and "Popular". 
  - **Selected Attractions:** Manages an array of selected attraction IDs. When an attraction is clicked, it toggles its visual state and updates a counter.
  - **Map:** Initializes a Leaflet map and plots markers for selected attractions using OSM tiles.

---

# 10. Backend Architecture

The backend is a Flask application that acts as the bridge between the UI and the AI agents.

- **Application Entry (`main.py`):** Configures Flask, initializes the session interface, and defines routes.
- **Routes:**
  - `GET /`: Serves the `index.html` frontend.
  - `POST /api/chat`: Receives user messages, validates session IDs, and initiates the `TravelGraph` workflow in a background thread.
  - `GET /api/stream`: An SSE endpoint that the frontend connects to. It yields real-time JSON updates from the `TravelGraph` execution queue.
  - `POST /api/new_trip`: Clears the session state to start fresh.
- **State Management:** Uses `flask_session` backed by the local filesystem to persist the `TravelState` object across HTTP requests, ensuring context is maintained.
- **Agents (`agents/`):** Encapsulated modules (e.g., `ChatAgent`, `InformationAgent`) that receive the current state, perform a specific logical step (like querying an LLM or an API), and return an updated state.
- **Services (`services/`):** Helper modules that interact with external systems (Geocoding, POI retrieval, Pexels, ChromaDB) independently of the AI logic.

---

# 11. AI Architecture

ExploreX uses a hybrid AI approach. AI is used strictly for Natural Language Processing (NLP) and decision-making, while factual data (geography, attractions) is retrieved from determinisic external APIs. 

- **Models:** Primarily relies on Google Gemini models via LangChain.
- **Structured Extraction:** The `ChatAgent` uses strict prompting instructions and LangChain output parsers to force the LLM to output user requirements as a structured JSON object.
- **Recommendation Generation:** The LLM is used as a fallback ranking mechanism. If heuristic matching fails to find enough relevant attractions, the LLM evaluates the raw POI list against the user's interests to identify nuanced matches.
- **RAG (Retrieval-Augmented Generation):** Localized travel guides are embedded in ChromaDB. The `RetrievalAgent` fetches relevant paragraphs and injects them into the prompt context to help the LLM make culturally accurate recommendations.
- **Important Distinction:** The AI does *not* hallucinate locations or attractions. It only filters, ranks, and categorizes real-world spatial data provided by OpenStreetMap.

---

# 12. Travel Information Extraction

The `ChatAgent` is responsible for converting free-form natural language into structured data. 

**Fields Extracted:**
- `destination`: The target city or state.
- `days`: Number of days for the trip.
- `members`: Total number of travelers.
- `kids`: Number of children in the group.
- `budget`: The user's budget level (e.g., 'budget', 'moderate', 'luxury').
- `interests`: A list of hobbies or preferences (e.g., ['temples', 'beaches']).
- `start_date`: The intended start date.

**Workflow:**
1. **User Sentence:** "I want to explore Kerala for 3 days with my family and I love beaches."
2. **Structured Extraction:** The LLM parses this and outputs a JSON object.
3. **TravelState:** The parsed values are merged into the session's `user_info` dictionary.
4. **Missing-Field Detection:** The system checks if critical fields (destination, days, members) are present.
5. **Completion:** If fields are missing, it asks the user for them. Otherwise, it proceeds to geographic retrieval.

---

# 13. Travel State / Session State

The `TravelState` is a shared dictionary that persists across the user's session. It acts as the single source of truth for the ongoing workflow.

| Field | Type | Meaning | Example |
|---|---|---|---|
| `session_id` | String | Unique identifier for the user's session. | `"a1b2c3d4..."` |
| `user_info` | Dictionary | Structured extraction of user preferences. | `{"destination": "Kerala", "days": 3, "interests": ["beaches"]}` |
| `missing_info` | List | Required fields the user hasn't provided yet. | `["members"]` |
| `location_data` | Dictionary | Validated geocoding coordinates and bounding boxes. | `{"lat": 10.85, "lng": 76.27, "state": "Kerala"}` |
| `attractions` | List of Dicts | Raw POIs retrieved from Overpass. | `[{"id": "123", "name": "Kochi Beach"...}]` |
| `recommended_attractions` | List of Dicts | Processed and categorized attractions sent to the UI. | `[{"id": "123", "category": "interest_based"...}]` |
| `selected_attractions` | List of Strings | IDs of attractions chosen by the user. | `["123", "456"]` |
| `itinerary` | Dictionary | The final generated daily trip plan. | `{"Day 1": [...]}` |

A "New Trip" action completely resets this state, wiping the dictionary clean.

---

# 14. Conversation Flow

A typical successful interaction follows this sequence:

1. **Page Loads:** Frontend generates a session ID.
2. **Chat Initialized:** Backend welcomes the user.
3. **User Input:** User provides full trip details.
4. **Extraction:** `ChatAgent` parses the details into `user_info`.
5. **Missing Fields Checked:** All required fields are present.
6. **Destination Detected:** `InformationAgent` triggers the geocoding cascade to find the destination's coordinates.
7. **Geographic Processing:** System determines if it's a city or a state (e.g., Kerala).
8. **POI Retrieval:** OpenStreetMap fetches local attractions.
9. **Image Enrichment:** Pexels API fetches images for the attractions.
10. **Recommendations Generated:** System cross-references POIs with user interests.
11. **Attractions Displayed:** Frontend receives the SSE stream and renders the attraction cards.
12. **User Selection:** User clicks cards to select their preferred attractions.
13. **Trip Planning:** User clicks "Generate Itinerary", triggering the Strategy, Route, and Budget agents to finalize the plan.

---

# 15. State Machine / Workflow

The `TravelGraph` uses a node-based state machine to route execution.

**States / Nodes:**
- `chat`: Extracts entities and identifies missing info. Transitions to `information` if complete, else stops to ask user.
- `information`: Geocodes destination, handles state-wide bounding boxes. Transitions to `retrieval`.
- `retrieval`: Fetches RAG documents from ChromaDB. Transitions to `recommend`.
- `recommend`: Fetches POIs, enriches images, matches interests. Yields attractions to frontend. Stops and waits for user selection.
- `strategy`: Evaluates selected attractions and plans daily pacing.
- `route`: Maps logical spatial routes between selected attractions.
- `transit`: Calculates transit times.
- `budget`: Estimates costs.
- `communication`: Summarizes the final itinerary for the chat window.

Infinite loops are prevented by strict conditional edges that only transition forward when dependencies (like missing info or user selection) are completely fulfilled.

---

# 16. Destination Detection

When a user provides a destination, ExploreX must convert that string into actionable geographic coordinates.

- **Detection:** Handled by the `GeocodingManager`.
- **States vs Cities:** If the user specifies an Indian state (e.g., "Kerala"), the system recognizes it as a massive region rather than a single point. 
- **Validation:** Ensures the resolved coordinates actually make sense (e.g., confirming Indian locations fall within India's bounding box).

---

# 17. Kerala / Indian State-Wide Search Architecture

Searching for attractions across an entire state is extremely challenging due to API timeouts and massive bounding boxes. ExploreX implements a specialized architecture for this:

1. **State Identification:** If the destination is identified as an Indian state (e.g., Kerala).
2. **Major Destinations:** The system uses a predefined dictionary of major tourist hubs within that state (e.g., for Kerala: Kochi, Munnar, Alleppey, Wayanad, Trivandrum, Varkala, Thekkady).
3. **Iterative Geocoding:** It individually geocodes each of these major hubs to establish search centers.
4. **Aggregated POI Discovery:** It queries the Overpass API iteratively for each hub, retrieving localized POIs.
5. **Aggregation & Deduplication:** The results from all hubs are combined into a massive list and deduplicated based on coordinates and names.
6. **Recommendation:** This state-wide pool of attractions is then fed into the recommendation engine just like a normal city search.

---

# 18. Geocoding System

**Geocoding** is the process of converting a text address (e.g., "Mumbai") into geographic coordinates (latitude/longitude). ExploreX requires this to know where to search for attractions on the map.

**Provider Cascade:**
To ensure 100% uptime without Google Maps billing, ExploreX utilizes a fallback cascade in `services/geocoding.py`:
1. **Nominatim:** The primary free geocoder powered by OpenStreetMap. It is highly accurate but has a strict 1 request/second rate limit.
2. **Geoapify:** An optional fallback that provides a generous free tier (requires API key).
3. **Photon:** An open-source OSM-based geocoder (opt-in).
4. **Static Table:** A hardcoded dictionary of hundreds of major global and Indian cities. If all network APIs fail, the system can instantly resolve major destinations entirely offline.

The system iterates through this list. If Nominatim times out, it transparently tries Geoapify, ensuring the workflow never breaks.

---

# 19. OpenStreetMap / OSM Architecture

ExploreX relies entirely on OpenStreetMap (OSM) data, circumventing commercial monopolies.
- **OpenStreetMap:** The underlying open-source database of global geographic data.
- **Nominatim:** A search engine for OSM data (used for Geocoding).
- **Overpass API:** A powerful query language API used to extract highly specific data (like all "tourism" nodes within a 5km radius) from OSM. ExploreX uses Overpass to fetch attractions.

ExploreX does not require API keys for OSM services. To handle rate limits responsibly, the `POIManager` rotates through multiple public Overpass endpoints (e.g., Kumi Systems, Main, LZ4) and utilizes strict timeouts and descriptive User-Agents.

---

# 20. POI / Attraction Retrieval

**POI** stands for Point of Interest.

**Pipeline:**
1. **Coordinates Received:** From the Geocoding system.
2. **Overpass Query Generation:** The system builds an Overpass QL query targeting nodes labeled with tags like `tourism=attraction`, `historic=*`, or `waterway=waterfall` within a specific radius of the coordinates.
3. **Network Execution:** `POIManager` executes the query with fallback endpoint rotation.
4. **Normalization:** The raw JSON graph data from Overpass is parsed.
5. **Deduplication:** Attractions with missing names are dropped, and duplicates (nodes very close to each other with the same name) are merged.

---

# 21. Attraction Data Model

The processed attraction objects sent to the frontend have the following schema:

| Field | Type | Required? | Description |
|---|---|---|---|
| `id` | String | Yes | Unique identifier (derived from OSM node ID). Cast to string to prevent frontend null bugs. |
| `name` | String | Yes | The display name of the attraction. |
| `description` | String | No | A short summary of the attraction (if available). |
| `category` | String | Yes | Classification (e.g., "interest_based", "popular", "waterfall"). |
| `lat` | Float | Yes | Latitude for map rendering. |
| `lon` | Float | Yes | Longitude for map rendering. |
| `tags` | Dictionary | No | Raw metadata tags from OSM. |
| `image_url` | String | No | URL to a high-quality image fetched via Pexels. |

---

# 22. Personalized Recommendation System

ExploreX dynamically categorizes the raw POI list into two distinct lists for the UI: **"Recommended Based on Your Interests"** and **"Popular Attractions in the City"**.

**Matching Architecture (`_heuristic_interest_match`):**
1. **User Interest Extraction:** User inputs "I like waterfalls and ancient temples."
2. **Normalization:** The system normalizes these interests, stripping filler words to core concepts: `["waterfall", "temple"]`.
3. **Multi-Field Matching:** The system iterates over every attraction and performs a fuzzy substring search. It looks for the core concepts inside the attraction's `name`, `category`, `description`, and raw OSM `tags` dictionary.
    - *Example:* User interest "waterfall". Attraction: "Athirappilly Waterfalls". The substring match succeeds. The attraction is categorized as `interest_based`.
4. **LLM Fallback:** If heuristic matching yields too few results, the raw POI list is sent to the LLM, which uses deep semantic understanding to identify nuanced matches.
5. **Popular Sorting:** Any remaining high-quality attractions that didn't match the specific interests are categorized as `popular`.

---

# 23. Attraction Selection System

Once attractions are rendered on the frontend, users must select them to build their itinerary.

- **Frontend State:** `main.js` maintains a `selected_attractions` array of string IDs.
- **Selection/Deselection:** Clicking an attraction card triggers `toggleAttractionSelection()`. If the ID is not in the array, it is added (Selection). If it is, it is removed (Deselection).
- **ID Stability:** Backend IDs are strictly cast to strings. This prevents historical bugs where OSM nodes missing IDs were passed as `null`, causing the frontend array logic to break when toggling multiple nulls.
- **UI Updates:** The selection count in the header updates dynamically. Selected cards receive a green border and a checkmark overlay.
- **Validation:** The user can select as many attractions as they want (e.g., 20+ for a state-wide tour). The backend utilizes these exact selected IDs when generating the final itinerary.

---

# 24. Image System

OpenStreetMap provides spatial data, but rarely provides images. ExploreX uses the **Pexels API** to visually enrich the UI.

- **Primary Query:** `poi_fallback.py` attempts to search Pexels using the exact `name` of the attraction.
- **Fallback Query:** Stock photo APIs often fail on highly specific local names. If the primary query returns 0 images, the system automatically falls back to querying the attraction's general `category` (e.g., "temple", "beach", "museum"). This ensures an aesthetically pleasing image almost always loads.
- **Caching:** Image queries are cached using Python's `@lru_cache` to drastically reduce redundant network calls and protect Pexels rate limits.
- **Browser Handling:** If an image URL fails to load client-side (e.g., 404), the HTML `onerror` attribute gracefully swaps it out for a beautiful local static placeholder image without breaking the layout.

---

# 25. Map System

ExploreX includes an interactive map to help users visualize their selections.

- **Map Library:** **Leaflet.js** (Lightweight, open-source mapping library).
- **Tile Provider:** OpenStreetMap standard tile layer.
- **Map Initialization:** The map is initialized in `main.js`.
- **Marker Plotting:** When attractions are rendered, their coordinates (`lat`, `lon`) are added to a map layer.
- **Interactivity:** Clicking an attraction card on the left automatically pans and zooms the map to highlight that specific marker on the right.

---

# 26. RAG Architecture

**RAG (Retrieval-Augmented Generation)** is a technique used to give the LLM access to external, factual knowledge it wasn't trained on.

- **Why ExploreX uses it:** To ensure travel recommendations and descriptions are culturally accurate and up-to-date, minimizing LLM hallucinations.
- **Document Source:** Foundational travel knowledge is stored in `document_ingestion.py`.
- **Retrieval Pipeline:** 
  1. Texts are converted to vector embeddings and stored in **ChromaDB**.
  2. The `RetrievalAgent` queries the database using the user's destination.
  3. The most relevant paragraphs are injected into the prompt context for the `RecommendAgent`.
- **Telemetry Crash Prevention:** ChromaDB telemetry is strictly disabled (`ANONYMIZED_TELEMETRY="False"`) globally to prevent SQLite threading exceptions during execution.

---

# 27. External APIs and Services

| Service/API | Purpose | Required? | Authentication | Fallback |
|---|---|---|---|---|
| **Google Gemini** | Core AI / LLM logic | **Yes** | `GEMINI_API_KEY` | None |
| **Nominatim (OSM)** | Primary Geocoding | No (Enabled by default) | None | Geoapify, Static |
| **Geoapify** | Secondary Geocoding | No | `GEOAPIFY_API_KEY` | Static Table |
| **Overpass API** | POI Retrieval | **Yes** | None | Endpoint Rotation |
| **Pexels API** | Image Retrieval | No (Highly Recommended) | `PEXELS_API_KEY` | Local static images |
| **RapidAPI** | Weather / Fuel | No | `RAPIDAPI_KEY` | Graceful skip |

*Note: ExploreX does not require any paid/billing-dependent services (like Google Maps) to function core features.*

---

# 28. Environment Variables

ExploreX uses a `.env` file in the root directory for configuration.

| Variable | Purpose | Required/Optional |
|---|---|---|
| `GEMINI_API_KEY` | Authenticates with Google Generative AI. | **Required** |
| `PEXELS_API_KEY` | Fetches high-quality attraction images. | Optional |
| `GEOAPIFY_API_KEY` | Enables the Geoapify geocoding fallback. | Optional |
| `RAPIDAPI_KEY` | Enables weather and transit data. | Optional |
| `FLASK_SECRET_KEY` | Secures the Flask session state. | **Required** |
| `PYTHONIOENCODING` | Prevents Windows unicode console errors (`utf-8`). | Optional |

*Never expose actual API keys in source control.*

---

# 29. Project Folder Structure

```
ExploreX/
├── main.py                     # Flask application entry point
├── agents/                     # AI Agent logic
│   ├── chat_agent.py
│   ├── information_agent.py    # Geocoding & heuristic matching
│   ├── recommend_agent.py
│   └── ...
├── services/                   # External API integrations
│   ├── geocoding.py            # Nominatim/Geoapify cascade
│   ├── poi_fallback.py         # Overpass API & Pexels images
│   ├── document_ingestion.py   # ChromaDB RAG setup
│   └── ...
├── workflows/                  
│   └── travel_graph.py         # Core state machine orchestrator
├── frontend/                   # Web interface
│   ├── templates/
│   │   └── index.html          # Main HTML structure
│   └── static/
│       ├── css/style.css
│       ├── js/main.js          # Core frontend logic & UI updates
│       └── images/             # Local fallback images
├── data/
│   └── vector_db/              # Local ChromaDB persistent storage
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (git-ignored)
└── TECHNICAL_DOCUMENTATION.md  # This documentation file
```

---

# 30. Important Classes

### `TravelGraph` (`workflows/travel_graph.py`)
- **Purpose:** Acts as the central nervous system of the backend.
- **Responsibilities:** Maintains the session state, routes execution between different agents, and orchestrates the streaming of events back to the frontend.

### `GeocodingManager` (`services/geocoding.py`)
- **Purpose:** Resolves destinations to coordinates.
- **Responsibilities:** Iterates through the provider cascade (Nominatim -> Geoapify -> Static) until a valid coordinate is found. Caches results to prevent rate limiting.

### `POIManager` (`services/poi_fallback.py`)
- **Purpose:** Retrieves attractions and images.
- **Responsibilities:** Executes complex Overpass QL queries, handles HTTP 429 rate limit fallbacks by rotating endpoints, normalizes JSON results, and triggers Pexels image fetching.

---

# 31. Important Functions

- **`_heuristic_interest_match(interests, attractions)`** (`information_agent.py`): The core algorithm that parses normalized user interests and fuzzy-matches them against attraction fields (name, category, description, tags) to generate highly personalized recommendations.
- **`fetch_image_for_poi(poi_name, category)`** (`poi_fallback.py`): Queries the Pexels API. Automatically falls back to the `category` string if the specific `poi_name` returns zero images. Heavily cached via `@lru_cache`.
- **`toggleAttractionSelection(attractionId)`** (`main.js`): The frontend workhorse for the UI. Manages the `selected_attractions` array, updates the visual state of the cards, and updates the selection counter.

---

# 32. API Endpoints

| Method | Endpoint | Purpose | Input | Output |
|---|---|---|---|---|
| `GET` | `/` | Serves the main application UI. | None | HTML Page |
| `POST`| `/api/chat` | Receives user messages and initializes the workflow in the background. | JSON (`{"message": "hi", "session_id": "123"}`) | JSON Status (`{"status": "processing"}`) |
| `GET` | `/api/stream` | Server-Sent Events (SSE) endpoint. Yields real-time updates from the background workflow. | Query Param (`?session_id=123`) | SSE Event Stream (JSON objects) |
| `POST`| `/api/new_trip` | Wipes the current session state to start fresh. | JSON (`{"session_id": "123"}`) | JSON Status (`{"status": "cleared"}`) |

---

# 33. Data Flow

### Chat & Extraction
User Types Message → `fetch(/api/chat)` → Flask → `TravelGraph` Queue → `ChatAgent` → LLM parses message → Updates `TravelState` → Flask yields response → `EventSource(/api/stream)` → Frontend renders Chat Bubble.

### Attractions & Images
`InformationAgent` → `GeocodingManager` (Coordinates) → `POIManager` → Overpass API (Raw POIs) → Normalization → `Pexels API` (Images) → `_heuristic_interest_match` → Updated `TravelState` → Flask yields attractions → Frontend builds DOM Cards.

### Selection
User clicks card → `main.js` `toggleAttractionSelection()` → Card visually updates → ID added to `selected_attractions` array → Array sent in subsequent `/api/chat` requests for itinerary generation.

---

# 34. Error Handling and Fallback Architecture

ExploreX prioritizes graceful degradation to ensure a seamless user experience.

- **Geocoding Failures:** If Nominatim times out, it transparently falls back to Geoapify. If offline, it falls back to a hardcoded dictionary of 300+ major cities.
- **POI Provider Failures:** Overpass is prone to rate limits (HTTP 429). The system catches this and instantly retries the query against a secondary public endpoint.
- **Image Failures:** If Pexels returns 0 results for a name, it queries the category. If the API completely fails, it returns a placeholder URL. If the browser fails to load that URL, the HTML `onerror` attribute swaps it to a local static image file.
- **AI Failures:** LLM outputs are wrapped in LangChain output parsers. If the LLM generates malformed JSON, the parser automatically attempts to fix it or falls back safely.
- **Frontend Errors:** If the SSE stream disconnects, the frontend displays a localized error message in the chat window rather than crashing the page.

---

# 35. Historical Bug Fixes and Improvements

*(Note: These are documented for historical context. These issues are fully resolved in the current architecture).*

- **Infinite Recommendation Loop:** The `TravelGraph` previously got stuck in a loop between `recommend` and `information` states. *Fix:* Restructured the conditional edges to ensure transitions only occur when data requirements are explicitly met.
- **UI Disappearance (Missing DOM elements):** A bug caused the entire chat interface to disappear due to missing Javascript functions (`updateAttractions`). *Fix:* Restored the core DOM manipulation functions in `main.js`.
- **Selection Array Null Bug:** OpenStreetMap occasionally generated POIs without IDs. The frontend assigned these as `null`. Selecting multiple `null` cards caused the array logic to break, preventing selections beyond ~13 items. *Fix:* Enforced string-casting on all IDs in the backend and added strict null-checks in the frontend array toggler.
- **Telemetry Crash:** ChromaDB telemetry caused fatal SQLite thread errors during queries. *Fix:* Globally enforced `ANONYMIZED_TELEMETRY=False` before initialization.
- **Google Maps Dependency Removal:** The project originally required paid Google Maps APIs. *Fix:* Entirely replaced the geocoding and POI pipelines with the Nominatim/Overpass cascade.

---

# 36. Security

- **Environment Variables:** All API keys and secrets (Flask Secret Key) are loaded via `.env` files and are excluded from version control (`.gitignore`).
- **Session Security:** Flask sessions store data server-side or in secure local files, preventing clients from directly manipulating the `TravelState` object.
- **Input Validation:** User input from the frontend is sanitized by the LLM and strict JSON parsing schemas before hitting any backend execution logic.

---

# 37. Performance Considerations

- **Asynchronous Streaming:** The heavy lifting (API calls, LLM inference) runs in a background thread. Results are streamed to the UI via Server-Sent Events (SSE), preventing HTTP request timeouts and keeping the UI responsive.
- **API Caching:** Expensive operations like Geocoding and Pexels image fetches are cached in-memory (`@lru_cache` and dictionary caches). This drastically speeds up iterative testing and respects third-party rate limits.
- **Lazy Image Fallbacks:** The two-stage Pexels query (Name -> Category) ensures network bandwidth isn't wasted on continuous failed highly-specific queries.

---

# 38. API Rate Limits and Responsible Usage

ExploreX is built to respect the public infrastructure it relies on:
- **Nominatim (OSM):** Strictly limited to 1 request per second. ExploreX caches results heavily to avoid duplicate queries for the same city.
- **Overpass (OSM):** Implements dynamic endpoint rotation and robust timeouts (8-15 seconds) to prevent overwhelming a single public server.
- **Pexels:** Limited to 200 requests per hour. The `@lru_cache` prevents duplicate image queries for common categories.

---

# 39. Installation and Setup

**Prerequisites:** Python 3.9+ and Git.

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd ExploreX
   ```
2. **Create a Virtual Environment:**
   ```bash
   # Windows
   python -m venv venv
   venv\Scripts\activate
   
   # Mac/Linux
   python3 -m venv venv
   source venv/bin/activate
   ```
3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure Environment:**
   Create a `.env` file in the root directory and add:
   ```env
   GEMINI_API_KEY=your_gemini_key_here
   FLASK_SECRET_KEY=super_secret_string
   PEXELS_API_KEY=your_pexels_key_here
   ```
5. **Start the Application:**
   ```bash
   python main.py
   ```
6. **Access the UI:**
   Open a browser and navigate to `http://127.0.0.1:8000`.

---

# 40. Configuration Guide

- **Required Setup:** You MUST provide a `GEMINI_API_KEY` for the AI to function, and a `FLASK_SECRET_KEY` for session security.
- **Free Configuration:** The geocoding and map systems work perfectly out of the box using OpenStreetMap. No Google Maps keys are required.
- **Optional visual upgrades:** Adding a free `PEXELS_API_KEY` will drastically improve the visual quality of the attraction cards. Adding a free `GEOAPIFY_API_KEY` increases geocoding resilience.

---

# 41. How to Run

From the root of the project, simply execute:
```bash
python main.py
```
The Flask server will start and indicate the port (default 8000).

---

# 42. How the Application Works — Complete Example

**User Input:** "My name is Nithin and I want to explore Kerala for 7 days with 8 members. I like waterfalls and beaches."

1. **Extraction:** The `ChatAgent` identifies `destination: Kerala`, `days: 7`, `members: 8`, and `interests: ["waterfalls", "beaches"]`. All required fields are present.
2. **Geocoding:** The system recognizes "Kerala" as a state. It geocodes major hubs (Kochi, Munnar, Alleppey) via Nominatim.
3. **POI Retrieval:** Overpass fetches hundreds of attractions across these hubs.
4. **Recommendation:** `_heuristic_interest_match` scans the POIs. "Athirappilly Waterfalls" matches "waterfall". "Marari Beach" matches "beaches". These are flagged as `interest_based`.
5. **Image Enrichment:** Pexels fetches images for these specific locations.
6. **Frontend Stream:** The UI receives the SSE events, rendering the chat response and beautiful attraction cards.
7. **User Action:** The user selects their favorite cards and asks to generate the itinerary.

---

# 43. Example of Recommendation Flow

**User Interests:** `["historic architecture", "nature"]`

**System Normalization:** The system strips complex phrasing, reducing interests to core keywords: `["historic", "architecture", "nature"]`.

**Attraction A:** `name: "Fort Kochi", tags: {"historic": "yes", "tourism": "museum"}`
- *Match:* The keyword "historic" is found in the tags. Categorized as `interest_based`.

**Attraction B:** `name: "Lulu Mall", description: "Large shopping center"`
- *No Match:* Keywords not found. The LLM also rejects it. If it is highly rated, it falls into the `popular` category instead.

---

# 44. Testing

Testing can be performed manually via the browser or using the included test scripts.
- **Basic Startup:** Run `python main.py` and ensure the UI loads without console errors.
- **Workflow Simulation:** Run `python scratch/test_kerala.py` to programmatically simulate a complete multi-step conversation bypassing the frontend, verifying that extraction, geocoding, retrieval, and recommendation states execute successfully without loops.
- **API Resilience:** Run `python test_poi_fallback.py` to ensure the Overpass fallback logic successfully fetches attractions.

---

# 45. Troubleshooting

- **Application crashes on startup (ChromaDB errors):** Ensure `ANONYMIZED_TELEMETRY=False` is set. Delete the `data/vector_db` folder to force a clean database rebuild.
- **No attractions load:** Check your terminal. Overpass API may be temporarily down or rate-limiting you. Wait 60 seconds and try again.
- **Images are blank/broken:** You are missing the `PEXELS_API_KEY` in your `.env`, or you have exhausted your hourly rate limit.
- **Map fails to load:** Ensure you have an active internet connection to download the open-source Leaflet tiles.
- **UnicodeEncodeError in Terminal:** Set `PYTHONIOENCODING=utf-8` in your environment variables.

---

# 46. Maintenance Guide

- **Modifying AI Prompts:** Locate the specific agent in `agents/` (e.g., `chat_agent.py`) and modify the `SystemMessage` string block.
- **Changing Map Providers:** Modify the map initialization code in `frontend/static/js/main.js` to point to a different tile server URL.
- **Adjusting Recommendation Logic:** Edit `_heuristic_interest_match` in `agents/information_agent.py` to add new fuzzy matching rules or adjust keyword normalization.
- **Updating RAG Data:** Add new text content to `INDIA_KNOWLEDGE_BASE` in `services/document_ingestion.py`.

---

# 47. Extension / Future Improvements

Based on the current highly modular architecture, Future Improvements could include:
- **Hotel & Flight Integration:** Adding agents to query real-time booking APIs (e.g., Amadeus or Skyscanner).
- **Persistent User Accounts:** Swapping local `flask_session` storage for a robust database (like PostgreSQL) to allow users to save and share itineraries permanently.
- **Cloud Vector Database:** Migrating from local ChromaDB to Pinecone for faster, serverless production deployments.
- **Advanced Route Optimization:** Implementing the Traveling Salesperson Problem (TSP) algorithms in the `RouteAgent` for mathematically optimal daily routing.

---

# 48. Limitations

- **Rate Limits:** Because the system leverages free public APIs (Nominatim, Overpass), rapid concurrent usage by many users may trigger temporary IP bans or timeouts.
- **International Granularity:** While international cities work, the system and its RAG database are heavily optimized and populated for Indian tourism.
- **Local Database Scaling:** ChromaDB is currently running as a local SQLite instance, making horizontal scaling across multiple servers difficult without migration.

---

# 49. Glossary

- **AI (Artificial Intelligence):** Systems capable of tasks that typically require human intelligence.
- **LLM (Large Language Model):** AI trained on vast amounts of text (e.g., Google Gemini) used to understand and generate human language.
- **RAG (Retrieval-Augmented Generation):** Giving an LLM access to a specific database of facts to prevent it from guessing or hallucinating answers.
- **POI (Point of Interest):** A specific location (like a museum, beach, or restaurant) on a map.
- **Geocoding:** Converting a text address (like "Kochi") into map coordinates (Latitude/Longitude).
- **OSM (OpenStreetMap):** A free, editable map of the whole world, used as an alternative to Google Maps.
- **SSE (Server-Sent Events):** A web technology that allows the backend to stream live updates to the frontend without the browser needing to constantly refresh.

---

# 50. Final Project Summary

ExploreX is a powerful demonstration of how orchestrated AI agents can solve complex, multi-step consumer problems. By chaining LLM natural language understanding with resilient, free-tier geospatial services (OpenStreetMap, Pexels), ExploreX successfully transforms free-form user desires into structured, visually appealing, and highly personalized travel itineraries. The architecture proves that intelligent, dynamic travel planning can be achieved efficiently without relying on rigid logic or expensive commercial mapping monopolies.
