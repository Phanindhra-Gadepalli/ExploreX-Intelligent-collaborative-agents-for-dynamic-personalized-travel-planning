# ExploreX — Intelligent Collaborative Agents for Dynamic Personalized Travel Planning

ExploreX is a Flask web application that plans a personalised trip through a
conversation. You describe the trip in natural language; the app extracts the
details, discovers real places and weather, ranks attractions against your
interests, lets you choose what to visit, then builds a day-by-day itinerary,
route, budget and transport estimate — and can email the plan to you.

The "collaboration" is a hand-written Python state machine (`TravelGraph`), not
LangGraph. The full explanation of every module, route, agent, provider,
environment variable and known limitation lives in:

**➡️ [EXPLOREX_TECHNICAL_DOCUMENTATION.md](EXPLOREX_TECHNICAL_DOCUMENTATION.md)**

---

## Quick start

```powershell
py -3.13 -m venv .venv313
.\.venv313\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env      # then fill in GOOGLE_API_KEY and GEMINI_API_KEY
python services\document_ingestion.py --rebuild   # first run / vector DB rebuild
python main.py                  # http://127.0.0.1:8000
```

Health check: `GET /health`
Tests: `.\.venv313\Scripts\python.exe -m pytest tests -q`

## Documentation map

| Topic | Section |
|---|---|
| What the project is and how the pieces fit | [A. Project overview](EXPLOREX_TECHNICAL_DOCUMENTATION.md#a-project-overview) |
| Folder-by-folder tour | [B. Directory structure](EXPLOREX_TECHNICAL_DOCUMENTATION.md#b-directory-structure) |
| Versions and libraries | [C. Technology stack](EXPLOREX_TECHNICAL_DOCUMENTATION.md#c-technology-stack) |
| Install and first run | [D. Installation](EXPLOREX_TECHNICAL_DOCUMENTATION.md#d-installation-and-first-run) |
| Architecture and SSE | [E. Architecture](EXPLOREX_TECHNICAL_DOCUMENTATION.md#e-architecture) |
| The seven planning stages | [F. Workflow](EXPLOREX_TECHNICAL_DOCUMENTATION.md#f-end-to-end-workflow) |
| Every route and backend module | [G. Backend reference](EXPLOREX_TECHNICAL_DOCUMENTATION.md#g-backend-reference) |
| Frontend state, views and renderers | [H. Frontend reference](EXPLOREX_TECHNICAL_DOCUMENTATION.md#h-frontend-reference) |
| Models, RAG and ranking | [I. AI and RAG pipeline](EXPLOREX_TECHNICAL_DOCUMENTATION.md#i-ai-and-rag-pipeline) |
| External services | [J. External APIs](EXPLOREX_TECHNICAL_DOCUMENTATION.md#j-external-apis-and-services) |
| Database and caches | [K. Data and persistence](EXPLOREX_TECHNICAL_DOCUMENTATION.md#k-data-and-persistence) |
| Environment variables | [L. Environment variables](EXPLOREX_TECHNICAL_DOCUMENTATION.md#l-environment-variables) |
| Run, test, troubleshoot | [M. Operations](EXPLOREX_TECHNICAL_DOCUMENTATION.md#m-running-testing-troubleshooting) |
| Maintenance and debt | [N. Maintenance](EXPLOREX_TECHNICAL_DOCUMENTATION.md#n-maintenance-guide) |
| Limitations and risks | [O. Known limitations](EXPLOREX_TECHNICAL_DOCUMENTATION.md#o-known-limitations-and-risks) |
| Terms | [P. Glossary](EXPLOREX_TECHNICAL_DOCUMENTATION.md#p-glossary) |
| Cheat sheet | [Q. Quick reference](EXPLOREX_TECHNICAL_DOCUMENTATION.md#q-quick-reference) |

## Reference material

* `Base_Paper.pdf` — the academic paper this project is based on.
* `explorex_document.pdf` — an earlier project write-up (may describe older behaviour).
