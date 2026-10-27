# ExploreX — Implementation Report
### Bug fixing, backend stabilization and end-to-end workflow repair (10-phase spec)

This report documents what was actually changed, **why** (root cause), which tests were
executed and what they really returned. Nothing below is claimed as verified unless it was
exercised by the automated suite or by a live HTTP-contract run against the real Flask app.

---

## 1. How to run

```powershell
# 1) install / refresh dependencies. Python 3.13 is canonical: use .venv313
#    explicitly. The checked-in .venv / .venv311 / verify_env folders are stale
#    and must not be activated.
.\.venv313\Scripts\python.exe -m pip install -r requirements.txt

# 2) configure secrets (never commit the populated file)
copy .env.example .env       # then fill GOOGLE_API_KEY / GEOAPIFY_API_KEY / MAIL_* ...

# 3) start the app (Flask dev server on http://127.0.0.1:8000)
.\.venv313\Scripts\python.exe main.py

# 4) full regression suite (all external services mocked, image lookups offline)
.\.venv313\Scripts\python.exe -m pytest tests -q

# 5) compile-only sanity check of the backend
.\.venv313\Scripts\python.exe -m compileall -q main.py services agents workflows
```

---

## 2. Test results (actual)

> **Historical snapshot.** The counts in this section describe the first round. The current
> canonical suite is **191 passed** — see §11.8 for the final command and result.

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

---

## 8. Repair round — reported Japan trip (destination scope → itinerary → strict budget)

This round addressed the failures observed on the reported Japan trip. Everything below was
implemented in the existing architecture (Flask + workflow state machine + agents + services);
no endpoint, page, authentication flow, or agent was replaced. All edits remain uncommitted.

### 8.1 Reported symptoms mapped to root causes

| # | Symptom | Root cause found in code |
|---|---|---|
| 1 | A request for **Japan** returned places around Numata/Gunma, not Tokyo/Kyoto/Osaka. | `_process_information` geocoded the country name, received a centroid (~36.5748, 139.2394), and searched a 20 km radius around it. There was no country/region/multi-city concept. |
| 2 | **"Viewpoints and photography" produced `interest_based = 0`** despite Japan having many observation decks. | Interest matching used an English-only keyword map on the display name. The enrichment layer rebuilt each POI and deleted `osm_tags`/`types`/`metadata`, so `tourism=viewpoint` and Japanese names could never match. Popularity then dominated the ranking. |
| 3 | Web/RAG retrieval returned **0 POIs**, and when the model did answer, ratings/images were invented. | `json.loads` was called on the whole model response, so markdown fences/prose/concatenated JSON failed silently. The Pydantic schema required coordinates and defaulted `rating=4.5`, `review_count=100`, `price_level=2`; enrichment then added `4.0` ratings and random Unsplash place photos. |
| 4 | A strict **₹40,000 Japan budget appeared to fit**. | Indian Tier-2 INR costs were applied to every destination, feasibility accepted 65 % of that unrealistic minimum, and the optimiser shaved variable costs until the displayed total "fit". Intercity fares were multiplied by party size regardless of `cost_basis`; car-rental prices in any non-INR currency were treated as USD. |
| 5 | Provider failures either **erased valid results** or produced **fabricated places**. | The fallback path emitted static "City Center"/"Historical District" records and generic images whenever providers were empty. Partial provider success was not consistently exposed. |
| 6 | The final itinerary repeated places, dropped selected places, or reported false "not in itinerary" warnings. | The planner output was trusted with light string checks; days could be missing/out of range; duplicates were detected globally but reported as missing selections; route-agent repeats landed in the global unscheduled list. OSRM had no timeout, restaurant lookups repeated per stop, and a rental-API failure cleared `should_rent_car`. |
| 7 | No readiness endpoint; memory/socket growth risk. | No `/health`; the workflow dict was only loosely capped; `CarRentalService` leaked its SSL connection on 403/non-200/JSON errors. |
| 8 | The UI hid scope and infeasibility, and showed stock photos as if they were the place. | The frontend had no destination-scope disclosure, read only the raw budget dict, and substituted random Unsplash images when `image_url` was absent. |

### 8.2 Fixes by area

**Destination scope** — new `services/destination_resolver.py`
- Classifies `country / region / city / multi_city / unknown / invalid` from a curated,
  geographically bounded reference list, then the geocoder's feature type (`country`,
  `state`, `city`, …) when the name is not curated.
- A country is **never** collapsed to its centroid. For a supported country it returns a
  transparent, editable multi-city proposal (Japan → Tokyo, Kyoto, Osaka, Sapporo, Hakone,
  Nara). An unsupported country returns `needs_clarification` instead of guessing.
- `validate_point_for_scope()` checks the country bounding box **and** distance to a proposed
  city, so a Gunma POI cannot enter a Tokyo/Kyoto/Osaka scope.
- `workflows/travel_graph.py` uses the resolver in both chat and information steps, anchors
  weather/map on the first verified city, searches each proposed city independently
  (`InformationAgent.get_attractions_for_cities`), and returns `destination_scope` plus
  `provider_status` to the client. Known curated Indian cities are not sent through the
  geocoder-based scope path, so a temporary geocoder outage cannot turn them into a
  clarification loop.

**Interest matching** — new `services/interest_match.py`
- Canonical groups with multilingual aliases and evidence terms (including Japanese
  observation-deck/photo vocabulary), OSM tag evidence, category/type hints, and per-interest
  weights.
- Matching inspects structured fields (`osm_tags`, `tags`, `types`, `metadata`, category,
  description), not just the English display name, and returns human-readable reasons.
- `InformationAgent._calculate_score` is auditable: interest 0–40, provider quality 0–20,
  verified popularity 0–15, completeness 0–15, LLM advisory 0–10. No interest evidence means
  **0** interest points; popularity cannot rescue the mismatch. Labels are
  `interest_based` / `discovery` / `outside_interests` (the legacy `popular` label is no longer
  produced on the active or fallback recommendation paths).

**Retrieval and POI parsing** — new `services/structured_parse.py`
- Extracts JSON values from fenced blocks, prose-wrapped output, concatenated objects and
  Python-style literals; repairs trailing commas and smart quotes. Invalid output returns
  `[]` and never raises.
- `RecommendAgent.POIExtraction` no longer requires coordinates and no longer defaults
  rating/review/price values. `extract_pois_from_text` keeps unknown fields `None`, dedupes
  names case-insensitively, and rejects `N/A`-style names.
- Every model-suggested POI is geocoded and checked against the destination country/city
  before it can reach the map or itinerary. Unverified records are dropped with a reason, not
  silently converted into places.

**Strict budget** — `agents/budget_agent.py`, `services/budget_norm.py`,
`workflows/travel_graph.py`
- A destination profile applies a documented country cost index (Japan ×3.2, etc.) and
  international entry-fee/fuel/rental scaling to the existing INR baseline. Unknown countries
  use a conservative default **and disclose the assumption**.
- `_check_budget_feasibility` compares the stated cap with a realistic minimum built from the
  destination's low-cost floor plus selected attractions, rental/fuel and intercity fare. If
  the cap is below that floor, `budget_infeasible=True` and the warning names the minimum and
  the shortfall. The user's `budget_amount` is never rewritten or relaxed.
- Intercity cost uses the normalised one-way party `group_cost` (or `per_person` × travellers
  only when that is the stated basis), doubles it for the return leg, and honours an explicit
  user transport mode even when it is more expensive. Foreign rental quotes are converted with
  a documented static currency table instead of being assumed to be USD.
- `local_transport` is the single canonical category and already includes car rental + fuel;
  the workflow no longer adds those components a second time when a selected accommodation is
  applied.
- `budget_norm.normalize_budget()` exposes `min_viable_cost`, `cost_index`,
  `destination_profile`, `currency_assumptions` and `breakdown` so the UI/email can explain
  the number rather than hiding it.

**Providers and no-fabrication**
- Removed the synthetic static fallback POIs, generic place images, default ratings, and
  invented addresses. A missing value stays `None`/empty and is labelled unavailable.
- Provider results survive partial failures; `provider_status` records each provider's state
  and is returned to the client.
- Model fallbacks are used only when providers **and** RAG are empty, are clearly marked
  unverified, and must pass geocoding + scope validation.
- Overpass/Geoapify behavior and the existing retry/cooldown logic were preserved.

**Itinerary integrity** — `agents/strategy_agent.py`, `agents/route_agent.py`,
`workflows/travel_graph.py`
- `StrategyAgent._finalize_daily_plan()` deterministically creates days 1..N, removes
  hallucinated names and cross-day duplicates, keeps one instance of a free-time label, and
  re-inserts every selected attraction on the lightest day. The same path is used when the LLM
  fails, so a planner outage no longer produces an empty itinerary.
- `RouteAgent` dedupes places across days and records repeats as per-day
  `duplicate_across_days`, **not** in the global "selected but unscheduled" list. Every
  activity carries `entry_type` (`attraction` / `meal` / `free_time` / `accommodation`), and
  each day carries `routing_source`, `routing_status`, `routing_optimized` and `routing_note`.
- OSRM calls have a bounded timeout (`OSRM_TIMEOUT_SECONDS`, default 6 s) and fall back to the
  local distance heuristic with explicit provenance. Restaurant lookups are cached per ~1 km
  cell. `extract_rental_recommendation` and `get_ai_recommendation` now treat an explicit
  `rental_choice` as authoritative; a dead rental provider cannot erase the decision, only its
  live quote.
- Final validation re-sequences each day after filtering and reports genuinely unknown POIs
  with a reason.

**Health and resource cleanup** — `main.py`, `services/car_rental_api.py`
- Added `GET /health` and `GET /api/health`: secret-free configuration presence, uptime,
  workflow count and readiness checks, with no provider/LLM calls.
- `prune_workflows()` bounds the in-process workflow store (`MAX_WORKFLOWS`, default 500) and
  runs on session creation/reset.
- `CarRentalService` initialises `conn = None` and closes it in `finally` on every path,
  including 403/non-200/timeout/JSON errors.

**Frontend** — `frontend/static/js/main.js`, `frontend/templates/index.html`
- Renders a destination-scope disclosure (country proposal / region / multi-city /
  clarification) and partial-provider status instead of hiding them.
- Renders the strict-budget infeasibility warning, the realistic minimum and the cost/FX
  assumptions from the canonical normalized budget.
- Removed random Unsplash fallbacks and the mock exchange-rate path; a missing image now shows
  an explicit neutral "No photo available" placeholder.

### 8.3 Files changed

| File | Change |
|---|---|
| `services/destination_resolver.py` **(new, 798 lines)** | Country/region/city/multi-city classification, editable proposals, bbox + nearest-city scope validation, disclosure text. |
| `services/interest_match.py` **(new, 453 lines)** | Canonical multilingual interest groups, OSM/category evidence, deterministic `match_interests` / `interest_score`. |
| `services/structured_parse.py` **(new, 183 lines)** | Defensive JSON extraction, wrapper flattening, rating/name sanitisation. |
| `agents/information_agent.py` | Shared interest matcher and auditable score; honest enrichment; verified multi-city discovery; geocode/scope validation of generated POIs; removed fabricated fallbacks; OSRM timeout and routing metadata. |
| `agents/recommend_agent.py` | Honest POI schema/extraction; optional coordinates/ratings; dedupe; consistent interest labels. |
| `agents/budget_agent.py` | Country cost model, strict minimum feasibility, intercity cost basis and explicit-mode precedence, currency table, international entry/fuel/rental scaling, canonical category sums. |
| `agents/strategy_agent.py` | Deterministic daily-plan finalisation, day 1..N guarantee, duplicate/hallucination removal, selected-place re-insertion, rental-choice precedence. |
| `agents/route_agent.py` | Cross-day dedupe with honest per-day reasons, entry types, routing provenance, meal cache. |
| `workflows/travel_graph.py` | Scope resolution and multi-city search, per-city accommodation/restaurants, destination-aware budget integration, strict advisory without aborting, final validation, recommendation labels, known-Indian-city fallback. |
| `services/budget_norm.py` | Normalized output carries minimum cost, cost index, destination profile, FX assumptions and breakdown. |
| `services/geocoding.py` | `feature_type`, `place_type` and `bbox` from Geoapify/Nominatim/Photon for scope classification. |
| `services/car_rental_api.py` | Socket closed on every path, including 403/non-200/timeout/JSON errors. |
| `main.py` | `/health` and `/api/health`, bounded workflow pruning, `destination_scope`/`discovery_status` in `/api/state`, email-configured check covers `MAIL_SERVER`. |
| `frontend/static/js/main.js` | Scope/provider disclosure, strict-budget minimum and assumptions, honest image placeholder, no mock FX. |
| `frontend/templates/index.html` | `#scope-disclosure` container. |
| `tests/test_repair_acceptance_a_h.py` **(new, 20 tests)** | Required A–H acceptance matrix, fully mocked/offline. |

### 8.4 Verification (actual commands and results)

Baseline before this round: **106 passed, 2 warnings**.

After the fixes:

```text
python -m pytest tests -q
126 passed, 2 warnings in 21.26s

python -m pytest tests/test_repair_acceptance_a_h.py -q
20 passed, 2 warnings in 3.06s

python -m compileall -q agents services workflows main.py
exit 0, no output

git diff --check
clean (only Git's LF→CRLF advisory warnings)
```

The two warnings are pre-existing and unrelated: `google.api_core` Python 3.11 EOL
`FutureWarning`, and `flask_session.FileSystemSessionInterface` `DeprecationWarning`.

### 8.5 Acceptance tests A–H

| Group | Coverage |
|---|---|
| **A — Destination scope** | Japan is a `country` with a disclosed, editable Tokyo/Kyoto/Osaka/Sapporo/Hakone/Nara proposal; Tokyo/Kyoto/Paris scope validation; city/region/multi-city preservation; unknown country asks for a region/city instead of a centroid. |
| **B — Interest matching** | "Viewpoints and photography" normalises to canonical groups; a Japanese observation deck matches through OSM tags; a highly rated shopping mall earns zero interest points; labels are honest. |
| **C — Retrieval parsing** | Fenced/prose-wrapped JSON with trailing commas; concatenated JSON values; Python literals; nested wrappers; unknown rating/location remain `None`; `N/A` names and case duplicates removed. |
| **D — Strict budget** | A ₹40,000 Japan trip is declared infeasible with a minimum and shortfall; no-limit trips report `within_budget=None`; explicit flight choice beats a cheaper train; the user's cap is unchanged. |
| **E — Provider failure** | Total provider + LLM failure returns `[]` with no fabricated "City Center"; partial provider success survives with no invented fields; generated POIs are geometry/country checked. |
| **F — Itinerary integrity** | Every selected place appears exactly once, days 1..N exist, hallucinated names are dropped, a repeat is reported per day not globally, routing provenance is present. |
| **G — Health/resources** | `/health` is secret-free and 200; workflow pruning evicts oldest entries; car-rental socket closes on 403 and the provider is disabled. |
| **H — Mocked Japan E2E** | Country proposal → Tokyo/Kyoto/Osaka POIs (Numata rejected) → strict ₹40,000 infeasible with complete itinerary → every selected spot scheduled once → transit and provider status preserved → normalized budget contract consistent. |

### 8.6 Deliberate limitations / not verified live

1. **The original acceptance suite mocks live providers and LLMs.** Gemini, Geoapify,
   Overpass, RapidAPI and SMTP are mocked there, so endpoint behavior is verified but quota and
   model-quality behavior is not. A later live run against real providers is recorded in
   section 10; it found and fixed additional defects that mocks cannot see.
2. **Cost indices and FX rates are deliberately static and indicative.** They are disclosed in
   the UI/report and are not live quotes. The ₹40,000 Japan trip is intentionally still
   infeasible once realistic flights are included; the product now says so instead of
   inventing a cheaper plan.
3. **The country proposal list is curated, not exhaustive.** It contains well-known cities for
   supported countries and is editable; it is not a complete tourism inventory.
4. **Frontend was not browser-executed here.** Node.js is absent, so `main.js` was checked by
   the existing source-level pytest guards plus Python syntax/brace checks of the edited
   functions. A live browser smoke test is still worthwhile.
5. **No bookings are made.** Accommodation, transit and car-rental figures remain planning
   estimates; the UI/email never label them as confirmed bookings.
6. **No commit was made.** All changes are in the working tree for review.

### 8.7 Commands used

```powershell
python -m pytest tests -q
python -m pytest tests/test_repair_acceptance_a_h.py -q
python -m compileall -q agents services workflows main.py
git diff --check
git status --short
```


---

## 9. Python 3.11 -> 3.13 runtime migration

### 9.1 Target environment (verified)

| Item | Value |
|---|---|
| Interpreter | CPython **3.13.15** (64-bit, MSC v.1944) |
| Path | `C:\Users\phani\AppData\Local\Programs\Python\Python313\python.exe` |
| Virtual environment | `.venv313` (created with `py -3.13 -m venv .venv313`) |
| Package manager | pip 26.2.2 inside `.venv313` |
| Old environment | `.venv311` (CPython 3.11.9) was the rollback option and was **deleted** after the final green run (11k+ files, ~1.1 GB). No user-created files existed in it, and nothing in the repo referenced it. |

Python 3.13 was installed from the official python.org installer through winget
(`Python.Python.3.13`, user scope). Python 3.11 remains installed system-wide; it was **not**
uninstalled.

### 9.2 Blockers found by inspection (not guessed)

| Dependency | Problem on CPython 3.13 / Windows x64 | Resolution |
|---|---|---|
| `chromadb==0.4.24` -> `chroma-hnswlib==0.7.3` | No cp313 wheel for `chroma-hnswlib`; it cannot be built without a C++ toolchain | Moved to **`chromadb>=1.0.20,<2`** (1.5.9 installed). The 1.x Windows wheel is `cp39-abi3` and bundles its own backend, so `chroma-hnswlib` is no longer required. |
| `numpy>=1.24,<2.0` | NumPy 1.26 has no cp313 wheels (support starts at 2.1) | Moved to **`numpy>=2.1,<3`**. The code uses no alias removed in NumPy 2 (`np.float_`, `np.int_`, `np.alltrue`, ... were grepped). |
| `langchain-chroma 0.2.2` | Requires `chromadb<0.7` | Moved to **`langchain-chroma>=0.2.6`** (0.2.6 installed), which requires `chromadb>=1.0.20` and `numpy>=2.1` on 3.13. |
| `langchain-core 0.2.x` | Required by the old `langchain-chroma`; the 0.3 line is required by the new one | Moved to **`langchain>=0.3.27` / `langchain-core>=0.3.79`**. `langchain_core.pydantic_v1` still exists in 0.3, but the project's own schema now imports Pydantic v2 directly. |
| `langchain-huggingface 0.0.3` | Requires `langchain-core<0.3` | Moved to **`>=0.1.2,<0.2`**. |
| `langchain-google-genai` | 2.1+ switches to the new `google-genai` SDK, but `utils.ask_openai()` used `google.generativeai` | **Superseded by §11.4.** Pinned **`>=2.1.12,<2.2.0`** together with **`google-genai>=1.65.0,<3.0.0`**; `utils.ask_openai()` was rewritten against `from google import genai` and the retired `google-generativeai` package was removed. 3.x/4.x remain avoided because they require `langchain-core>=1.1/1.4`. |
| `langchain` / `langchain-community` 0.2 | Conflicted with core 0.3 | `langchain` moved to 0.3; `langchain-community` was dropped because **no project file imports it** (grep-verified). |

`pip check` in `.venv313`: **No broken requirements found.**

### 9.3 Code and configuration changes

| File | Change |
|---|---|
| `requirements.txt` | Rewritten for the verified 3.13 set with comments explaining each floor (NumPy 2.1, ChromaDB 1.x, LangChain 0.3, `langchain-google-genai` 2.1.x + `google-genai`). Still resolvable on 3.11. |
| `agents/recommend_agent.py` | POI schema now imports `BaseModel`/`Field` from Pydantic v2 instead of the deprecated `langchain_core.pydantic_v1` shim (the shim is removed in langchain-core 1.0). |
| `frontend/static/js/main.js` | Prime the assistant rail on the anonymous landing page so the static "Loading your trip…" placeholder is replaced by the real empty state (verified in a browser). |
| `tests/test_python313_runtime.py` **(new)** | Guards the runtime (>=3.13), the binary dependency floors (NumPy >=2.1, ChromaDB >=1), the Flask/agent/model import stack, a real ChromaDB 1.x add/get/query/delete round-trip in a temp directory, and the RAG corpus's availability for re-ingestion. |
| `tests/test_repair_acceptance_a_h.py` | Added `test_h2_final_client_payload_and_email_use_the_same_itinerary`: runs the mocked Japan trip through `/api/state` and `/api/email`, asserting day numbers, map coordinates, budget identity and the email payload all come from the same server-side itinerary. |
| `.gitignore` | Added `.venv313/` and `data/vector_db_backup_*/`; both environments stay untracked. |
| `verify_env/pyvenv.cfg` | Untracked (`git rm --cached`). It is a stale environment descriptor pointing at the non-existent `C:\Users\PHANINDHRA\...` interpreter. The file stays on disk; only its Git tracking is removed. |
| `.python-version` **(new)** | `3.13` for version managers/tooling. |
| `README.md`, `TECHNICAL_DOCUMENTATION.md` | Python 3.13 / `.venv313` setup, vector-database rebuild step, health check and test commands. |
| `diag_chat_step.py` | Diagnostic docstring points at `.venv313`. |

### 9.4 Vector store migration

`data/vector_db` was built by ChromaDB 0.4.24. It was backed up to
`data/vector_db_backup_0_4_24` (gitignored) and rebuilt with ChromaDB 1.5.9:

```text
python services/document_ingestion.py --rebuild
[SUCCESS] Ingested 52 India knowledge documents into data/vector_db
```

A real similarity query (`vegetarian food in goa`) returns the Goa
`accessibility_food` document, and `RetrievalAgent` loads the collection and answers an Agra
query in ~0.1 s.

### 9.5 Verification (actual results)

```text
.venv313\Scripts\python.exe --version          -> Python 3.13.15
.venv313\Scripts\python.exe -m pip check       -> No broken requirements found.
.venv313\Scripts\python.exe -m compileall -q main.py agents services workflows tests -> OK
.venv313\Scripts\python.exe -m pytest tests -q -> 155 passed in 24.26s
```

Backend, production-style (Waitress on 127.0.0.1:8018):

```text
GET /health      -> 200 {"status":"ok", ... "workflow_count":0}
GET /api/health  -> 200
GET /            -> 200 (38118 bytes)
GET /api/state   -> 200
GET /api/assistant -> 200
GET /static/js/main.js -> 200 (169196 bytes)
```

Browser (real Chromium session against the running app):

* landing page renders, `main.js` loads with **no uncaught JavaScript errors**;
* guest sign-in (`Continue as Guest`) reaches `/` with the app view and a working assistant rail;
* all literal `/api/...` paths referenced by `main.js` exist in Flask's URL map;
* the only console errors are blocked external assets (`tile.openstreetmap.org`,
  `ui-avatars.com`) because the browser's network is tunnelled — not application errors.

End-to-end (mocked providers/LLM): the Japan trip runs discovery -> selection -> strict-budget
advisory -> itinerary -> route -> `/api/state` -> `/api/email`, and every selected attraction
appears exactly once with real coordinates and the same total in the page and the email payload.

### 9.6 Known limitations / left as-is

1. **The migration verification itself used mocked providers.** The suite mocks Gemini,
   Geoapify, Overpass, RapidAPI and SMTP. A subsequent live end-to-end run (section 10) exercised
   the real providers; it is the authoritative check of live behavior, not this section.
2. **`google-generativeai` is end-of-life** and was **removed in this round** (see §11.4).
   `langchain-google-genai` 2.1.12 and `utils.ask_openai()` now both use the supported
   `google-genai` SDK (`google.ai.generativelanguage` under LangChain), so no project file
   imports the retired package.
3. **The vector database must be rebuilt once** whenever the ChromaDB major version changes
   (documented in README and the requirements file).
4. **Python 3.11 was not uninstalled.** No other Python 3.11 virtual environments were found
   under the user's Desktop/Documents/source/repos at depth 4, but a machine-level uninstall
   needs explicit confirmation and is not required for ExploreX to run.
5. **No Node.js/npm build exists** for this project (vanilla JS served by Flask), so there is no
   frontend toolchain to migrate. Browser assets that need the public internet remain
   environment-dependent.
6. **`.venv` and `verify_env` remain on disk.** Both are pre-existing, broken environments whose
   `pyvenv.cfg` points at the non-existent `C:\Users\PHANINDHRA\...` interpreter. Neither is used
   or referenced; they are gitignored and can be deleted (`Remove-Item .venv,verify_env -Recurse`).
7. **`data/vector_db/chroma.sqlite3` is tracked by Git** even though `data/vector_db/` is ignored
   (the ignore rule was added after the file was committed). Its rebuilt ChromaDB 1.x version is
   part of this change. It can be untracked later with
   `git rm --cached -r data/vector_db` because it is fully regenerable from
   `services/document_ingestion.py --rebuild`.
8. **Nothing was committed.** All changes are in the working tree/index for review; the single
   staged entry is the removal of the tracked `verify_env/pyvenv.cfg`.

### 9.8 Cleanup performed after verification

* `.venv311` deleted (28,261 files / ~1.1 GB). It contained only `Include/ Lib/ Scripts/ share/
  pyvenv.cfg` — no user-created files — and no script or documentation still referenced it.
* Temporary `server313*.log` files and the background Waitress processes used for the HTTP checks
  were removed.
* `verify_env/pyvenv.cfg` untracked from Git (the working-tree folder is untouched).
* The full suite was re-run **after** the deletion (155 passed after later additions) and the backend was restarted from
  `.venv313` on a clean port to prove nothing depended on the old environment.

### 9.7 Commands for the new environment

```powershell
cd C:\Users\phani\Desktop\ExploreX_Project\ExploreX-Intelligent-collaborative-agents-for-dynamic-personalized-travel-planning
py -3.13 -m venv .venv313
.\.venv313\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python services/document_ingestion.py --rebuild
python -m pytest tests -q
python main.py
```

---

## 10. Live end-to-end repair round (real providers, 2026-10-25)

This round started from an end-to-end failure in the chat hand-off and ended with two complete
live runs against the real Gemini/Geoapify/Nominatim/Wikipedia endpoints. The server ran in
development mode on `127.0.0.1:8010`; `PRODUCTION=1` was deliberately not set because it marks
the session cookie `Secure`, which a plain-HTTP test client will not send back.

### 10.1 Scenario exercised live

| Input | Value |
|---|---|
| Destination | Tokyo (typed as a city, not a hard-coded test fixture) |
| Duration / party | 5 days, 10 travellers, no children |
| Origin | Bengaluru |
| Budget wording | "high budget" (no numeric cap) |
| Interests | photography and viewpoints |
| Start date | first stated as flexible, then 2026-11-01 |

The driver used the real HTTP/SSE API (`/api/stream` and `/api/state?session_id=...`), not the
in-process workflow. Both runs followed: chat extraction → information/discovery → recommend →
selection → strategy → communication → route → `next_step="complete"`.

### 10.2 Provider results observed live

| Provider | Result |
|---|---|
| Geoapify | `ok:81` POIs |
| Overpass | `empty` — every mirror timed out or returned 502; the app continued and disclosed it |
| Wikipedia notability | `ok:16` notable POIs |
| Merged/deduplicated | 97 POIs before ranking |

The first 10–15 ranked cards were recognisable, verifiable places (Tokyo Tower, Tokyo Skytree,
Sensō-ji, Zōjō-ji, Tsukiji fish market, Hama-rikyū Gardens, Akasaka Palace, The National Art
Center, Shiba Park, Tsukiji Hongan-ji). Previously the same query returned 51 obscure
neighbourhood parks and community halls.

### 10.3 Final contract observed live

| Check | Observed |
|---|---|
| `next_step` | `complete` |
| Itinerary days | 5 (01–05 Nov 2026) |
| Selected attractions | 10, all Wikipedia-backed records with IDs and coordinates |
| Selected attractions scheduled | **10 / 10**, `unscheduled_attractions=[]` |
| Map coordinates | 10/10 activities carry numeric latitude/longitude |
| Accommodation options | 12 live records; none exposed a nightly price, so the budget used the disclosed destination average |
| Transit | 2 flight options plus local modes, each explicitly marked `estimated`, not a booking quote |
| Intercity cost used | ₹650,000 one-way for 10 travellers × 2 = ₹1,300,000 (the cheaper of the two returned flights) |
| Budget | ₹2,256,100 total for 10 people × 5 days; categories accommodation ₹400,000, food ₹224,000, local transport ₹88,000, activities ₹39,000, intercity ₹1,300,000, miscellaneous ₹205,100 |
| Budget cap | `budget_limit=None`, `within_budget=None` — the user gave a level, not a numeric cap |
| `/api/state` | returned the same 5 days, 10 selected attractions and ₹2,256,100 total via the explicit `session_id` query parameter |

Both runs produced the same total and the same 10/10 placement. A live spot check of the
notability provider outside Tokyo returned 23 Kyoto results (Nijō Castle, Kyoto
Imperial Palace, Kyoto Tower, Daisen-in, Honnō-ji, Arashiyama, …; a later filter also removes
event pages such as "Siege of Sanjō Palace") and 7 for Darjeeling (Tiger Hill, Rock Garden,
Padmaja Naidu Himalayan Zoological Park, Lloyd's Botanical Garden, …), which confirms the layer
is destination-driven rather than Tokyo-specific.

### 10.4 Defects found only by the live run

1. **Chat extraction never reached the workflow** because the Gemini tool schema's root title
   became the function name. Fixed by keeping the root `TravelState` title and stripping only
   property-level titles; verified with a real extraction returning `TravelState`.
2. **Discovery ranked obscure OSM nodes above famous sights.** Fixed with the Wikipedia
   notability provider (text search + curated category + GeoSearch seeded from search results),
   tourism-evidence filtering, accent-insensitive cross-provider deduplication and a ranking
   bonus. Station/ward/company/school/stable/event pages are rejected; live tests caught
   "Siege of Sanjō Palace", which is now filtered as an event rather than a place.
3. **The recommend step sometimes returned no `state`**, so `/api/state` could not reconstruct
   the selection screen. Every branch now returns `state`.
4. **`/api/state` depended on a cookie**, which SSE/plain-HTTP clients do not reliably send.
   It now accepts `?session_id=`.
5. **`duration_days` was read where the chat flow writes `days`**, pinning the required-POI
   budget to the 3-day default for every trip. All three sites now prefer `days`.
6. **The UI silently auto-selected every attraction** when the user clicked Generate. First
   click now validates and warns; a second click is the explicit override. A separate
   "Let AI Choose" action uses the backend's explicit `go ahead` path.
7. **The E2E harness itself asserted a non-existent raw-budget key** (`budget["total"]`).
   `BudgetAgent` emits `estimated_total`, and `normalize_budget` exposes `total`. The assertion
   was corrected; this was a harness bug, not an application defect.

### 10.5 Still unverified

* **Browser rendering of the new selection flow.** No Node.js/browser automation was available
  in this round; the JavaScript was reviewed and the backend contracts it calls are covered by
  tests, but a manual browser click-through is still worthwhile.
* **Email delivery** (SMTP credentials) and the visual result in Gmail/Outlook.
* **OSRM routing and map tiles** for the live run; coordinates and the normalized day contract
  were verified, but the route provenance is estimated locally when OSRM is unavailable.
* **Accommodation prices** — the live provider returned no nightly rates, so the budget's
  destination-average accommodation figure is an estimate, not a quote.
* **A strict numeric cap** was not part of this live scenario; strict-limit behaviour remains
  covered by mocked acceptance tests.
* **Overpass success** — all mirrors failed during both runs, so the combined Geoapify +
  Wikipedia path was what actually produced the trip.

The canonical suite after these changes: **155 passed** (24–34 s across consecutive runs).

---

## 11. Repair round 2 — images, discovery breadth, SDK migration, navigation (2026-10-26)

This round fixed the defects that survived §10 and validated them against the real providers with a
second, stricter acceptance scenario: **Tirupati → Kerala, 5 days, 10 adults, 0 kids, good health,
beaches + hill stations + viewpoints, flexible date, ₹100,000 strict budget**. Unlike §10, the run
went all the way to `next_step="complete"` and produced a final itinerary and budget.

### 11.1 Root causes and fixes

| Reported symptom | Actual root cause | Fix | Where |
|---|---|---|---|
| `[ATTRACTION_PIPELINE] city=Kochi status=provider_failure error='int' object has no attribute 'lower'` | Provider payloads were treated as strings. Geoapify `name`, `categories`, OSM tag values and Wikipedia titles can be numbers; `.lower()`/`.strip()` on them raised inside the city worker and discarded **that city's whole result**. | Every provider boundary now goes through `_coerce_text` / `_coerce_float`; numeric-only names are rejected as unattributable, malformed records are dropped individually, and each city is isolated in its own try/except so one bad record/city cannot abort the state search. | `services/poi_fallback.py` (`_coerce_text`, `_coerce_float`, Geoapify/OSM/Wikipedia validators), `agents/information_agent.py` (`_coerce_coord`) |
| Attraction cards showed stock photos of unrelated places, or broke | Attractions were enriched with Pexels by name, then by **category** ("temple", "beach"), plus random Unsplash fallbacks. A generic photo was attached to a named place and the UI had no safe failure path. | Removed unconditional stock photos from the attraction path. Added a verified-only resolver: normalise provider URLs, or resolve the Wikipedia lead image **only** when the article title matches and (when coordinates exist) the article is inside the destination radius. Otherwise `image_url` stays `null`. (Round 3 later added a relevance-gated Pexels fallback **after** every verified source; see §11.12.) | `services/attraction_images.py` (new), `agents/information_agent.py`, `frontend/static/js/main.js` |
| Recommendation list was always small/arbitrary | Hard caps (`[:50]`, fixed `total=20`), a `state_city = state_name` overwrite that erased per-city spread, and single-word substring matching. | Duration-aware target `max(60, min(180, days*12))`, a balanced per-city cap, per-city attribution (`state_city=<city>`, `state=<state>`), canonical `interest_based`/`discovery`/`outside_interests` labels, and a shared plural-aware matcher (beaches, hill stations, backwaters, waterfalls, viewpoints). No `[:50]` truncation anywhere. | `agents/information_agent.py`, `agents/recommend_agent.py`, `services/interest_match.py`, `workflows/travel_graph.py` |
| Discovery step took ~470 s and hammered every Overpass mirror | Each city retried 3× with ~25 s reads across 4 mirrors, and the same cascade repeated for accommodation/restaurant queries. | Live cooldown re-check, a 40 s per-query wall-clock budget, a process-wide circuit breaker (2 failed queries → 90 s), faster 429 failover, and a 30-minute successful-query cache. | `services/poi_fallback.py` |
| Deprecated Gemini SDK | `utils.ask_openai()` imported `google.generativeai` (end-of-life). | Migrated to the supported `from google import genai` client with a cached client and `types.GenerateContentConfig`; prompts, model, temperature and token limits unchanged; the API key is read from the environment only. `google-generativeai` removed from `requirements.txt`. | `utils.py`, `requirements.txt` |
| Chat/progress moved the user to another tab unexpectedly | `switchTab()` was called from the SSE `status` handler, `handleComplete`, and boot code, so any backend `next_step`/`ui_action` yanked the user out of Explore/Itinerary. | Backend progress is now **data only** (`state.workflowStep`). Only explicit user actions change tabs (tab click, "View Attractions", chat action buttons, map/itinerary buttons). The last user-chosen tab is persisted in `sessionStorage.explorex_tab` and restored after a reload. | `frontend/static/js/main.js` |

### 11.2 Image pipeline, end to end

`POIManager` (provider image URL / OSM tags) → normalisation/dedup → `enrich_poi_images()`
(staged, verified lookup) → `TravelState.attractions[].image_url` → `/api/stream` JSON → card DOM.

* **Verified only.** Exact OSM/Geoapify wiki identifiers are trusted. A name-based match is accepted
  only when the page/file title matches the place and, when coordinates exist, the article/file is
  within a tight radius (3 km for Wikipedia GeoSearch, 5 km for name search, 300 m for a Commons
  file). Disambiguation pages, generic filenames, city-only filenames and far-away articles are
  rejected. A POI whose whole name is a type (`Temple`, `CSI Church`, `Beach`) can only match the
  identical article/file title, and a town article/file is never attached to a place that merely
  contains the town name, so neither can borrow a neighbour's photo.
* **First verified match wins.** Stages run strongest-first and `_set_image()` never overwrites an
  existing image, so a weak later guess cannot replace an exact-identifier photo.
* **Stages (batch first).** Provider image → OSM tags → exact Wikipedia titles → Wikidata `P18` →
  Commons files/categories → Wikipedia GeoSearch → name search → Commons GeoSearch fallback (at most
  eight cluster requests). Batch APIs are used for clusters, not one request per POI.
* **Normalisation.** Protocol-relative URLs are repaired, non-`http(s)` schemes are rejected, and
  existing provider URLs are preserved through deduplication.
* **Caching.** Positive results use `ATTRACTION_IMAGE_CACHE_TTL` (7 days); misses use
  `ATTRACTION_IMAGE_NEGATIVE_TTL` (1 h). Failed or rate-limited lookups are not cached as negatives.
* **Politeness and latency.** 0.5 s between general requests, 1.0 s between the expensive
  `generator=search` calls, a 5 s request timeout, a size-derived search budget
  (`max(24, min(120, batch))`), a 10 s search-only 429 cooldown with one bounded retry, and a
  120 s wall-clock deadline (`ATTRACTION_IMAGE_DEADLINE`) for the search + Commons stages so a bad
  Wikimedia day cannot stall the plan. Skipped places keep `image_url=null` and are retried later
  (no negative cache).
* **On-demand endpoint.** `GET /api/attraction-image?name=&city=&country=&lat=&lng=` returns a
  verified URL or `null`, plus resolver diagnostics.
* **Frontend.** `loading="lazy"`, `decoding="async"`, `referrerpolicy="no-referrer"`, a one-shot
  `onerror`, and a neutral inline SVG "No verified photo available" placeholder (`PLACEHOLDER_IMG`).
  There was no Unsplash/Pexels fallback for attractions in this round (round 3 later added a relevance-gated Pexels fallback; see §11.12). Cards render 12 at a time with a "Load more"
  button. The display is a **stable image-first partition**: cards with a verified photo render before
  placeholders, while the recommendation order (and auto-selection) is unchanged.
* **Test isolation.** `conftest.py` sets `ATTRACTION_IMAGE_LOOKUP=0`, so the suite never calls
  Wikimedia; the dedicated tests enable it and mock the HTTP session. `enrich()` now honours the
  switch with an explicit offline path instead of relying on each stage to fail.

### 11.3 Provider hardening (the `.lower()` crash and partial failures)

* `_coerce_text(value)` handles `None`, numbers, bools, lists and dicts; numeric-only names are
  treated as missing instead of being rendered as "12345".
* Geoapify `features` must be a list; OSM `elements` must be a list; Wikipedia pages must be dicts.
  A wrong container type degrades that provider to `[]` (recorded as `provider_status`) without
  discarding the others.
* `_coerce_float` rejects non-finite values, so NaN/Infinity coordinates cannot poison dedup or
  distance checks.
* Deduplication is keyed on a normalised name/id pair and skips records without a usable key, so a
  single malformed record cannot abort the merge.
* `_get_category_filters` accepts a non-string hobbies value (list/tuple/None) instead of calling
  `.lower()` on it.

### 11.4 Google SDK migration (exact versions)

| Package | Version installed | Notes |
|---|---|---|
| `google-genai` | **2.33.0** | Used by `utils.ask_openai()` via `from google import genai`. |
| `google-ai-generativelanguage` | **0.12.1** | Transport used by `langchain-google-genai`. |
| `langchain-google-genai` | **2.1.12** | Requires `langchain-core` 0.3; does not import `google.generativeai`. |
| `langchain-core` | **0.3.86** | Kept on the 0.3 line (3.x/4.x Gemini integrations require ≥1.1/1.4). |
| `google-generativeai` | **removed** | `pip check` clean; no project file imports it (test-enforced). |

`ask_openai()` keeps its historical name and `{"answer": ...}` contract. A cached
`genai.Client` is created from `GEMINI_API_KEY` (falling back to `GOOGLE_API_KEY`); the key is never
printed or embedded in an exception.

### 11.5 Live evidence — Kerala strict-budget acceptance

Driver: a real `TravelGraph.process_step` loop (no Flask, no browser) using the real Gemini,
Geoapify, Nominatim, Overpass, Wikipedia, transit and budget providers. Session
`86586e0123ccb0a21c2961c8d9af555b`, total wall clock **671.1 s**.

**Chat extraction (exact, no fabrication):**

```json
{"origin_city": "Tirupati", "city": "Kerala", "days": 5, "budget_amount": 100000,
 "budget_strictness": "strict", "people": 10, "kids": 0, "health": "good",
 "hobbies": "beaches, hill stations and viewpoints", "start_date": "flexible"}
```

**Step timings:**

| Step | Result | Duration |
|---|---|---|
| chat | → information | 25.6 s |
| information | 5/5 Kerala cities succeeded, 417 raw POIs → 60 candidates | 325.8 s |
| retrieval | 4 RAG POIs added | 65.8 s |
| recommend | 64 recommendations, stops for explicit selection | 0.0 s |
| recommend (selection submitted) | → strategy | 0.0 s |
| strategy | daily plan + rental recommendation, stops for confirmation | 77.0 s |
| strategy (confirmed) | → communication | 0.0 s |
| communication | transit + budget + tips, stops for confirmation | 110.4 s |
| communication (confirmed) | → route | 0.0 s |
| route | final itinerary + budget, `next_step="complete"` | 66.5 s |

**Discovery and ranking (real data):**

* 5/5 cities succeeded: Kochi 96, Munnar 70, Alleppey 77, Thiruvananthapuram 89, Wayanad 85 POIs.
* 417 deduplicated POIs → 60 candidates (duration-aware target for 5 days).
* Recommendation labels: **20 `interest_based`**, **24 `discovery`**, **20 `outside_interests`**.
* Sources: Wikipedia 22, Geoapify 38, RAG 4 (Pexels was not used for attractions in this run).
* City spread: **12 each** for Kochi, Munnar, Alleppey, Thiruvananthapuram and Wayanad, plus 4
  state-level RAG POIs — the balanced cap works in a live state search.
* Top matches were genuine: Western Ghats, Anamudi, Eravikulam National Park, Anaimalai Hills,
  Mattupetty Dam, Alappuzha Beach, Kovalam, Cherai Beach, Pookode Lake.

**Images:** 22 of 60 attractions received a verified image; 38 stayed `null` because Wikipedia was
rate-limiting (`[ATTRACTION_IMAGES] resolved=22 missing=38 state=rate_limited`). Those 38 cards
render the placeholder — no substitute photo was invented. All 10 scheduled activities that are real
places carry an image; the synthetic "Lunch Break" block has none.

**Final contract:**

```
next_step       = complete
selected        = 10 attractions (all 10 scheduled; unscheduled = [])
itinerary       = 5 days, 2/4/2/2/1 activities per day, days 1..5 present
transit         = train, 4 intercity + 3 local options (status=ok)
budget_type     = strict
budget_limit    = 100000
estimated_total = 94578
within_budget   = true
budget_infeasible = false
budget_warning  = null
remaining       = 5422 (94.6% utilisation)
```

Strict budget breakdown (from the backend, not recomputed by hand):

| Category | Amount |
|---|---|
| Intercity transport (train, round trip) | ₹24,000 |
| Accommodation (₹800/room/night × 5 rooms × 5 nights) | ₹20,000 |
| Food (₹350/person/day × 10 × 5) | ₹17,500 |
| Paid attractions | ₹15,000 |
| Local transport = self-drive rental ₹7,500 + fuel ₹1,980 | ₹9,480 |
| Miscellaneous (10% of subtotal) | ₹8,598 |
| **Total** | **₹94,578** |

The strict cap was **met** in this scenario, so the shortfall path was not triggered live. That path
is covered by
`test_strict_kerala_budget_keeps_the_users_cap_and_declares_shortfall`: an impossible cap must still
return a complete itinerary, keep `budget_limit`, set `budget_infeasible`, and explain the exact
excess instead of silently trimming the plan.

**Provider status captured at completion:** `geoapify: ok`, `wikipedia: ok` (partial),
`overpass: cooldown` (circuit breaker open), `accommodation_geoapify: ok`, `restaurant: degraded`.
The plan completed on partial provider data, which is the intended contract.

### 11.6 What the live run proved about resilience

* **Overpass never succeeded** (timeouts, HTTP 429 and 502 on every mirror). The circuit breaker
  opened after two failed queries and later queries — including accommodation and restaurant
  lookups — skipped immediately instead of repeating the timeout cascade. This is visible in the log
  as `Circuit breaker open; skipping this query (non-fatal)`.
* **Wikipedia was rate-limited** for most cities. Each 429 aborted the optional enrichment for that
  city immediately rather than retrying; partial notability results were kept.
* The discovery step still produced 60 candidates and 12 per city from Geoapify + partial Wikipedia.
* The rental provider returned **HTTP 403**, so the budget used a clearly labelled fallback estimate
  (`car_rental_source="fallback_estimate"`) rather than inventing a quote.
* No email was sent because the scenario supplied no address (the log states this explicitly).

### 11.7 Navigation decoupling (contract)

`frontend/static/js/main.js`:

* `state.workflowStep` records the backend's `current_step`/`next_step`; it never calls `switchTab`.
* `switchTab(tabId, {persist})` is only called from user-initiated handlers (nav clicks, chat action
  buttons, "Generate Itinerary", "View Attractions", map/itinerary buttons, reset, boot restore).
* The boot path restores the **user-chosen** tab from `sessionStorage.explorex_tab`; it does not
  infer a tab from workflow progress.
* Wait-for-user barriers are unchanged: `recommend` stops for a selection, `strategy` stops for a
  confirmation, `communication` stops before route planning. The UI triggers the next step but does
  not change the visible tab.

This is enforced by `test_backend_progress_never_switches_frontend_tabs` (source contract) and
`test_sessions_keep_independent_trip_states`.

### 11.8 Tests and commands (actual results)

```powershell
.\.venv313\Scripts\python.exe -m pytest tests -q
# 191 passed, 1 warning in 56.25s

.\.venv313\Scripts\python.exe -m compileall -q main.py services agents workflows
# exit 0
```

The single warning is the pre-existing `flask_session.FileSystemSessionInterface` deprecation.

New regression file `tests/test_attraction_images_navigation_and_discovery.py` (36 tests):

| Area | Tests |
|---|---|
| Image normalisation / verified lookup / negative cache / 429 cooldown | `test_image_url_normalisation_rejects_unsafe_values`, `test_resolver_keeps_provider_image_without_a_lookup`, `test_resolver_uses_only_a_verified_wikipedia_lead_image`, `test_resolver_rejects_unrelated_titles_and_far_coordinates`, `test_resolver_negative_result_is_cached`, `test_resolver_stops_lookups_after_a_rate_limit` |
| Wikimedia verification: generic names, Commons proximity/name, stage priority, caps | `test_commons_file_confidence_rejects_generic_city_and_distant_photos`, `test_commons_geosearch_resolves_a_geotagged_named_photo`, `test_commons_geosearch_failure_is_not_cached_as_a_negative`, `test_generic_names_cannot_borrow_a_nearby_article_or_photo`, `test_first_verified_image_wins_and_is_never_overwritten`, `test_commons_categories_skip_places_already_resolved_by_a_file`, `test_commons_geosearch_has_a_hard_cluster_cap` |
| Budget, deadline, offline switch | `test_disabled_image_lookup_preserves_provider_images_without_network`, `test_search_budget_prefers_distinctive_names`, `test_search_stage_respects_the_wall_clock_deadline` |
| Provider type-safety, dedup, partial failure, integer `.lower()` | `test_dedup_survives_integer_names_and_malformed_records`, `test_geoapify_rejects_non_list_features_without_dropping_the_city`, `test_osm_fetch_coerces_numeric_names_and_string_coordinates`, `test_get_category_filters_accepts_non_string_hobbies`, `test_partial_provider_failure_keeps_geoapify_results_and_reports_overpass`, `test_overpass_circuit_breaker_stops_a_dead_mirror_cascade` |
| Discovery breadth, per-city attribution, labels | `test_state_search_attributes_each_poi_to_its_origin_city`, `test_balanced_cap_keeps_every_city_represented`, `test_interest_aliases_understand_plurals_and_hill_stations`, `test_rerank_keeps_all_results_and_labels_discovery`, `test_workflow_discovery_target_scales_with_duration_and_has_no_top_50_cut` |
| SDK, streaming, navigation, endpoint, sessions, strict budget, image-first grid | `test_ask_openai_uses_supported_google_genai_client`, `test_no_source_imports_the_retired_generativeai_package`, `test_chat_agent_streams_chunks_from_its_model`, `test_backend_progress_never_switches_frontend_tabs`, `test_frontend_renders_lazy_images_with_a_placeholder_fallback`, `test_explore_cards_with_a_verified_photo_render_first`, `test_attraction_image_endpoint_returns_verified_url`, `test_sessions_keep_independent_trip_states`, `test_strict_kerala_budget_keeps_the_users_cap_and_declares_shortfall` |

The total changed from **155 passed / 13 files** at the end of §10 to **191 passed / 13 files**
(+36 in the new file). The counts quoted in §2, §8 and §10 are historical snapshots; §11.8 is the
current canonical result.

### 11.9 Files touched in this round

| File | Change |
|---|---|
| `services/attraction_images.py` **(new, 1,566 lines)** | Verified image resolver: provider/OSM identifiers, batch Wikipedia titles, Wikidata `P18`, Commons files/categories, Wikipedia GeoSearch, bounded name search, capped Commons GeoSearch; normalisation, caching, throttling, timeouts, size-derived budget, deadline, stage/source diagnostics. |
| `services/poi_fallback.py` | Defensive coercion, container-type validation, numeric-name rejection, Overpass successful-query cache, 40 s query budget, process-wide circuit breaker, faster 429 failover, Wikipedia pacing (1 req/s), hardened restaurant/accommodation payloads. |
| `agents/information_agent.py` | Image enrichment integration with `last_image_status`; duration-aware balanced caps; per-city attribution (`state_city`/`state`/`city`); discovery diagnostics for state searches; preserved POI fields (including `state`). |
| `services/interest_match.py` | Plural/alias-aware canonical interest groups and OSM evidence mapping. |
| `agents/recommend_agent.py` | Full result set (no `[:50]`), safe category handling, canonical labels. |
| `workflows/travel_graph.py` | Discovery target, recommendation labels, state/discovery status serialisation, per-city spread preserved to route/budget. |
| `main.py` | `GET /api/attraction-image`, `/api/state?session_id=`, SSE contract, itinerary normalisation. |
| `frontend/static/js/main.js` | Lazy images + inline placeholder + safe `onerror`; 12-card "Load more"; workflow progress decoupled from navigation; persisted user tab; removed Unsplash fallbacks. |
| `utils.py` | `google-genai` migration for `ask_openai()`. |
| `requirements.txt` | `langchain-google-genai>=2.1.12,<2.2.0`, `google-genai>=1.65.0,<3.0.0`; removed `google-generativeai`. |
| `tests/test_attraction_images_navigation_and_discovery.py` **(new)** | 34 regression tests (table above). |
| `tests/test_repair_acceptance_a_h.py`, `tests/test_spec_phases_regression.py` | Updated expectations for the new contracts. |
| `conftest.py` | Keeps image lookups offline for the whole suite. |
| `TECHNICAL_DOCUMENTATION.md` | Corrected every stale Pexels/`popular`/`/api/new_trip` claim; documented the verified-image policy, provider cascade, resilience env knobs and new endpoints. |
| `README.md` | Corrected map library and provider list; documented verified imagery and non-intrusive navigation. |

### 11.10 Honest limitations of this round

1. **Image coverage is low and data-bound; see §11.11.** The last measured live run resolved **4 of 60**
   balanced POIs, and the earlier one 7 of 60 with looser rules. Wikimedia rate-limited the search
   stage in both runs, and most sampled places (small churches, temples, madrasas, parking areas) have
   no Wikipedia/Wikidata/Commons record at all. The resolver reports `state=rate_limited` with
   per-stage diagnostics and the UI shows placeholders plus verified photos first. This is the honest
   behaviour, not a pass.
2. **Overpass was unavailable for the entire live run.** All POIs came from Geoapify plus partial
   Wikipedia/RAG. The Overpass code paths are exercised only by mocked tests; success against a live
   mirror was not observed.
3. **The strict-budget shortfall path was not exercised live** — this itinerary genuinely fit at
   ₹94,578. The infeasible path is covered by the mocked acceptance test.
4. **The captured run's `discovery_status` is `{}`.** It was captured before the state-search
   diagnostics were added. The populated payload is now asserted by
   `test_state_search_attributes_each_poi_to_its_origin_city`; a second full live run was not
   repeated solely for that diagnostics field.
5. **No browser automation exists.** The navigation/image changes are verified by source-contract
   tests (no `switchTab` in SSE/handleComplete paths; lazy attributes and `onerror` present), not by
   driving a real browser.
6. **Budget figures are estimates, not quotes.** Accommodation and food use tiered baselines and the
   rental was a labelled fallback estimate because the provider returned HTTP 403.
7. **The driver's shell pipeline exits `1`** even on success: `2>&1 | Tee-Object` promotes the
   Python `UserWarning` on stderr to a PowerShell ErrorRecord. The driver itself completed and wrote
   its JSON (`[LIVE] wrote ...`, `no contract anomalies detected`); `$LASTEXITCODE` was 0.

### 11.11 Follow-up — why many cards still show the placeholder (measured, not assumed)

Two complete live measurements were taken against the real providers (no mocks, empty in-process cache)
on a balanced 60-POI sample: the first 12 distinct Geoapify attractions per city for Kochi, Munnar,
Alleppey, Thiruvananthapuram and Wayanad (`beaches, hill stations and viewpoints`).

| Run | Code state | Elapsed | Verified images | Sources |
|---|---|---|---|---|
| A | before the generic-name guard and first-match-wins | 277.4 s | **7/60** | `wikipedia_title` 1, `commons` 2, `commons_category` 1, `geosearch` 2, `commons_geosearch` 2 |
| B | shipped rules (generic-name/town guard, Commons cap, wall-clock deadline) | 146.4 s | **4/60** | `wikipedia_title` 1, `commons` 2, `commons_geosearch` 1 |

Run B is the representative result for the shipped code. Its stage diagnostics were:

```
osm ok | wikipedia_title ok | wikidata ok | commons_file ok | commons_category ok
geosearch ok | search rate_limited (cooldown, 3 lookups) | commons_geosearch ok
```

#### Why the count is low, and why that is not a bug

1. **The sample is dominated by places nobody has documented.** `Sree Dharma Sastha Temple`,
   `Masjidul Huda`, `Matha parking`, `workship place`, `peetes`, `Church` - most have no Wikipedia
   article, no Wikidata item and no geotagged Commons file, so no *verified* photo exists. Those cards
   correctly keep the placeholder.
2. **Wikimedia rate-limits anonymous clients on the expensive `generator=search` endpoint.** Both live
   runs received HTTP 429 there (after 10-12 lookups in Run A; after 3 in Run B, because the cooldown
   from the earlier measurement traffic was still active). The resolver pauses only that stage,
   retries once after a short cooldown, and does not negative-cache the skipped records, so a later run
   can resolve them.
3. **Exact identifiers and proximity are the reliable paths.** Three of the four Run B hits came from
   an OSM `wikipedia`/`wikimedia_commons` tag (batch lookups that were never rate-limited); the fourth
   (`Thayyil Kalariyil`) came from a geotagged Commons file that shares the distinctive token
   `Kalariyil` and sits within 150 m. The name-search stage, which is the one Wikimedia throttles,
   contributed zero.

#### What the audit changed

Run A proved the resolver could attach a photo to a POI named only `Temple` and to a school via a
generic town photo. Those are exactly the "wrong image" failures the ticket forbids, so the shipped
rules were tightened:

* a name made only of type words (`Temple`, `CSI Church`, `Beach`) may match only an identical title,
  and a town article/file is never attached to a place that merely contains the town name
  (`test_generic_names_cannot_borrow_a_nearby_article_or_photo`);
* a Commons file whose only shared token is the city is rejected
  (`test_commons_file_confidence_rejects_generic_city_and_distant_photos`);
* the first verified match can never be overwritten by a weaker later stage
  (`test_first_verified_image_wins_and_is_never_overwritten`);
* resolved places are removed before the Commons category stage runs
  (`test_commons_categories_skip_places_already_resolved_by_a_file`);
* Commons GeoSearch is capped at eight cluster requests
  (`test_commons_geosearch_has_a_hard_cluster_cap`) and the search + Commons stages stop at a 120 s
  deadline (`test_search_stage_respects_the_wall_clock_deadline`).

The visible effect is honest: in Run B, `Temple`, `Church`, `CSI Church`, `workship place`,
`Matha parking` and `LFGHS Munnar` stay blank, while `Changampuzha Park`, `Pathaparambu`,
`Chengalathukavu` and `Thayyil Kalariyil` show a real, attributable photo. The Explore grid renders
those verified cards first, so the first screen is not a wall of placeholders, and the recommendation
order itself is unchanged.

---

## 11.12 Repair round 3 - interests card removal and Pexels attraction coverage (2026-10-27)

### Root cause of the missing attraction images

The Pexels key was present and valid (a direct `GET /v1/search` returned HTTP 200), but the attraction
pipeline never called Pexels:

* `POIManager._fetch_pexels_image()` was only invoked for accommodation and restaurant records
  (`services/poi_fallback.py:1096`, `:1586`, `:1644`, `:1699`).
* `services/attraction_images.py` - the resolver that `InformationAgent.enrich_attractions()` calls -
  used provider metadata, OSM/Wikipedia/Wikidata/Commons only. It had no Pexels stage at all.
* Wikimedia's `generator=search` endpoint rate-limits anonymous clients after a few lookups, so the
  only source that could plausibly cover famous attractions was exhausted early. Obscure OSM POIs
  often have no Wikimedia presence, which is why most cards kept the placeholder.
* The old helper also swallowed every exception and never reported provider health, so a failed
  Pexels request was invisible in logs. It is still the restaurant/accommodation helper and was left
  unchanged; the attraction path now has its own observable stage.

### Fix

`services/attraction_images.py` now has a final `pexels` stage:

* runs only after every provider/OSM/Wikipedia/Wikidata/Commons stage has been exhausted;
* builds at most two attraction-specific queries (`name + city`, then `name + state/region/country`);
* accepts a candidate only when its `alt` text or photo slug contains the attraction's distinctive
  name tokens (all tokens for 1-2 word names, a majority for longer names);
* common names (`St Mary's Church`, `Victoria Memorial`, `Central Park`, ...) additionally require the
  destination in the photo text - a live search for "St Marys Church Kochi" returned a church in
  Limerick, and that candidate is now rejected;
* never reuses one Pexels photo (including a different rendition of the same photo id) for two
  attractions;
* caches successful mappings and definitive empty results in a namespaced cache, so a Wikimedia miss
  cannot suppress the fallback and a Pexels miss cannot masquerade as a verified answer;
* handles a missing key, HTTP 401/403 (disables the provider), HTTP 429 (cooldown, not cached as a
  negative), timeouts, invalid JSON and invalid image URLs without failing the attraction search;
* has its own call budget (`ATTRACTION_IMAGE_PEXELS_MAX_CALLS`, default 40), deadline
  (`ATTRACTION_IMAGE_PEXELS_DEADLINE`, default 30 s), and opt-out `ATTRACTION_IMAGE_PEXELS=0`.

`resolve()` keeps verified Wikimedia search ahead of Pexels, but still runs Pexels when the Wikimedia
search stage is rate-limited. `GET /api/attraction-image` now reports the actual supplying source
(`pexels` vs `wikipedia`/etc.) instead of always claiming `verified`.

### Interests card

The `What are you interested in?` card, all chips, the weight label/percentage and the slider were
removed from `frontend/templates/index.html`. The DOM-specific chip/slider wiring was removed from
`main.js`; the shared `state.interests` / `state.interestWeight` model and
`getSelectedInterestsString()` remain, with the previous defaults (`Food`, `Photography`, weight 80).
Because the removed chips were also the only trigger for `/api/destinations`, `switchTab('explore')`
now calls `fetchDestinations()` (which already returns early when a plan's attractions exist).

### Live evidence

Pexels stage, real API, 14 attractions across Agra, Kerala, Tamil Nadu, Maharashtra, Telangana,
Punjab, Rajasthan, West Bengal and Karnataka:

```
Taj Mahal, Munnar Tea Garden, Athirappilly Falls, Meenakshi Temple, Gateway of India,
Charminar, Golden Temple, Hawa Mahal, Victoria Memorial, Ranthambore National Park,
Ellora Caves, Marina Beach, Hampi -> resolved
St Marys Church (Kochi) -> correctly rejected; a Limerick church was the only matching stock photo
```

The selected photo `alt` texts were checked individually (e.g. "Beautiful view of the Taj Mahal ...
in Agra", "Vibrant tea plantations ... Munnar", "Stunning view of the Golden Temple in Amritsar").
A full `enrich()` run on 8 famous places across 6 states resolved 7/8 from Wikimedia GeoSearch and
left only `St Marys Church` unresolved, so the fallback order remains Wikimedia first.

`GET /api/attraction-image?name=Hawa Mahal&city=Jaipur&country=India` with the Wikimedia stages
stubbed returned HTTP 200, a Pexels URL, `source: "pexels"` and `diagnostics.detail: "pexels"`.

### Tests

`pytest tests -q` -> **207 passed, 1 warning in 60.88s**; `compileall` exit 0. New regression tests
cover: relevant Pexels acceptance, wrong-country rejection, generic-photo rejection, missing key,
401 disabling, 429 not negative-cached, timeout not negative-cached, invalid JSON, invalid URL,
empty result + negative cache, success cache, no photo reuse, `enrich()` integration, Wikipedia-first
ordering, Pexels when Wikimedia search is rate-limited, and the interests-card removal/state
preservation contract.


