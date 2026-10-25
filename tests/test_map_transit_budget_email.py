"""
Regression tests for Requirements 3, 4, 5 and 6.
=================================================

These complement tests/test_attraction_discovery_and_details.py (R1/R2).

What is covered, and how:

  * R4 TransitAgent  — unit test with a *mocked* Gemini model. We assert the
    prompt that gets sent demands comprehensive, honestly-labelled estimates,
    that fenced/raw JSON is parsed, and that a malformed response degrades
    gracefully instead of raising.
  * R6 /api/email     — Flask ``test_client`` integration tests against the
    real hardened endpoint. The SMTP provider is mocked; every external call
    is stubbed so the suite is deterministic. Mocked success is NOT treated as
    proof of live delivery (that remains a manual step).
  * R3 Map + R5 Budget + R4 frontend — the browser JS cannot be executed under
    pytest, so these are *source-ordering* regression guards that fail loudly if
    someone reintroduces the original bugs (day filters rendered after the
    map-init guard, missing budget closing section, no estimate banner).

A mocked dependency is always clearly labelled as such in the test name or
docstring; no test pretends a stubbed network call is a verified live result.
"""
import json
import os
from unittest.mock import MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_JS = os.path.join(ROOT, "frontend", "static", "js", "main.js")
INDEX_HTML = os.path.join(ROOT, "frontend", "templates", "index.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# R4 — TransitAgent (mocked LLM)
# ---------------------------------------------------------------------------

_VALID_TRANSIT = {
    "origin": "Kolkata",
    "destination": "Darjeeling",
    "is_estimate": True,
    "estimation_note": "AI estimates, not live bookings.",
    "currency": "INR",
    "intercity_transport": [
        {"mode": "train", "provider": "Indian Railways", "duration": "6h",
         "estimated_cost": 350, "cost_basis": "per_person", "is_estimate": True},
        {"mode": "self_drive", "provider": "Self-drive car", "duration": "8h",
         "estimated_cost": 3500, "cost_basis": "per_vehicle", "is_estimate": True},
    ],
    "local_transport": [
        {"mode": "taxi", "estimated_cost": 1500, "cost_basis": "per_day", "is_estimate": True},
    ],
    "recommended_mode": "train",
    "recommended_local_mode": "taxi",
}


def _bare_transit_agent():
    """Build a TransitAgent without touching the real Gemini client."""
    from agents.transit_agent import TransitAgent
    from unittest.mock import MagicMock
    agent = TransitAgent.__new__(TransitAgent)
    agent.model = MagicMock()
    return agent


def test_transit_agent_prompt_requests_comprehensive_estimate_modes():
    agent = _bare_transit_agent()
    resp = MagicMock()
    resp.content = json.dumps(_VALID_TRANSIT)
    agent.model.invoke.return_value = resp

    result = agent.get_transit_options(
        origin="Kolkata", destination="Darjeeling", start_date="2026-01-01",
        budget_level="medium", is_international=False, num_people=4,
    )

    # The single HumanMessage carries the prompt.
    messages = agent.model.invoke.call_args[0][0]
    prompt = messages[-1].content

    assert "at least 4 entries" in prompt
    for mode in ("flight", "train", "bus", "self_drive", "taxi_or_cab_transfer"):
        assert mode in prompt, f"prompt must enumerate {mode} as a candidate mode"
    # Estimate labelling + fabrication guardrails are explicitly instructed.
    assert "is_estimate" in prompt
    assert "cost_basis" in prompt
    assert "NEVER a specific flight/train number" in prompt
    assert "local_transport" in prompt
    # Group size is threaded into the prompt so fares are considered per group.
    assert "4 traveller" in prompt

    # Parsed output is returned intact.
    assert result["is_estimate"] is True
    assert len(result["intercity_transport"]) == 2
    assert result["recommended_mode"] == "train"


def test_transit_agent_parses_markdown_fenced_json():
    agent = _bare_transit_agent()
    resp = MagicMock()
    resp.content = "```json\n" + json.dumps(_VALID_TRANSIT) + "\n```"
    agent.model.invoke.return_value = resp

    out = agent.get_transit_options("Kolkata", "Darjeeling", "2026-01-01")
    assert isinstance(out, dict) and out.get("is_estimate") is True


def test_transit_agent_degrades_gracefully_on_garbage_response():
    """A non-JSON model reply must never raise to the caller."""
    agent = _bare_transit_agent()
    resp = MagicMock()
    resp.content = "Sorry, I cannot help with that."
    agent.model.invoke.return_value = resp

    out = agent.get_transit_options("Kolkata", "Darjeeling", "2026-01-01")
    assert isinstance(out, dict)
    # The old ``{"flights": [], ...}`` reply was unreadable by the Transport tab,
    # so a model failure looked exactly like "this route simply has no options".
    # The canonical payload keeps the journey, labels the failure and never
    # invents a fare.
    assert out["intercity_transport"] == []
    assert out["local_transport"] == []
    assert out["origin"] == "Kolkata"
    assert out["destination"] == "Darjeeling"
    assert out["provider_status"] == "error"
    assert out["is_estimate"] is True


# ---------------------------------------------------------------------------
# R3 — Map day filters (source-ordering regression guard)
# ---------------------------------------------------------------------------

def test_index_html_exposes_map_day_filter_and_budget_closing_containers():
    html = _read(INDEX_HTML)
    assert 'id="map-day-filters"' in html
    assert 'id="budget-closing"' in html


def test_day_filters_are_generated_before_the_map_init_guard():
    """
    Original bug: day-filter buttons were built AFTER
    ``if (!state.map || daysArray.length === 0) return;`` so on first paint
    (map not yet mounted) the "All Days / Day N" header never appeared.
    """
    js = _read(MAIN_JS)
    gen_idx = js.index("getElementById('map-day-filters')")
    guard_idx = js.index("if (!state.map || daysArray.length === 0) return;")
    assert gen_idx < guard_idx, "day filters must be rendered before the early return"
    # Both buttons and the type-safe comparison must exist.
    assert "map-filter-all" in js
    assert "map-filter-day-${day.day}" in js
    assert 'String(state.mapCurrentDay) === String(value)' in js


def test_map_day_filter_defaults_to_all_days_and_is_independent_of_itinerary():
    """Requirement 3: the map starts on "All Days" and its selection must not
    be shared with the itinerary's active day, and no page reload is needed."""
    js = _read(MAIN_JS)
    assert "mapCurrentDay: sessionStorage.getItem('mapCurrentDay') || 'all'" in js
    # The map filter state is its own variable - the itinerary tab keeps
    # activeItineraryDay, so switching days in one does not hijack the other.
    assert "state.mapCurrentDay" in js
    assert "renderMap(state.itinerary)" in js
    # Stale in-flight OSRM results must never paint a day the user left.
    assert "state._mapRenderToken" in js
    # An empty day is reported, not left silently blank.
    assert "has no mapped stops" in js


# ---------------------------------------------------------------------------
# R5 — Budget closing section (source regression guard)
# ---------------------------------------------------------------------------

def test_budget_closing_renderer_exists_and_is_invoked():
    js = _read(MAIN_JS)
    assert "function renderBudgetClosing()" in js
    # Defined once, then called at least once from renderBudget.
    assert js.count("renderBudgetClosing()") >= 2
    # It must target the dedicated container and not fabricate weather.
    assert "budget-closing" in js


# ---------------------------------------------------------------------------
# R4 frontend — estimate banner + local transport + group-cost basis
# ---------------------------------------------------------------------------

def test_transit_frontend_renders_estimate_banner_local_options_and_group_math():
    js = _read(MAIN_JS)
    assert "transit-estimate-banner" in js
    assert "transit-local-option" in js
    assert "Estimated" in js
    # Group / per-person basis must be modelled, not assumed round-trip.
    assert "cost_basis" in js


# ---------------------------------------------------------------------------
# R6 — /api/email hardened endpoint (mocked SMTP provider)
# ---------------------------------------------------------------------------

class FakeWorkflow:
    def __init__(self, state):
        self.state = state

    def get_current_state(self):
        return self.state

    def set_state_field(self, key, value):
        self.state[key] = value


@pytest.fixture()
def email_client(monkeypatch):
    import main
    # Reset the module-level in-flight guard for a clean slate each test.
    main._EMAIL_INFLIGHT.clear()
    app = main.app
    app.config.update({"TESTING": True})
    yield app, main
    main._EMAIL_INFLIGHT.clear()


def _make_client(app, main, state, session_id="sess-1", user_email=None):
    main.workflows[session_id] = FakeWorkflow(state)
    client = app.test_client()
    with client.session_transaction() as s:
        s["session_id"] = session_id
        if user_email:
            s["user_email"] = user_email
    return client, state


_ITIN = [{"day": 1, "date": "2026-01-01", "activities": [{"name": "Tiger Hill"}]}]


def test_email_rejects_invalid_recipient(email_client):
    app, main = email_client
    client, _ = _make_client(app, main, {"itinerary": _ITIN})
    resp = client.post("/api/email", json={"email": "not-an-email"})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_email_requires_active_session(email_client):
    app, main = email_client
    client = app.test_client()  # no session_id set
    resp = client.post("/api/email", json={"email": "traveler@example.com"})
    assert resp.status_code == 400
    assert "session" in resp.get_json()["error"].lower()


def test_email_missing_credentials_returns_503(email_client, monkeypatch):
    app, main = email_client
    monkeypatch.delenv("MAIL_SERVER", raising=False)
    monkeypatch.delenv("MAIL_USERNAME", raising=False)
    client, _ = _make_client(app, main, {"itinerary": _ITIN})
    resp = client.post("/api/email", json={"email": "traveler@example.com"})
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["success"] is False
    assert "not configured" in body["error"].lower()


def test_email_success_with_mocked_provider(email_client, monkeypatch):
    app, main = email_client
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "user@example.com")
    sent = {}

    def fake_send(email, user_name, city, itinerary, budget, confirmation, notes=None, trip_context=None):
        sent["email"] = email
        sent["itinerary"] = itinerary
        return True

    monkeypatch.setattr(main, "send_trip_email", fake_send)
    client, state = _make_client(app, main, {"itinerary": _ITIN, "user_info": {"name": "Asha", "city": "Darjeeling"}})
    resp = client.post("/api/email", json={"email": "asha@example.com"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True and body["status"] == "success"
    # Delivery recorded on session state so a retry cannot re-send.
    assert state["email_sent"] is True
    assert state["email_sent_to"] == "asha@example.com"
    assert sent["email"] == "asha@example.com"


def test_email_prefers_server_side_itinerary_over_body(email_client, monkeypatch):
    app, main = email_client
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "user@example.com")
    captured = {}

    def fake_send(email, user_name, city, itinerary, budget, confirmation, notes=None, trip_context=None):
        captured["itinerary"] = itinerary
        return True

    monkeypatch.setattr(main, "send_trip_email", fake_send)
    # Server has a real itinerary; the (malicious) body tries to smuggle another.
    client, _ = _make_client(app, main, {"itinerary": _ITIN})
    resp = client.post("/api/email", json={
        "email": "asha@example.com",
        "itinerary": [{"day": 99, "activities": [{"name": "SOMEONE ELSES PLAN"}]}],
    })
    assert resp.status_code == 200
    assert captured["itinerary"] == _ITIN  # server-side value wins


def test_email_already_sent_is_idempotent_and_does_not_ressend(email_client, monkeypatch):
    app, main = email_client
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "user@example.com")
    calls = {"n": 0}

    def fake_send(*a, **k):
        calls["n"] += 1
        return True

    monkeypatch.setattr(main, "send_trip_email", fake_send)
    state = {"itinerary": _ITIN, "email_sent": True, "email_sent_to": "asha@example.com"}
    client, _ = _make_client(app, main, state)
    resp = client.post("/api/email", json={"email": "asha@example.com"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True and body["status"] == "already_sent"
    assert calls["n"] == 0  # no second SMTP attempt


def test_email_in_flight_double_click_is_rejected_with_429(email_client, monkeypatch):
    import time
    app, main = email_client
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "user@example.com")
    monkeypatch.setattr(main, "send_trip_email", lambda *a, **k: True)
    client, _ = _make_client(app, main, {"itinerary": _ITIN})
    main._EMAIL_INFLIGHT["sess-1"] = time.time()  # simulate a send already running
    resp = client.post("/api/email", json={"email": "asha@example.com"})
    assert resp.status_code == 429
    assert resp.get_json()["success"] is False


def test_email_cross_user_delivery_is_forbidden(email_client, monkeypatch):
    app, main = email_client
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "user@example.com")
    calls = {"n": 0}

    def fake_send(*a, **k):
        calls["n"] += 1
        return True

    monkeypatch.setattr(main, "send_trip_email", fake_send)
    # Signed-in as owner, but request tries to send to a different address.
    client, _ = _make_client(app, main, {"itinerary": _ITIN},
                             user_email="owner@example.com")
    resp = client.post("/api/email", json={"email": "victim@example.com"})
    assert resp.status_code == 403
    assert resp.get_json()["success"] is False
    assert calls["n"] == 0  # never reached the provider


def test_email_provider_failure_returns_500_not_success(email_client, monkeypatch):
    app, main = email_client
    monkeypatch.setenv("MAIL_SERVER", "smtp.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "user@example.com")
    monkeypatch.setattr(main, "send_trip_email", lambda *a, **k: False)
    client, state = _make_client(app, main, {"itinerary": _ITIN})
    resp = client.post("/api/email", json={"email": "asha@example.com"})
    assert resp.status_code == 500
    assert resp.get_json()["success"] is False
    # A failed send must not be recorded as delivered.
    assert not state.get("email_sent")


# ---------------------------------------------------------------------------
# R-login-loop — the landing page must not trap authenticated users
# ---------------------------------------------------------------------------
# Reported bug: after signing in, the browser looped  /login -> / -> /login .
# Root cause: index.html renders the landing view as active by default and the
# landing's "Get Started" button hard-navigates to /login; when the JS boot
# fails to hide the landing, an authenticated user is bounced back by /login's
# own redirect. The fix server-renders the correct initial view per auth state
# and makes the entry buttons auth-aware.

def _render_landing(client):
    return client.get("/").get_data(as_text=True)


def test_anonymous_landing_is_the_active_view():
    import main
    main.app.config["TESTING"] = True
    client = main.app.test_client()
    html = _render_landing(client)
    assert 'id="view-landing" class="view-section active"' in html
    assert "authenticated: false" in html


def test_authenticated_user_sees_app_not_landing():
    """The core regression: a signed-in session must NOT render the landing."""
    import main
    main.app.config["TESTING"] = True
    client = main.app.test_client()
    login = client.post("/login", json={"provider": "guest"})
    assert login.status_code == 200 and login.get_json()["status"] == "success"

    html = _render_landing(client)
    # Landing must not be the active section; the chat app must be.
    assert 'id="view-landing" class="view-section active"' not in html
    assert 'id="view-chat" class="view-section pt-4 active"' in html
    assert "authenticated: true" in html
    # And /login correctly redirects an authenticated session away.
    assert client.get("/login").status_code == 302


def test_landing_entry_buttons_are_auth_aware():
    """Entry buttons must call explorexEnter(), never hard-navigate to /login."""
    html = _read(INDEX_HTML)
    assert "window.explorexEnter = function" in html
    assert 'onclick="explorexEnter()"' in html
    # The old unconditional bounce to /login must be gone from the buttons.
    assert "onclick=\"window.location.href='/login'\"" not in html
