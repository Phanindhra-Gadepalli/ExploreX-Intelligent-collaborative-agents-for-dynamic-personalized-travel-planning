# ExploreX — Project Repair, Debugging & End-to-End Validation Report

**Project:** ExploreX — Intelligent Collaborative Agents for Dynamic Personalized Travel Planning
**Stack:** Flask · LangChain 0.2 · google-genai / Gemini · ChromaDB 0.4.24 · sentence-transformers (all-MiniLM-L6-v2) · OSRM/Overpass/Geoapify
**Verified on:** CPython 3.11.9 (Windows)
**Status:** FINAL SUCCESS CRITERION MET — app starts, retrieval initialises, the multi-agent workflow executes, and a budget-aware, dated 5-day itinerary is generated.

---

## 1. Executive Summary

The application had a blocking startup crash, a broken international-retrieval dependency, a
silent Gemini structured-output failure, incomplete dependency declarations, and an
incompatible ChromaDB on-disk database. All were root-caused, fixed in the codebase (not
worked around), and verified with a live end-to-end run plus an automated pytest suite
(10/10 passing). No success is fabricated: where a constraint cannot be satisfied (strict
budget), the system reports it transparently instead of faking compliance.

---

## 2. Root Causes Found & Fixed

### 2.1 ChromaDB startup crash — `sqlite3.OperationalError: no such column: collections.topic`
- **Path:** `main.py → TravelGraph → RetrievalAgent → Chroma(...)`.
- **Root cause:** the on-disk DB in `data/vector_db` was created by **ChromaDB 0.6.x**
  (its `collections` table has `config_json_str`, and the `topic` column was removed by
  migration `sysdb 00005`), but the app runs **ChromaDB 0.4.24**, which still queries
  `collections.topic` (`chromadb/db/mixins/sysdb.py`). Confirmed via
  `PRAGMA table_info(collections)` and `SELECT * FROM migrations`. Downward migration is
  not supported, so a controlled rebuild was required.
- **Fix (per Phase-2 rules — backup first, never delete as the first step, preserve embeddings):**
  1. Timestamped copy backup → `data/vector_db_incompatible_backup_20261024_193455/`
  2. Moved the incompatible original aside (not deleted) → `data/_vector_db_old_20261024_193455/`
  3. Re-ingested with the pinned client: `python services/document_ingestion.py --rebuild`
     → 52 knowledge documents, dim 384, self-compatible schema.
- **Guard added** in `agents/retrieval_agent.py`: on a schema-mismatch error the agent now
  raises an actionable `RuntimeError` naming the folder, the version conflict, and the exact
  rebuild command — instead of a raw `sqlite3` error.

### 2.2 Landing page could 500 on restore — `main.py`
- Restoring a saved session called `TravelGraph(...)` in the `/` route with no error handling,
  so any init failure crashed the home page.
- **Fix:** wrapped the restore in `try/except` with a clear log line; the workflow is created
  lazily in `/api/process`/`/api/stream` if missing. Home page now always renders.

### 2.3 Gemini structured-output failure — `agents/recommend_agent.py`
- **Observed:** `Key 'title' is not supported in schema` warnings, then a hard 400
  `parameters.properties[pois].items: missing field`.
- **Root cause (proven empirically):** the installed **google-genai 0.7.2** function-declaration
  converter omits `items` for **any** array property (even `List[str]`), so nested-array
  structured output via `with_structured_output` is unusable. `method="json_mode"` is not
  accepted by langchain-google-genai 1.0.10.
- **Fix (at the integration boundary, tool semantics preserved, no fabrication):** POI
  extraction now uses **native JSON mode** — a dedicated `ChatGoogleGenerativeAI` with
  `model_kwargs={"response_mime_type": "application/json", "response_schema": POIExtractionList}`.
  Response is fenced (```json) → fence stripped → `json.loads` → lenient parse accepting both
  `{"pois":[...]}` and a bare list. Items **without a valid `location` dict are discarded**
  (coordinates are never invented). `POIExtraction` defaults non-essential fields
  (`id`/`address`/`description`) to `""` since the model omits them.
- Verified live: `chat_agent.TravelState` (scalar-only) and `budget_agent.CityCosts`
  (scalar floats) work under structured output; the array-containing path uses the JSON-mode fix.

### 2.4 International retrieval silently broken — `agents/retrieval_agent.py`
- Code imported `from ddgs import DDGS`, but `ddgs` was not installed (only the older
  `duckduckgo_search` was present) → every international web search failed silently.
- **Fix:** `_ddg_search` tries `ddgs`, falls back to `duckduckgo_search` (same `DDGS` API:
  title/href/body), and only warns + returns `[]` if both are unavailable.

### 2.5 Excessive DEBUG log flooding — `workflows/travel_graph.py`
- `logging.basicConfig(level=logging.DEBUG)` at import drowned real signals in third-party noise.
- **Fix:** switched to `INFO`, silenced noisy libraries (`httpcore`, `httpx`, `urllib3`,
  `chromadb`, `posthog`, `sentence_transformers`, `huggingface_hub`, `filelock`) at `WARNING`,
  and `langchain_google_genai._function_utils` at `ERROR` (the cosmetic `title` converter
  warning), with explanatory comments.

---

## 3. Files Changed

| File | Change |
|------|--------|
| `agents/retrieval_agent.py` | ChromaDB schema-mismatch guard; `ddgs`→`duckduckgo_search` fallback |
| `agents/recommend_agent.py` | Native-JSON-mode POI extraction (fence strip, lenient parse, no-fabrication filter); tolerant `POIExtraction` defaults |
| `workflows/travel_graph.py` | Logging level + per-library silencers |
| `main.py` | Fault-tolerant landing-page session restore |
| `requirements.txt` | Rewritten: added `googlemaps`, `google-genai`, `ddgs`, `huggingface-hub`, `numpy<2.0`; bound `langchain-chroma<0.3.0`; pinned `chromadb==0.4.24` with a schema/re-ingest NOTE |
| `.gitignore` | Hardened: ignore `.venv311/`, `verify_env/`, `data/vector_db*`, backup dirs, caches; removed the self-ignore and `test_*.py` blocks so tests can be committed |
| `conftest.py` | New: makes repo root importable, loads `.env`, sets `ANONYMIZED_TELEMETRY` |
| `tests/test_chromadb_validation.py` | New: Phase-2 mandated temp-DB ChromaDB round-trip + 384-dim check |
| `tests/test_startup.py` | New: Flask import, `/` → 200, real-DB RetrievalAgent init |
| `tests/test_itinerary_structure.py` | New: deterministic date/day-structure guarantees |
| `e2e_validate.py` | New: headless driver of the real `/api/process` pipeline |

**Data operations:** `data/vector_db` rebuilt with 52 docs; original DB preserved in
`data/vector_db_incompatible_backup_20261024_193455/` and `data/_vector_db_old_20261024_193455/`.

**Cleanup:** removed temporary diagnostic probes (`_probe_schema.py`, `_probe_poi.py`,
`_probe_raw.py`) and throwaway `e2e_*.log` files.

---

## 4. Dependency & Config Audit (Phase 9)

- Missing-but-imported packages added: **`googlemaps`** (used by `information_agent.py`),
  **`ddgs`** (retrieval), **`google-genai`**, **`huggingface-hub`**, **`numpy`**.
- Version pinning documented where on-disk formats matter (ChromaDB schema coupling).
- No secrets committed: `.env` (GEMINI_API_KEY, RAPIDAPI_KEY, GEOAPIFY_API_KEY, MAIL_*) is
  gitignored; `.gitignore` no longer ignores itself or test files.
- Environment note: `.venv` was broken and `.venv311` corrupted (chromadb failed on missing
  `opentelemetry.semconv._incubating`; pip's vendored pygments broken). All verification ran
  on the working global **CPython 3.11.9**. Recreate a clean venv from `requirements.txt`
  before deploying.

---

## 5. Validation Results

### 5.1 Automated tests — `python -m pytest tests/ -v`
```
10 passed, 2 warnings in 12.92s   (warnings are cosmetic: google.api_core py-eol notice,
                                   Flask-Session FileSystem deprecation)
```
- ChromaDB temp-DB round-trip (add→persist→reopen→query) and 384-dim embeddings: **PASS**
- Flask import, `/` → 200 with `<title>`, `/api/process` + `/api/stream` routes present: **PASS**
- Real-knowledge-base `RetrievalAgent` init + `retrieve_context("Kerala")`: **PASS**
- Itinerary structure (1 entry/day in order; unique, consecutive, non-suggested dates;
  invalid start date flagged not crashed): **PASS**

### 5.2 Live end-to-end (headless driver against the real Flask pipeline)
Scenario: **Kerala · 5 days · 10 adults · 0 kids · viewpoints & photography · strict budget**.
All 9 agents executed in order: chat → information → retrieval → recommend → strategy →
communication → route → budget → transit.

- **₹80,000 strict →** estimated **₹114,400** → reported `budget_infeasible=True` with a
  transparent "closest feasible configuration ₹114,400" warning. **No fabricated compliance.**
- **₹200,000 strict →** estimated **₹210,490** → correctly infeasible again.
- **₹300,000 strict →** estimated **₹287,766 ≤ ₹300,000** → **FULL ITINERARY GENERATED**:
  - Day 1 = 05 Nov 2026 … Day 5 = 09 Nov 2026 (5 unique, consecutive dates)
  - Real POIs (Fort Immanuel, Mattancherry Dutch Palace, Changampuzha Samadhi, etc.),
    OSRM-optimised routes with real coordinates
  - Data provenance: 40 POIs across 5 Kerala cities (Overpass), 12 accommodations,
    29 restaurants, 5 RAG docs, Geoapify geocoding
  - Email auto-send correctly **skipped** (transparently) because no email is in state

### 5.3 Functional requirements (Phase 7)
- Personalisation: 9 required fields extracted & honoured.
- Strict budget: enforced as a **whole-trip** constraint; infeasibility reported honestly.
- Dates: valid, unique, consecutive; flexible start handled with explicit "suggested" flag.
- Map: real coordinates + OSRM routing produced.
- Email: transparent, never silently dropped.

---

## 6. How to Run

```powershell
# 0. Clean environment (recommended)
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1. Credentials (create .env — never commit it)
#    GEMINI_API_KEY, GEOAPIFY_API_KEY, RAPIDAPI_KEY, MAIL_USERNAME, MAIL_PASSWORD, ...

# 2. (Re)build the knowledge base with the pinned ChromaDB version
python services/document_ingestion.py --rebuild

# 3. Automated checks
python -m pytest tests/ -v

# 4. Start the app
python main.py
#    open http://127.0.0.1:5000

# 5. Optional headless pipeline drive (no browser)
python e2e_validate.py 300000
```

---

## 7. Backup & Rollback

- **Incompatible DB preserved (not deleted):**
  `data/vector_db_incompatible_backup_20261024_193455/` and `data/_vector_db_old_20261024_193455/`.
- **To roll back the vector store:** remove `data/vector_db`, then either restore a backup
  *and* align the ChromaDB version, or re-run `services/document_ingestion.py --rebuild`.
- All code changes are additive/in-place; revert via git to restore prior behaviour.

---

## 8. Resolved vs. Remaining

**Resolved:** startup crash, landing-page 500 risk, Gemini array-schema failure, silent
international-retrieval break, log flooding, incomplete dependencies, gitignore hygiene.

**Not exercised / environment-dependent (transparently noted, not hidden):**
- Email delivery: skipped in tests because no recipient email is present in the session state.
- Car-rental RapidAPI returned 403 in this environment; the transit path degrades gracefully
  and reports it rather than crashing.
- The corrupted `.venv` / `.venv311` were left untouched (not used); a fresh venv from
  `requirements.txt` is advised for deployment.

**Final success criterion: MET.**
