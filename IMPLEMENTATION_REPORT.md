# ExploreX — Implementation Report
### Bug fixing, backend stabilization and end-to-end workflow repair (10-phase spec)

This report documents what was actually changed, **why** (root cause), which tests were
executed and what they really returned. Nothing below is claimed as verified unless it was
exercised by the automated suite or by a live HTTP-contract run against the real Flask app.

---

## 1. How to run

```powershell
# 1) install / refresh dependencies (system Python 3.11 is used; the checked-in
#    .venv / .venv311 / verify_env folders are stale and must not be activated)
python -m pip install -r requirements.txt

# 2) configure secrets (never commit the populated file)
copy .env.example .env       # then fill GOOGLE_API_KEY / GEOAPIFY_API_KEY / MAIL_* ...

# 3) start the app (Flask dev server on http://127.0.0.1:8000)
python main.py

# 4) full regression suite (all external services mocked)
python -m pytest tests -q

# 5) compile-only sanity check of the backend
python -m compileall -q main.py services agents workflows

# 6) optional live end-to-end run — REQUIRES real Gemini + Geoapify credentials
python e2e_validate.py 200000
```

---

## 2. Test results (actual)

```
$ python -m pytest tests -q
83 passed, 2 warnings in 16.16s
```

The two warnings are pre-existing and unrelated to this work: a `FutureWarning` from
`google.api_core` about Python 3.11 EOL dates, and a `DeprecationWarning` for
`flask_session.FileSystemSessionInterface`.

Collection breakdown (83 tests / 7 files):

| File | Tests | Coverage area |
|---|---|---|
| `tests/test_startup.py` | 3 | import/boot, session cleanup, static assets |
| `tests/test_attraction_discovery_and_details.py` | 7 | Geoapify/Overpass discovery, detail enrichment |
| `tests/test_chromadb_validation.py` | 3 | vector store load/query |
| `tests/test_generation_and_providers_fixes.py` | 7 | provider fixes from the earlier round |
| `tests/test_itinerary_structure.py` | 4 | day/spot structure emitted by the workflow |
| `tests/test_map_transit_budget_email.py` | 20 | map filters, transit, budget, email |
| **`tests/test_spec_phases_regression.py`** | **39** | **NEW — phases 2–9, groups A–H** |

Baseline at the start of this work was **44 passed**. All 44 still pass; 39 tests were added.
No existing test was deleted. Two assertions were rewritten because the *intended* contract
changed (documented in §3 Phase 3 and §4 below), and the replacement assertions are stricter.

`python -m compileall -q main.py services agents workflows` → exit 0, no syntax errors.

A separate **live HTTP-contract smoke run** (Flask `test_client`, real route handlers, no
network) confirmed the Phase 9 error surface end to end before the scratch script was removed:

```
GET  /                               -> 200
GET  /api/state                      -> 200  {status, session_id, trip}
GET  /api/assistant                  -> 200  {status, ...}
GET  /api/nearby/<bad-coords>        -> 404  {error: "Session not found"}
GET  /api/attraction-details?name=   -> 400  {error: "name parameter is required"}
POST /api/email   (no session)       -> 400  {success: false, message: ...}
POST /api/process (workflow raises
      RuntimeError("Gemini 403 for key sk-SMOKETESTSECRET"))
                                   -> 500  leaked_secret=False  request_ref=<8 hex chars>
```

---

## 3. Phase-by-phase: root cause → fix

### Phase 1 — Repository audit
Traced the real execution path rather than the dead code: `frontend/static/js/main.js`
→ `POST /api/process` → `TravelGraph.process_step(step, session_id, **kwargs)` over the
state machine `chat → information → retrieval → recommend → strategy → communication → route
→ complete`, with `POIManager` (`services/poi_fallback.py`) as the single POI source and
`services/budget_norm.py` / `services/email_service.py` as the canonical budget/email
renderers. Several top-level modules (`agents/*`, `workflows/travel_graph.py`) were reached
only through that path; ordering bugs found later were all in it. Branding, light theme and
all existing endpoint names were preserved.

### Phase 2 — Overpass hardening and no-fabrication
**Root causes**
1. Overpass was called with a single hard-coded URL, a single implicit timeout, no retries
   and no per-mirror cooldown, and the exception propagated out of `get_attractions()`, so a
   rate-limited public mirror aborted an otherwise healthy trip.
2. When Geoapify succeeded but Overpass failed, the provider health map was only written for
   the *successful* provider — the failure was invisible to `/api/state` and to the plan.
3. Detail fields were being filled with plausible defaults (rating `4.5`, `currency "INR"`,
   invented amenity lists) whenever the source data had none — i.e. fabrication.

**Fixes** (`services/poi_fallback.py`)
- Endpoint set is now configurable through `OVERPASS_ENDPOINTS` (default = built-in mirror
  list), with per-mirror rotation and per-mirror cooldown tracking
  (`self.overpass_cooldowns[endpoint]`).
- Separate `OVERPASS_CONNECT_TIMEOUT` (5 s) and `OVERPASS_TIMEOUT` (25 s read) plus a
  process-wide minimum request interval (`OVERPASS_MIN_INTERVAL_SECONDS`, default 2 s) so a
  burst of itinerary generations cannot trip the mirrors.
- A proper User-Agent (`OVERPASS_USER_AGENT`) instead of none.
- Bounded retries with exponential backoff **plus jitter**
  (`(2 ** attempt) + random.uniform(0, 1)`) on 429/500/502/503/504, honouring a server
  `Retry-After` (numeric seconds *or* HTTP-date) via `_retry_after_seconds()`; the sleep is
  capped so a worker can never be blocked for minutes.
- `_throttled_post()` + explicit `cooldown` state when every mirror is cooling.
- Structured provider status: `_ensure_provider_state`, `_record_status`, and the new
  `_publish_provider_status(provider_status)` which mirrors **every** provider outcome
  (`ok / disabled / empty / error / timeout / cooldown / unavailable:<reason>`) into
  `_provider_health` — including on the *partial success* path, which was the actual bug.
  Surfaced through `get_provider_status()` and `/api/state`.
- All providers failing now returns `[]` with a recorded reason instead of raising, so the
  itinerary generation continues from whatever real data exists and never receives
  synthetic attractions.
- **No fabrication** in the detail/OSM path: untagged OSM records keep `rating=None`,
  `currency=None`, `amenities_status="unavailable"` and an explicit
  "Address not provided" placeholder; `_parse_rating()` only accepts a genuinely reported
  numeric value.

**Tests:** `test_overpass_total_failure_still_returns_geoapify_results_with_status`,
`test_overpass_exception_is_caught_and_recorded_not_raised`,
`test_all_providers_down_returns_empty_list_with_reason_not_fabrications`,
`test_parse_rating_only_accepts_a_reported_value` (parametrized),
`test_osm_accommodation_without_tags_exposes_unknown_instead_of_guesses`,
`test_geoapify_attractions_emit_no_rating_or_price_when_absent`,
`test_frontend_does_not_render_default_ratings_or_mock_exchange_rates` (source-level guard on
`main.js`).

### Phase 3 — Map day filters and one normalized day/date contract
**Root cause** — the day filter chips were generated from `state.activeItineraryDay`, and
"all" was only reachable as a side effect; the map re-initialised itself per filter change, so
markers from the previous day survived and the active chip never matched the rendered layer.

**Fixes** (`frontend/static/js/main.js`)
- `state.mapCurrentDay` is now an **independent** filter axis, defaulted to `'all'` in the
  state object, in `resetTrip()` and on every new plan (`mapCurrentDay: 'all'`, chips built as
  `All Days` + `Day 1..Day N`, "All Days" is the default view, not opt-in).
- Filter changes re-render from the in-memory itinerary (`renderMap(state.itinerary)`) — no
  reload, no re-fetch — and the active chip is derived from the same variable
  (`String(state.mapCurrentDay) === String(value)`), so chip and layer cannot diverge.
- A monotonic `_mapRenderToken` invalidates in-flight async renders, preventing stale marker
  batches from a previously selected day.
- Per-day markers **and** per-day polylines only; routes are never drawn across days, and an
  empty day renders `Day N has no mapped stops.` instead of an implicit fallback to Day 1.
- One normalized contract for a stop: integer `day`, `date`, string `attraction_id`, `name`,
  `lat`, `lng`, `sequence`, `start_time`, `end_time`. `normalize_itinerary_for_client()` in
  `main.py` coerces `"day1"`/`"Day 2"` labels to integers, backfills `sequence` 1..N per day,
  stamps `attraction_id`/`day` onto each stop and **never mutates the stored server state**
  (it works on a deep copy). The frontend receives all days in one payload.
- `tests/test_map_transit_budget_email.py`: the old guard
  asserted `String(d.day) === String(state.activeItineraryDay)` — i.e. it *encoded the bug*.
  It was retargeted to the new contract, and
  `test_map_day_filter_defaults_to_all_days_and_is_independent_of_itinerary` was added to
  lock in the `'all'` default, the decoupled variable, the no-reload render call, the stale
  render token and the empty-day message.

### Phase 4 — Transit, routing and the single `should_rent_car` authority
**Root causes**
1. Intercity transit was queried even when origin == destination city, producing a
   nonsensical "Getting There" leg for a local trip; when the list came back empty the
   frontend wiped the whole panel — including valid local options — and showed a failure.
2. Two independent consumers made the car-rental decision (the agent recommendation and the
   UI), and a `False` user choice was being overwritten by the agent.
3. `RouteAgent` closed its tour with `start → … → start`, duplicating the first stop.
4. `renderTransport()` in the frontend recomputed the budget a *second* time (subtracting the
   selected intercity cost and re-summing every numeric key), which double-counted against
   `BudgetAgent`, whose `local_transport` already contains `car_rental + fuel`.
5. A dead RapidAPI rental key was retried on every request.

**Fixes**
- `workflows/travel_graph.py`: `_city_key()` normalizes origin/destination; same-city trips
  skip `transit_agent.get_transit_options()` entirely and set `same_city: true`. Provider
  availability is reported as **data** (`car_rental_available`, `car_rental_source`) and never
  as the decision.
- One authoritative `should_rent_car`: explicit user `rental_choice` > agent recommendation >
  fallback estimate, with the winning source recorded. A user `False` is never overridden.
- `services/car_rental_api.py`: `_disable()` circuit breaker on 401/403/429 — the service is
  marked unavailable for the session, a fallback estimate is used, and no further live calls
  are made.
- `agents/route_agent.py`: tour rendered with `tour[:-1]` so the return leg to the start is
  gone; each stop carries `sequence`, `day`, `attraction_id`; stops that could not be
  scheduled are returned through `unscheduled_out` with a reason.
- `frontend/static/js/main.js` `renderTransport()`:
  - empty intercity list now distinguishes a same-city trip
    (`data-testid="transit-empty"`, "No intercity journey needed") from a genuine failure, and
    local options are still rendered via the extracted `renderLocalTransit()`;
  - **the client-side budget mutation was removed** — selecting an option now sets
    `state.selectedTransport`, marks `state.planIsStale = true`, persists to `sessionStorage`
    and shows "Transport option selected. Regenerate your itinerary to apply the new cost.";
  - a selection is labelled **Selected**, never "Booked"; estimates are labelled estimates;
  - `const type = String(opt.mode || 'route')` removes a `.toLowerCase()` crash on
    non-string modes; `typeIcons` is hoisted above the early return (no TDZ).
- `tests/…`: `test_same_city_trip_does_not_request_an_intercity_leg` (asserts
  `get_transit_options.call_count == 0`),
  `test_different_city_still_queries_intercity_options`,
  `test_dead_rental_provider_never_erases_the_self_drive_decision`
  (`should_rent_car is True`, `car_rental_source == "fallback_estimate"`),
  `test_rental_choice_of_false_is_not_overridden_by_the_agent`,
  `test_frontend_no_longer_recomputes_the_budget_on_transport_choice`.

### Phase 5 — Budget: one normalized breakdown, truth about completeness
**Root causes** — the total could be reported while only some categories existed (a partial
sum presented as a valid complete budget); "0" was ambiguous between *free* and *unknown*;
an incomplete plan could be described as "within budget"; the UI re-derived totals a second
time.

**Fixes** (`services/budget_norm.py`, consumed by `main.py` state, `/api/state`, the budget
page and `services/email_service.py`)
- `normalize_budget()` now reports `total_source` ∈
  `reported | derived_from_categories | none`, and a checksum so the advertised total equals
  the sum of displayed categories.
- `data_complete = total is not None and checksum_ok and not missing_categories`.
- `status` ∈ `within | over | incomplete`; it degrades to `incomplete` unless the total is
  genuinely complete (or the plan is definitively over the limit), and `within_budget` is
  `True` only when `data_complete`. An over-budget plan can therefore never claim to fit.
- Currency is single (INR) with `format_inr()`; accommodation is computed
  per-room × rooms × nights; the strict numeric limit supplied by the user is preserved.
- `frontend/static/js/main.js` `renderBudget()`: reads the single canonical breakdown, treats
  `nb.missing_categories` as an explicit set, keeps a genuine `0` visible
  (`₹0 · reported`) and renders `data-testid="budget-unknown-categories"` naming every
  category with no data and stating that the total excludes them. `renderBudgetClosing()`
  now unwraps `state.itinerary.days` (it was indexing a dict as if it were a list).

### Phase 6 — Accommodation and email delivery
**Root causes**
1. The selected accommodation dict was passed into a list renderer that iterated its **keys**,
   silently producing an empty "Where You'll Stay" section.
2. `TransitAgent` emits `intercity_transport` / `local_transport`, but the email renderer read
   `options` — so the transport section always vanished.
3. Success was reported before SMTP acknowledged, and `unscheduled` stops were never rendered.

**Fixes** (`services/email_service.py`, `main.py`)
- Accommodation/food come from one source of truth keyed by stable ID;
  `_as_poi_list()` accepts a dict, a list or `None`, and recommendations are labelled
  "(Suggested)" — never "booked".
- `_render_transit()` reads the real TransitAgent structure, marks the recommended mode, and
  appends an explicit "planning estimates … not bookings and not guaranteed prices" note.
- `_render_unscheduled(items)` adds a "⚠️ Not Included in the Plan" section (name, planned day,
  reason) to **both** the HTML and the text part, wired straight after the itinerary.
- Server-side pre-send validation runs against the finalized state and logs the **exact**
  missing fields (`city`, `itinerary`, `budget.total`, `accommodation`, recipient); an invalid
  payload refuses to send. `/api/email` additionally keeps the `_EMAIL_INFLIGHT` guard for
  duplicate protection.
- `send_trip_email()` returns success only after `sendmail()` **and** `quit()` complete;
  `SMTPSenderRefused` (and any SMTP error) returns `False`, and the logged message never
  contains the password. Body is branded, INR-formatted, HTML + plain-text alternative.

### Phase 7 — Persistent AI Travel Assistant
**Root cause** — the assistant existed only inside the chat step and was re-created per page;
follow-ups restarted information collection and could overwrite the destination, budget, dates
or selections.

**Fixes**
- `frontend/templates/index.html`: a real left-rail `<aside id="assistant-panel"
  class="assistant-panel" data-testid="assistant-panel">` with header, `aria-live` body,
  footer actions and a mobile open button — present on every trip page.
- `frontend/static/css/styles.css` (+129 lines): two-column desktop grid with a **sticky**
  rail and a full-width drawer fallback under the mobile breakpoint.
- `main.py`: `GET /api/assistant` is read-only and renders from the persisted session state —
  it never re-runs an agent and never re-renders from a frontend mockup.
  `TravelGraph.get_assistant_context()` returns the persisted conversation window
  (`get_conversation_history(limit=40)`), current step and collected facts; the panel shows
  explicit loading / empty / error states.
- Follow-ups merge into the existing state (`_process_chat` applies
  `user_info.update(new_state)` and invalidates dependents **only** when the city actually
  changes), so a follow-up cannot clobber the destination, budget, dates or selections;
  `new_trip_state()` resets trip data only.

### Phase 8 — Data-flow invariants (all 12)
Verified/locked in by tests and code paths:
- The user's selection is authoritative; `should_rent_car`, transit and budget all read it
  rather than re-deciding.
- Every selected attraction is either scheduled or **explicitly reported unscheduled with a
  reason** (`RouteAgent.unscheduled_out` → plan state → `/api/state` → email section added in
  Phase 6). Previously such stops disappeared silently.
- Stable IDs, coordinates and names survive recommendation → itinerary → map → email
  (`attraction_id` stamped by `normalize_itinerary_for_client`, `test_normalize_itinerary_coerces_day_labels_and_stamps_the_contract`).
- Day grouping, sequence and times are consistent and recomputed on every change;
  `_reconcile_plan_with_selection()` recomputes dependents when selections change, and
  `planIsStale` blocks a stale plan from being read as current.
- The final response reflects the actual final itinerary;
  `test_normalize_itinerary_does_not_mutate_the_stored_server_state` guards against the
  client view diverging from the server state.
- `main.normalize_itinerary_for_client()` now **always** returns `{"days": [...]}` (previously
  `[]` for the empty case and the raw object otherwise), so downstream consumers have one
  shape to code against.

### Phase 9 — Error handling, session isolation, observability
**Root cause** — five endpoints returned `jsonify(error=str(e))`, leaking exception text
(including provider names, status codes and, in the worst case, key fragments) to the browser,
`/api/attractions/<city>` had no `try/except` at all, and there was no way to correlate a
user-visible failure with a server log line.

**Fixes** (`main.py`)
- A single error contract added at module level (`import uuid`, `import traceback`):
  - `log_error(context, exc)` → prints `[ERROR][<context>][ref=<8 hex>] <Type>: <msg>` plus
    the traceback **to the server log only**, and returns the correlation id;
  - `client_error_message(exc, fallback=...)` → drops anything matching `_INTERNAL_MARKERS`
    (attributeerror / keyerror / none / traceback fragments / rapidapi / "api key" / "bearer ")
    and returns a generic, actionable sentence;
  - `error_response(exc, context, status, fallback, extra)` →
    `{"status":"error","success":false,"error":<safe>,"message":<safe>,"request_ref":<ref>}`
    (`error` is kept because `main.js` reads `data.error`).
- Wired into `/api/process` (with the failing `step` in the payload), `/api/destinations`,
  `/api/attractions/<city>` (also given a try/except and a consistent 404), `/api/nearby/<id>`,
  `/api/attraction-details`, and the SSE `/api/stream` generator, which now emits
  `{type:"error", error:<safe>, request_ref:<ref>}` instead of raw exception text.
- HTTP codes are meaningful: 400 for malformed requests, 404 for unknown session/POI,
  429 for duplicate email submission, 500 for genuine failures.
- Session isolation: per-session `TravelGraph.session_states`, `new_trip_state()` clearing trip
  data while keeping identity, `workflows` capped at 500 entries with cleanup, and
  `test_new_trip_resets_trip_data_but_keeps_identity_and_isolates_sessions` +
  `test_process_endpoint_error_does_not_leak_the_exception` (which plants
  `sk-SMOKETESTSECRET` in an exception message and asserts it never reaches the client while
  `request_ref` does, exactly 8 characters).

---

## 4. Files modified

| File | + / − | Phase(s) |
|---|---|---|
| `main.py` | 323 / 44 | 3, 6, 7, 8, 9 |
| `workflows/travel_graph.py` | 557 / 112 | 3, 4, 7, 8 |
| `services/poi_fallback.py` | 384 / 87 | 2, 9 |
| `services/email_service.py` | 232 / 84 | 5, 6, 8 |
| `services/budget_norm.py` | (new) | 5 |
| `services/car_rental_api.py` | 48 / 2 | 4 |
| `agents/route_agent.py` | 143 / 24 | 3, 4, 8 |
| `agents/transit_agent.py` | 96 / 31 | 4 |
| `agents/budget_agent.py` | 71 / 13 | 5 |
| `agents/recommend_agent.py` | 46 / 12 | 2, 8 |
| `agents/retrieval_agent.py` | 39 / 11 | 2 |
| `agents/information_agent.py` | 32 / 10 | 4 |
| `frontend/static/js/main.js` | 809 / 112 | 3, 4, 5, 7, 9 |
| `frontend/templates/index.html` | 75 / 6 | 7 |
| `frontend/static/css/styles.css` | 129 / 0 | 7 |
| `requirements.txt` | 27 / 3 | 1 |
| `tests/test_spec_phases_regression.py` | (new, 39 tests) | 10 |
| `tests/test_map_transit_budget_email.py` | updated guards | 3 |

Two pre-existing assertions were intentionally rewritten (not deleted):
`test_day_filters_are_generated_before_the_map_init_guard` (was encoding the
`activeItineraryDay` coupling that Phase 3 removes) and the map/budget shape readers that
assumed a bare list instead of `{"days": [...]}`. Both now assert the stricter, intended
contract.

## 5. Defects found *by* the new tests and then fixed
These were not in the original inventory; each was reproduced red, fixed, and re-run green.

1. **Invisible provider failure** — when Geoapify succeeded, a failing/disabled Overpass was
   never written into `_provider_health`, so partial success hid the outage
   → `_publish_provider_status()`.
2. **Partial budget presented as complete** — `total_is_valid` was `True` for a one-category
   breakdown → `total_source` + `data_complete` rules in `normalize_budget()`.
3. **`unscheduled` silently dropped from the email** — `main.py` passed it,
   `email_service.py` never rendered it → `_render_unscheduled()`.
4. **Inconsistent itinerary shape** — `normalize_itinerary_for_client()` returned `[]` in one
   branch and `{"days": …}` in the other, breaking `renderBudgetClosing()` → always
   `{"days": [...]}`, frontend unwraps `.days`.

---

## 6. Remaining issues — cannot be verified without live credentials

These paths are implemented and unit-covered with mocks, but their *live* behaviour was not
observed in this environment. They need real keys/SMTP before they can be declared working:

1. **Gemini generation quality and the full multi-agent run** — requires `GOOGLE_API_KEY` /
   `GEMINI_API_KEY`. Run `python e2e_validate.py 200000`.
2. **Geoapify Places quotas / real attraction coverage** — requires `GEOAPIFY_API_KEY`
   (small POI caches under `data/attraction_cache/` are not a substitute for live coverage).
3. **Live Overpass mirror behaviour** — public mirrors were unreachable from this machine, so
   backoff/jitter/cooldown logic is verified only against mocked responses (a real mirror
   timeout still needs a spot check).
4. **RapidAPI car rentals** — `RAPIDAPI_KEY` is unset; only the 401/403/429 circuit breaker and
   the fallback estimate path were exercised.
5. **SMTP delivery and real rendering in mail clients** — `MAIL_*` is unset; the tests use a
   mocked SMTP and a decoded MIME body, so HTML layout in Gmail/Outlook is unverified.
6. **Pexels images, weather and fuel-price APIs** — optional keys unset; they degrade to
   "unavailable" rather than fabricating values.
7. **`flask_session` deprecation** — the filesystem backend warns; migrating to the cache-lib
   backend is a separate, deliberate change and was not attempted here.
8. **Frontend cannot be syntax-checked mechanically** — Node.js is absent from this environment.
   `main.js` was validated by Python brace/paren balance walks of the edited functions plus the
   source-level pytest guards; a browser smoke test of the map filters, transport selection,
   budget page and assistant rail is still worth doing against a live server.

---

## 7. Follow-up round — Transport options and the Budget page layout

Reported after the first pass, with screenshots:

> *"transit agent is not working properly in transport options — it should show transport
> options from origin city to destination city along with the price"* and
> *"budget and final greetings are showing side by side; budget details should show on top and
> the greetings below"*.

### 7a. Why the fares looked broken (root causes)

1. **Currency mismatch, not a pricing bug.** `TransitAgent` was told to quote *international*
   routes in **USD** (`currency_symbol = "USD" if is_international else "INR"`), while every
   consumer labelled the number with a rupee sign. A `$320` Delhi→Dubai airfare therefore
   rendered as **₹320**, and `BudgetAgent` (which reads `estimated_cost` as `price_inr`) turned
   it into a ₹5,000 "Intercity transport" line for ten travellers.
2. **The journey was never named.** The payload carried `origin`/`destination` only if the model
   felt like it, and the Transport tab rendered bare "Flight / Train" cards — nothing said which
   route was priced. The email said only "Getting There & Back".
3. **The cost basis was ambiguous.** `per_person` was printed verbatim as a snake_case token and
   there was no statement of whether a fare was one-way or round-trip, while the budget silently
   multiplied it by 2.
4. **A provider failure looked like "no options exist".** `get_transit_options` returned the
   unreadable legacy blob `{"flights": [], "trains": [], "buses": []}` on any exception.
5. `BudgetAgent._calculate_intercity_transport_cost` **mutated the shared transit payload**
   (`o["price_inr"] = float(o.get("estimated_cost", 0))`) and treated a missing price as `0`.

### 7b. Fixes

| File | Change |
|---|---|
| **`services/transit_norm.py`** *(new, 367 lines)* | One canonical transit shape: session-authoritative `origin`/`destination` + `route_label`, `trip_basis: "one_way"`, per-option leg, `cost_status` (`estimated` / `unavailable` / `non_inr_currency`), `price_inr` populated **only** for genuinely-INR quotes, `group_cost = cost × travellers` (arithmetic, not a new estimate), legacy `options` / `flights` / `trains` / `buses` shapes flattened, duplicates collapsed, recommendations kept only when they point at a card that exists, a quoted `0` demoted to "no price", and **idempotent** (safe to re-normalize; preserves `provider_status: "error"`). |
| `agents/transit_agent.py` | Prompt now demands **INR for every route including international**, an explicit **one-way per-person/per-vehicle** basis, the leg echoed on each option, and realistic-magnitude guidance ("₹320 for a Delhi→Dubai flight is not plausible"). The USD/INR branch is gone. Results are normalized before returning; a failure now returns the canonical empty payload with `provider_status: "error"` and an honest note. |
| `workflows/travel_graph.py` | Both the same-city branch and the agent branch run through `normalize_transit_options`, and the session's own origin/destination always win over whatever the model echoed; the `except` path produces a canonical payload instead of `None`. |
| `agents/budget_agent.py` | Intercity costing works on **copies**, uses only INR-denominated fares, ignores zero/absent prices, and returns `(0.0, "N/A")` when nothing is priced in rupees instead of inventing a total. |
| `frontend/static/js/main.js` | Transport tab: a route heading (`data-testid="transit-route"`, "Getting from Delhi → Dubai · group of 10 · one-way fares per traveller"), a per-card leg line with the departure→arrival window (`data-testid="transit-leg"`), currency-aware `money()` (no more hard-coded `₹`), human basis labels ("per person · one way"), a group-cost line, a "not priced in ₹ — excluded from the budget" note for foreign-currency quotes, richer mode icons, and a distinct message when the provider failed vs when a route genuinely has no options. |
| `frontend/templates/index.html` + `styles.css` | `#budget-closing` moved **out of** `.budget-container` (a `display:flex` row) to a sibling below it, with `.budget-closing { display:block; width:100%; margin-top:1.75rem; }` — budget chart + legend on top, wrap-up/greetings underneath at full width. |
| `services/email_service.py` | `_render_transit` now titles the section "Getting There & Back (Delhi → Dubai)", prints each option's leg, spells the basis out (`per person, one way`) and formats every fare **in its own currency** via `_money()`/`_CURRENCY_SYMBOLS` instead of prefixing `₹`. |

### 7c. Verification

```
python -m compileall -q main.py services agents workflows   → exit 0
python -m pytest tests -q                                   → 106 passed, 2 warnings in 16.12s
```

`tests/test_transit_route_and_budget_layout.py` (**new, 23 tests**) covers: the journey is
stamped on the payload and every option; session values beat model output; group cost is
derived arithmetic; a USD quote is never relabelled as ₹ and is excluded from the INR budget;
`0`/missing fares become "no price"; normalization is idempotent and keeps `error` status;
legacy shapes still parse; same-city has no outbound leg; dangling recommendations are dropped;
`BudgetAgent` counts only INR, prices a return trip, and no longer mutates the payload; the
international prompt asks for INR + one-way; the Transport tab shows the route and never
hard-codes `₹`; `#budget-closing` is provably outside `.budget-container` (div-depth scan of the
markup) and styled full-width below; the email names the route and uses the right symbol.

One pre-existing assertion was retargeted: `test_transit_agent_degrades_gracefully_on_garbage_response`
required the legacy `{"flights": []}` shape (the bug); it now requires the canonical payload with
`provider_status == "error"` and the preserved origin/destination.

**Still needs a live run to confirm:** realistic *magnitudes* of the fares themselves — they are
LLM estimates, so the numbers only become observable with a real `GOOGLE_API_KEY`
(`python e2e_validate.py 200000`). What is now guaranteed by code and tests is that whatever is
quoted is displayed in the currency it was quoted in, attached to the named origin→destination
journey, and counted in the budget only when it is in rupees.
