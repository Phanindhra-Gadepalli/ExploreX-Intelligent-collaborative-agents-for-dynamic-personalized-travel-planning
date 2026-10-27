# ExploreX — Technical Documentation

## 1. Project Overview

ExploreX is an intelligent, multi-agent AI travel assistant designed to generate dynamic, personalized itineraries. The application allows users to describe their travel requirements in natural language through a conversational chat interface. By leveraging Large Language Models (LLMs), a Retrieval-Augmented Generation (RAG) pipeline, and a cascade of free geospatial APIs, ExploreX orchestrates the complex task of travel planning—extracting user intent, validating destinations, finding attractions, generating interest-based recommendations, and organizing a trip. While highly optimized for Indian tourism data and state-wide searches (like Kerala), it also supports international queries.

---

## 2. Abstract

Traditional travel planning is fragmented and time-consuming, requiring users to manually coordinate between maps, attraction guides, weather forecasts, and budgeting tools. ExploreX proposes an autonomous, multi-agent orchestrator (`TravelGraph`) that sequentially leverages specialized AI agents (Chat, Information, Retrieval, Recommend, Strategy, Route, Budget, and Communication) to solve this problem. By augmenting LLMs with factual retrieval (RAG) and external geographic services (Nominatim/Geoapify geocoding, Overpass API, Wikipedia), the system curates personalized, context-aware travel recommendations with verified imagery and honest provider disclosure. ExploreX eliminates dependency on expensive, rigid commercial mapping services (like Google Maps) by utilizing a highly resilient cascade of free-tier providers, presenting a unified, dynamic itinerary tailored to the user's explicit interests.

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
- **Attraction Discovery via OpenStreetMap:** Fetches Points of Interest (POIs) through a resilient Geoapify → Overpass → Wikipedia cascade. Overpass is rotated across public mirrors, cached, time-budgeted and protected by a circuit breaker, and a failed mirror never discards the Geoapify/Wikipedia results.
- **Personalized Interest-Based Recommendations:** Uses a shared, plural-aware interest matcher over names, categories, descriptions and raw OSM tags, then a single LLM re-rank; genuine matches are labelled `interest_based`, verifiable non-matches `discovery`, and everything else `outside_interests`.
- **Broad, City-Balanced Discovery:** The discovery pool is duration-aware (60–180 candidates) and balanced across every searched city, so a state-wide request is not dominated by one town and is not truncated at an arbitrary top 50.
- **Guaranteed Attraction Images:** Preserves provider images and resolves Wikipedia/Wikidata/Commons photos when the article/file title and coordinates verify the place. If none verifies, a relevance-checked Pexels photo is tried, then a representative fallback (Pexels category + destination, Commons text/nearby search, or the destination's Wikipedia image). As a final guarantee, a generated ExploreX travel card carrying the attraction name is used, so no displayed attraction has an empty image area. Representative/generated images are flagged and labelled.
- **Interactive Attraction Selection:** Users can visually browse attraction cards (12 at a time with "Load more"), select their favorites, and track their selection count via a reactive frontend UI.
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
| **Geoapify Places** | Primary POI Source | Supplies attraction POIs (and accommodation/restaurant POIs) with coordinates and categories. |
| **Wikipedia / Wikidata** | Notability + Images | Optional enrichment: notable-place discovery, notability markers and verified lead images. |
| **Pexels API** | Attraction/hospitality Images (optional) | Verified stock photo fallback after provider/Wikimedia sources, plus representative category + destination photos; also used for restaurant and accommodation cards. |
| **Leaflet** | Maps | Renders interactive maps on the frontend displaying selected attractions. |

---

## 7. Why Each Technology Was Chosen

- **Python & Flask:** Chosen for their simplicity and massive ecosystem for AI integration (LangChain) and data processing.
- **JavaScript/HTML/CSS (Vanilla):** Keeps the frontend lightweight and avoids the overhead of complex build tools (like React or Webpack) while remaining highly interactive.
- **Google Gemini:** Provides fast inference, strong natural language understanding, and highly accurate JSON schema extraction from unstructured text.
- **ChromaDB:** A lightweight, serverless local vector database perfect for rapid prototyping and local RAG retrieval without managing cloud infrastructure.
- **Nominatim & Overpass (OpenStreetMap):** Chosen to completely eliminate dependency on expensive Google Maps billing. They provide comprehensive spatial data globally for free.
- **Leaflet:** A lightweight, open-source mapping library that seamlessly integrates with OSM tiles, avoiding Google Maps SDKs.
- **Pexels API (optional):** Supplies photos for restaurant and accommodation cards, and is the first representative attraction fallback. Verified attraction images still require the photo text to name the place; if none exists, a category + destination Pexels photo is accepted and labelled representative. Without Pexels, Commons/destination images and generated travel cards still guarantee a visual.

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
POI Providers (Geoapify + Overpass + Wikipedia)
 ↓
Normalization / Dedup / Score
 ↓
Recommendation Engine (Interest Matching + LLM re-rank)
 ↓
Image Enrichment (provider/Wikimedia first, relevance-checked Pexels fallback)
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
  - **Attraction Interface:** Dynamically generates DOM elements for attraction cards, split into "Recommended Based on Your Interests", "More Notable Places to Discover" and a clearly separated out-of-interest remainder. 
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
  - `POST /api/reset`: Clears the session state and returns a fresh `session_id` for a new trip.
  - `GET /api/state?session_id=...`: Returns the persisted state for a session, including `current_step`, so a reload restores the workflow instead of restarting it.
  - `GET /api/attraction-image?name=&city=&country=&lat=&lng=`: Resolves one attraction image and always returns a renderable `image_url` (verified where possible, otherwise a representative image or generated travel card), plus `source` and `image_is_representative`.
  - `GET /api/attraction-details`, `POST /api/email`: On-demand Wikipedia-backed details and the trip email.
  - `GET /api/nearby/<lat,lng>?radius=1000`: Returns eateries around the **selected attraction's** coordinates (not the user's). `radius` is capped to 100-5000 m. Each result carries `latitude`, `longitude`, `distance_m`, `distance_type: "straight_line"` (haversine), address/cuisine when provided and an OpenStreetMap link. Overpass is tried first through the shared cache/throttle/circuit breaker; Geoapify is the fallback. The endpoint works for guests and keeps the existing `restaurants` key.
  - `GET /api/assistant`: Returns the server-recorded workflow progress used by the assistant/journey UI.
- **State Management:** Uses `flask_session` backed by the local filesystem to persist the `TravelState` object across HTTP requests, ensuring context is maintained.
- **Agents (`agents/`):** Encapsulated modules (e.g., `ChatAgent`, `InformationAgent`) that receive the current state, perform a specific logical step (like querying an LLM or an API), and return an updated state.
- **Services (`services/`):** Helper modules that interact with external systems (Geocoding, POI retrieval, verified images, ChromaDB) independently of the AI logic.

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
8. **POI Retrieval:** Geoapify, Overpass (cached, time-budgeted, circuit-broken) and optional Wikipedia notability return POIs; malformed records are dropped and the survivors are deduplicated.
9. **Image Enrichment:** Provider images are preserved and Wikipedia/Commons images are resolved only when verified. Every remaining displayed attraction receives an explicitly representative image (Pexels category + destination, Commons text/nearby, or destination image) or a generated travel card, so no card is blank.
10. **Recommendations Generated:** System labels POIs `interest_based` / `discovery` / `outside_interests` after a single LLM re-rank.
11. **Attractions Displayed:** Frontend receives the SSE stream and renders the attraction cards (12 at a time, lazy images, branded travel-card fallback on failure). It stays on the current tab and offers an explicit "View Attractions" action.
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
| `category` | String | Yes | Classification reported by the provider (e.g., "beach", "temple", "viewpoint"). |
| `lat` | Float | Yes | Latitude for map rendering. |
| `lon` | Float | Yes | Longitude for map rendering. |
| `tags` | Dictionary | No | Raw metadata tags from OSM. |
| `image_url` | String | Yes | A provider/Wikipedia/Commons image, a relevance-checked or representative Pexels photo, or a generated ExploreX travel card. Every displayed attraction has one. |
| `image_is_representative` | Boolean | No | `true` when the image is representative/generated rather than a verified photo of the exact attraction. |

---

# 22. Personalized Recommendation System

ExploreX categorizes the ranked POI list with three canonical `recommendation_type` values: **`interest_based`**, **`discovery`** and **`outside_interests`**. The first two are shown as "Recommended Based on Your Interests" and "More Notable Places to Discover"; genuine mismatches are not presented as interest matches.

**Matching architecture (`services/interest_match.py`, shared by `InformationAgent` and `RecommendAgent`):**
1. **User interest extraction:** "I like beaches, hill stations and viewpoints."
2. **Normalization:** aliases and plurals collapse to canonical groups (`nature`, `mountains`, `viewpoints`).
3. **Evidence matching:** every attraction is matched against its name, category, description and raw OSM tags with word-boundary and plural-aware rules.
    - *Example:* "Kovalam Beach" matches the `nature` group; "Munnar Hill Station" matches `mountains`; "Kallimali View Point" matches `viewpoints`.
4. **Honest labeling:** a matched attraction is `interest_based`. A verifiable notable place (Wikipedia/Wikidata) that does not match a hobby is a `discovery` — it broadens the trip without pretending to match. Anything else is `outside_interests` and is not counted as a recommendation.
5. **Breadth:** the discovery pool is duration-aware (at least 60 candidates for a 5-day trip, up to 180) and balanced across searched cities; ranking no longer truncates at an arbitrary top 50.

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

OpenStreetMap and Geoapify provide spatial data but rarely provide images. ExploreX resolves verified provider/Wikimedia images first, then a relevance-checked Pexels photo, and finally a representative/generated fallback so every displayed attraction has a visual.

- **Existing provider image:** A URL reported by Wikipedia/Geoapify is normalized (`https`, protocol-relative URLs repaired, unsafe schemes rejected) and preserved through deduplication.
- **Exact identifiers (batch):** OSM `image` / `wikimedia_commons` / `wikipedia` / `wikidata` tags and Geoapify's wiki fields are trusted. The resolver then batches Wikipedia `titles` lookups, Wikidata `P18`, and Commons files/categories. Exact identifiers do not depend on name guessing.
- **Wikipedia GeoSearch (batch):** Remaining POIs are grouped by real coordinate centroids (not rounded one-decimal points), with one `generator=geosearch` request per cluster. A page is accepted only when its title matches and its coordinates are within 3 km; a page without coordinates needs an exact title match. A place whose name is only a generic type (`Temple`, `CSI Church`, `Beach`) can match only an article with that exact title, and a town article is never attached to a place that merely contains the town name (`LFGHS Munnar` does not get `Munnar`'s photo).
- **Wikipedia name search:** A bounded fallback at one request per second. The article title must match and the article must be within 5 km of the attraction (or have an exact title when coordinates are absent).
- **Commons GeoSearch (batch fallback):** For places still missing a photo, a Commons file is accepted only when it is geotagged within 300 m **and** its filename shares a distinctive name token. Generic filenames (`Temple.jpg`, `Church 02.jpg`), city-only matches (`Munnar 4.jpg` for a school in Munnar) and distant photos are rejected. This stage is capped at eight cluster requests and runs last because it is request-hungry; disable it with `ATTRACTION_IMAGE_COMMONS_GEOSEARCH=0`.
- **Caching and limits:** Positive and negative results use TTLs. Batch APIs are preferred over one request per POI. The per-enrichment search budget defaults to `max(24, min(120, batch size))` (`ATTRACTION_IMAGE_MAX_LOOKUPS` overrides it). A 429 pauses only the search stage for a short cooldown and is not cached as a negative, so a later enrichment can retry. The first verified match wins: a later stage never overwrites an image that an exact identifier already supplied. Set `ATTRACTION_IMAGE_LOOKUP=0` to disable all network lookups (the test suite does).
- **Pexels verified fallback:** Only places that no provider/Wikimedia stage could resolve are sent to Pexels, with attraction-specific `name + city/state` queries. A verified candidate is accepted only when its `alt` text or photo slug contains the attraction's distinctive name tokens; common names (`St Mary's Church`, `Victoria Memorial`, `Central Park`) also require a city/state/country token. The same photo is never reused for two attractions. Successful mappings and definitive empty results are cached separately from Wikimedia answers; timeouts, HTTP 401/403, HTTP 429 and invalid payloads are reported and never cached as permanent misses. It has its own call budget (`ATTRACTION_IMAGE_PEXELS_MAX_CALLS`) and deadline (`ATTRACTION_IMAGE_PEXELS_DEADLINE`) and can be disabled with `ATTRACTION_IMAGE_PEXELS=0`.
- **Guaranteed fallback chain:** If no verified or name-matched photo exists, the resolver tries (1) a bounded Commons text search for `name + city`, preferring files that match both the city and the place type, (2) a category + destination Pexels photo, (3) the nearest geotagged Commons photo, (4) the destination's Wikipedia image, and (5) a deterministic ExploreX travel card (inline SVG). Results from steps 1-4 set `image_is_representative=true`; step 5 additionally sets `image_source="generated"`. Every displayed attraction therefore has an `image_url`.
- **No image found:** This state no longer occurs for enriched cards. If a remote URL later fails in the browser, `onerror` swaps once to a branded ExploreX travel card. The Explore grid renders verified photos first and representative/generated cards afterwards, while the underlying recommendation order and auto-selection are unchanged.
- **Browser handling:** Images use `loading="lazy"`, `decoding="async"` and a safe `onerror` fallback. The UI accepts both `https://` URLs and inline SVG data URIs and labels representative images.
- **On-demand endpoint:** `GET /api/attraction-image?name=&city=&country=&lat=&lng=` returns a renderable image URL, the supplying `source`, `image_is_representative`, and resolver diagnostics.

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
| **Geoapify** | Geocoding + Places POIs (attractions, accommodation, food) | No | `GEOAPIFY_API_KEY` | Static Table (geocoding) / Overpass + Wikipedia (POIs) |
| **Overpass API** | POI Retrieval | No | None | Endpoint rotation, 30-min cache, circuit breaker, Geoapify results kept |
| **Wikipedia / Wikidata** | Notability + verified images | No | None | Skipped quickly on HTTP 429; remaining places continue to the Pexels fallback |
| **Pexels API** | Representative attraction images; restaurant/accommodation images | No | `PEXELS_API_KEY` | Verified stage rejects unrelated photos; representative stage is labelled. Without a key, Commons/destination/generated fallbacks still guarantee a visual |
| **RapidAPI** | Weather / Fuel | No | `RAPIDAPI_KEY` | Graceful skip |

*Note: ExploreX does not require any paid/billing-dependent services (like Google Maps) to function core features.*

---

# 28. Environment Variables

ExploreX uses a `.env` file in the root directory for configuration.

| Variable | Purpose | Required/Optional |
|---|---|---|
| `GEMINI_API_KEY` | Authenticates with Google Gemini through the supported `google-genai` SDK. | **Required** |
| `PEXELS_API_KEY` | Enables the relevant-image fallback for attractions and images for restaurants/accommodations. | Optional |
| `GEOAPIFY_API_KEY` | Enables Geoapify geocoding and the primary Places POI source. | Optional |
| `RAPIDAPI_KEY` | Enables weather and transit data. | Optional |
| `FLASK_SECRET_KEY` | Secures the Flask session state. | **Required** |
| `PYTHONIOENCODING` | Prevents Windows unicode console errors (`utf-8`). | Optional |

*Never expose actual API keys in source control.*

Provider/image resilience can be tuned without code changes:

| Variable | Default | Effect |
|---|---|---|
| `ATTRACTION_IMAGE_LOOKUP` | `1` | `0` disables all image network lookups (the test suite sets this). |
| `ATTRACTION_IMAGE_MAX_LOOKUPS` | `max(24, min(120, batch))` | Per-enrichment Wikipedia-search budget; batch stages are not charged to it. |
| `ATTRACTION_IMAGE_MIN_INTERVAL` | `0.5` | Global seconds between image API requests. |
| `ATTRACTION_IMAGE_SEARCH_INTERVAL` | `1.0` | Minimum seconds between `generator=search` requests (the rate-limited endpoint). |
| `ATTRACTION_IMAGE_TIMEOUT` | `5` | Per-request timeout (seconds). |
| `ATTRACTION_IMAGE_RATE_LIMIT_COOLDOWN` | `10` | Seconds to pause the search stage after HTTP 429. |
| `ATTRACTION_IMAGE_COMMONS_GEOSEARCH` | `1` | `0` disables the Commons GeoSearch and nearby representative fallbacks. |
| `ATTRACTION_IMAGE_GUARANTEE` | `1` | `0` disables the representative/generated guarantee (restores `null` for unresolved cards). |
| `ATTRACTION_IMAGE_PEXELS` | `1` | `0` disables the Pexels attraction fallback. |
| `ATTRACTION_IMAGE_PEXELS_MAX_CALLS` | `40` | Maximum Pexels requests per enrichment. |
| `ATTRACTION_IMAGE_PEXELS_TIMEOUT` | `5` | Per-request Pexels timeout (seconds). |
| `ATTRACTION_IMAGE_PEXELS_DEADLINE` | `30` | Wall-clock ceiling (seconds) for the Pexels stage. |
| `ATTRACTION_IMAGE_PEXELS_PER_PAGE` | `15` | Candidate photos requested per query (clamped 5-40). |
| `ATTRACTION_IMAGE_DEADLINE` | `120` | Soft wall-clock ceiling (seconds) for the search + Commons + Pexels stages; `0` disables it. Batch identifier stages always run. |
| `ATTRACTION_IMAGE_CACHE_TTL` / `_NEGATIVE_TTL` | `604800` / `3600` | Positive / negative cache lifetime. |
| `WIKIPEDIA_ENABLED` | `1` | Enables Wikipedia notability enrichment. |
| `OVERPASS_TIMEOUT` / `OVERPASS_CONNECT_TIMEOUT` | `25` / `5` | HTTP timeouts. |
| `OVERPASS_MAX_SECONDS` | `40` | Wall-clock budget for one Overpass query. |
| `OVERPASS_BREAKER_FAILURES` / `_SECONDS` | `2` / `90` | Circuit-breaker trip threshold and open window. |
| `OVERPASS_ENDPOINTS` | built-in mirrors | Comma-separated override of the Overpass mirror list. |

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
│   ├── poi_fallback.py         # Geoapify/Overpass/Wikipedia POI discovery + normalisation
│   ├── attraction_images.py    # Verified attraction images (cache/throttle/budget)
│   ├── interest_match.py       # Shared plural-aware interest matcher
│   ├── chat_extract.py         # Offline deterministic chat-field fallback
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
│       └── images/             # Static assets (cards use an inline SVG travel-card fallback)
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
- **Purpose:** Retrieves and normalises attractions, accommodations and restaurants.
- **Responsibilities:** Queries Geoapify Places, Overpass (rotated mirrors, 30-minute successful-query cache, 40-second wall-clock budget, circuit breaker) and optional Wikipedia notability. Normalises malformed provider payloads defensively (numeric names, non-string fields, missing coordinates are dropped rather than crashing), deduplicates across providers, and reports per-provider status through `get_provider_status()`.

### `AttractionImageResolver` (`services/attraction_images.py`)
- **Purpose:** Attach verified images where possible and guarantee a renderable image for every displayed attraction.
- **Responsibilities:** Normalises provider URLs; uses exact OSM/Geoapify wiki identifiers; batches Wikipedia titles, Wikidata `P18`, Commons files/categories, Wikipedia GeoSearch and (as a capped fallback) Commons GeoSearch. Every verified name-based match needs a title/name match plus a tight coordinate check (or an exact title when coordinates are absent). If nothing verifies, the resolver runs a representative chain (Commons text search, category + destination Pexels, nearby Commons, destination Wikipedia image, generated travel card) and marks those results with `image_is_representative`. Handles positive/negative TTL caches, global throttling, a slower search cadence, request timeouts, a size-derived lookup budget and 429 cooldowns. Exposes stage/source diagnostics in `last_status`.

---

# 31. Important Functions

- **`match_interests(attr, user_prefs)`** (`services/interest_match.py`): Shared plural-aware matcher used by both `InformationAgent` and `RecommendAgent`. Matches canonical interest groups against name, category, description and raw OSM tags; genuine matches become `interest_based`, verifiable non-matches `discovery`, the rest `outside_interests`. `_heuristic_interest_match()` in `information_agent.py` is the thin agent-level wrapper around it.
- **`enrich_poi_images(pois, city)`** (`services/attraction_images.py`): Fills `image_url` within a lookup budget, using verified provider/Wikipedia images first and the representative/generated guarantee last. Every returned item is renderable.
- **`toggleAttractionSelection(attractionId)`** (`main.js`): The frontend workhorse for the UI. Manages the `selected_attractions` array, updates the visual state of the cards, and updates the selection counter.

---

# 32. API Endpoints

| Method | Endpoint | Purpose | Input | Output |
|---|---|---|---|---|
| `GET` | `/` | Serves the main application UI. | None | HTML Page |
| `POST`| `/api/chat` | Receives user messages and initializes the workflow in the background. | JSON (`{"message": "hi", "session_id": "123"}`) | JSON Status (`{"status": "processing"}`) |
| `GET` | `/api/stream` | Server-Sent Events (SSE) endpoint. Yields real-time updates from the background workflow. | Query Param (`?session_id=123`) | SSE Event Stream (JSON objects) |
| `POST`| `/api/reset` | Wipes the current session state and returns a new `session_id`. | JSON (`{"session_id": "123"}`) | JSON Status (`{"status": "success", "session_id": "..."}`) |
| `GET` | `/api/state` | Returns the persisted state for `?session_id=...` (including `current_step`) so a reload restores progress. | Query Param | JSON State |
| `GET` | `/api/attraction-image` | Resolves one attraction image, always renderable. | Query (`name`, `city`, `country`, `lat`, `lng`) | `{"image_url": "<url>", "source": "...", "image_is_representative": false, "diagnostics": {...}}` |
| `GET` | `/api/attraction-details` | On-demand details for one attraction. | Query Param | JSON Details |
| `POST`| `/api/email` | Sends the trip email on demand. | JSON | JSON Status |

---

# 33. Data Flow

### Chat & Extraction
User Types Message → `fetch(/api/chat)` → Flask → `TravelGraph` Queue → `ChatAgent` → LLM parses message → Updates `TravelState` → Flask yields response → `EventSource(/api/stream)` → Frontend renders Chat Bubble.

### Attractions & Images
`InformationAgent` → `GeocodingManager` (Coordinates) → `POIManager` → Geoapify + Overpass + Wikipedia notability → Normalization/dedup → `enrich_poi_images` (provider/exact identifiers, Wikipedia GeoSearch/search, Wikidata/Commons, relevance-checked Pexels, representative/generated guarantee, bounded/cached) → `_heuristic_interest_match` → Updated `TravelState` → Flask yields attractions → Frontend builds DOM Cards (verified photos first, lazy images, labelled representative cards, inline fallback on failure).

### Selection
User clicks card → `main.js` `toggleAttractionSelection()` → Card visually updates → ID added to `selected_attractions` array → Array sent in subsequent `/api/chat` requests for itinerary generation.

---

# 34. Error Handling and Fallback Architecture

ExploreX prioritizes graceful degradation to ensure a seamless user experience.

- **Geocoding Failures:** If Nominatim times out, it transparently falls back to Geoapify. If offline, it falls back to a hardcoded dictionary of 300+ major cities.
- **POI Provider Failures:** Overpass is prone to rate limits (HTTP 429). The client backs off, fails over to a different mirror, caches successful queries for 30 minutes, and keeps partial Geoapify/Wikipedia results. Wikipedia is optional enrichment: a 429 aborts it quickly instead of stalling the request. Per-provider state is disclosed through `provider_status`.
- **Image Failures:** Every displayed attraction has an `image_url`: verified where possible, otherwise an explicitly representative photo or a generated travel card. If a resolved remote URL fails in the browser, `onerror` swaps once to a branded travel card. Pexels timeouts, auth failures and 429s are non-fatal and are not cached as permanent misses. No random Unsplash fallback exists.
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
- **API Caching:** Geocoding is cached in memory, successful Overpass queries are cached for 30 minutes, and attraction images use a TTL cache with separate positive/negative lifetimes. Repeated plan regeneration therefore avoids most third-party traffic.
- **Bounded Image Work:** Attraction images are resolved within a per-call lookup budget, globally throttled and time-limited, so a slow image provider cannot delay the itinerary. Any card still unresolved at the deadline gets a generated travel card instead of another network request.

---

# 38. API Rate Limits and Responsible Usage

ExploreX is built to respect the public infrastructure it relies on:
- **Nominatim (OSM):** Strictly limited to 1 request per second. ExploreX caches results heavily to avoid duplicate queries for the same city.
- **Overpass (OSM):** Serialised through a process-wide gate (minimum interval), with dynamic endpoint rotation, a 40-second per-query wall-clock budget and a circuit breaker (2 failed queries → 90-second outage window). A failed round is non-fatal and the Geoapify/Wikipedia results are kept.
- **Wikipedia:** Discoverability requests are serialised at one per second. Image lookup distinguishes batch stages (titles/GeoSearch/Wikidata/Commons) from the expensive `generator=search` stage: search runs at one request per second, retries once after a short 429 wait, and pauses only that stage on a persistent 429. Rate-limited records are not cached as permanent negatives.
- **Pexels:** Limited to 200 requests per hour on the free tier. Verified attraction lookups run after Wikimedia sources; representative category + destination photos run next, capped (`ATTRACTION_IMAGE_PEXELS_MAX_CALLS`, default 40) and time-boxed (30 s). The resolver reuses cached successes, never reuses one photo for two attractions, and stops on HTTP 429 instead of retrying in a burst.

---

# 39. Installation and Setup

**Prerequisites:** CPython 3.13 (verified on 3.13.x, Windows x64) and Git. The
requirements also resolve on CPython 3.11, but 3.13 is the supported runtime.

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd ExploreX
   ```
2. **Create a Virtual Environment:**
   ```bash
   # Windows (verified interpreter)
   py -3.13 -m venv .venv313
   .\.venv313\Scripts\Activate.ps1

   # Mac/Linux
   python3.13 -m venv .venv313
   source .venv313/bin/activate
   ```
3. **Install Dependencies:**
   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
   ChromaDB 1.x ships its own native backend on Python 3.13; the old
   `chromadb 0.4.x` / `numpy <2` pair has no cp313 Windows wheels.
4. **Build the RAG vector database** (required once, and after any ChromaDB
   major-version change):
   ```bash
   python services/document_ingestion.py --rebuild
   ```
5. **Configure Environment:**
   Create a `.env` file in the root directory and add:
   ```env
   GEMINI_API_KEY=your_gemini_key_here
   FLASK_SECRET_KEY=super_secret_string
   # Optional
   GEOAPIFY_API_KEY=your_geoapify_key_here
   PEXELS_API_KEY=your_pexels_key_here   # attraction fallback + restaurant/accommodation images
   ```
6. **Start the Application:**
   ```bash
   python main.py
   ```
7. **Access the UI:**
   Open a browser and navigate to `http://127.0.0.1:8000`.

---

# 40. Configuration Guide

- **Required Setup:** You MUST provide a `GEMINI_API_KEY` for the AI to function, and a `FLASK_SECRET_KEY` for session security.
- **Free Configuration:** The geocoding and map systems work perfectly out of the box using OpenStreetMap. No Google Maps keys are required.
- **Optional providers:** A free `GEOAPIFY_API_KEY` adds the primary Places POI source and increases geocoding resilience. A free `PEXELS_API_KEY` improves attraction coverage by supplying the representative category + destination photo and restaurant/accommodation photos; without it, Commons/destination images and generated travel cards still guarantee a visual for every attraction.

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
3. **POI Retrieval:** Geoapify, Overpass (if available) and Wikipedia return POIs for every hub; malformed records are dropped, duplicates are merged, and the survivors are balanced across cities.
4. **Recommendation:** The shared interest matcher scans the POIs. "Athirappilly Waterfalls" matches the nature group, "Marari Beach" matches beaches. These are flagged `interest_based`; notable non-matches are `discovery`.
5. **Image Enrichment:** Each POI keeps a provider image or receives a verified Wikipedia/Commons image. Every remaining attraction receives a representative image or generated travel card, so no card is blank.
6. **Frontend Stream:** The UI receives the SSE events, renders the chat response and the attraction cards (12 at a time), and stays on the current tab until the user chooses "View Attractions".
7. **User Action:** The user selects their favorite cards and asks to generate the itinerary.

---

# 43. Example of Recommendation Flow

**User Interests:** `["historic architecture", "nature"]`

**System Normalization:** The system strips complex phrasing, reducing interests to core keywords: `["historic", "architecture", "nature"]`.

**Attraction A:** `name: "Fort Kochi", tags: {"historic": "yes", "tourism": "museum"}`
- *Match:* The keyword "historic" is found in the tags. Categorized as `interest_based`.

**Attraction B:** `name: "Lulu Mall", description: "Large shopping center"`
- *No Match:* No interest keywords are found and it is not a verifiable notable place, so it is labelled `outside_interests` and is not presented as a recommendation. A verifiable notable place that simply does not match a hobby would be labelled `discovery` instead.

---

# 44. Testing

Testing is performed with pytest. All external providers are mocked and image lookups are disabled while the suite runs.
- **Full suite:** `.\.venv313\Scripts\python.exe -m pytest tests -q` (180 tests).
- **Startup:** `.\.venv313\Scripts\python.exe main.py` and confirm the UI loads without console errors.
- **Offline HTTP contract:** `.\.venv313\Scripts\python.exe smoke_phase9.py` drives the real Flask routes with `test_client` and prints each status code and payload shape.
- **Live acceptance (requires real keys):** run a chat → discovery → recommend → strategy → communication → route cycle against real Gemini/Geoapify. Overpass and Wikipedia may legitimately be unavailable; the plan must still complete with partial data and disclose provider status.

---

# 45. Troubleshooting

- **Application crashes on startup (ChromaDB errors):** Ensure `ANONYMIZED_TELEMETRY=False` is set. Delete the `data/vector_db` folder to force a clean database rebuild.
- **No attractions load:** Check `provider_status` in the response/terminal. Overpass may be down or rate-limiting; Geoapify and Wikipedia should still return results. If every provider is empty the app reports an honest empty list rather than inventing places.
- **Images are blank/broken:** Attraction cards should never be blank: the resolver guarantees a verified, representative, or generated image. A slow or rate-limited Wikimedia/Pexels day may reduce verified coverage, so more cards will show the “Representative image” label or a generated travel card. Check `image_status`/`diagnostics` for stages such as `pexels`, `commons_search`, `commons_nearby`, `destination` and `guarantee`. If a card is still blank, confirm `ATTRACTION_IMAGE_GUARANTEE` is not set to `0` and that the frontend has the latest `main.js`.
- **Map fails to load:** Ensure you have an active internet connection to download the open-source Leaflet tiles.
- **UnicodeEncodeError in Terminal:** Set `PYTHONIOENCODING=utf-8` in your environment variables.
- **`'int' object has no attribute 'lower'`:** Fixed in this round — provider fields are coerced before any string operation. If it reappears, a new provider adapter is passing a raw numeric field through.

---

# 46. Maintenance Guide

- **Modifying AI Prompts:** Locate the specific agent in `agents/` (e.g., `chat_agent.py`) and modify the `SystemMessage` string block.
- **Changing Map Providers:** Modify the map initialization code in `frontend/static/js/main.js` to point to a different tile server URL.
- **Adjusting Recommendation Logic:** Edit the interest groups/aliases in `services/interest_match.py` (shared by both agents) rather than duplicating rules in `agents/information_agent.py`.
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

ExploreX is a powerful demonstration of how orchestrated AI agents can solve complex, multi-step consumer problems. By chaining LLM natural language understanding with resilient, free-tier geospatial services (OpenStreetMap, Geoapify, Wikipedia), ExploreX successfully transforms free-form user desires into structured, visually appealing, and highly personalized travel itineraries. The architecture proves that intelligent, dynamic travel planning can be achieved efficiently without relying on rigid logic or expensive commercial mapping monopolies.
