"""
Phase 10 regression tests for the ExploreX stabilization pass.

Each test group maps to one requirement block of the repair specification and
runs with EVERY external service mocked (no network, no SMTP, no LLM), so the
suite is deterministic and offline:

A  Overpass failure is non-fatal - partial Geoapify results survive and the
   provider status is explicit.
B  No provider field is ever fabricated (ratings, prices, amenities, fees).
C  The itinerary contract (integer day / sequence / attraction_id / coords)
   reaches the client for EVERY day without mutating server state.
D  Transit: a same-city trip never asks for an intercity leg, and a dead
   rental provider never erases the authoritative self-drive decision.
E  Budget: the displayed categories are the checksum, and "fits the budget" is
   never claimed when the plan is over or the data is incomplete.
F  Email: pre-send validation reports the missing fields and success is only
   reported after SMTP accepted the message.
G  The travel assistant renders from persisted session state only - it does not
   re-run any planning agent and cannot see another session's trip.
H  Errors keep one shape and never leak exception text, keys or traces.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_JS = os.path.join(ROOT, "frontend", "static", "js", "main.js")


def _read_main_js():
    with open(MAIN_JS, encoding="utf-8") as fh:
        return fh.read()


def _decoded_email_body(raw):
    """MIME parts are base64/quoted-printable encoded, so decode before asserting."""
    from email import message_from_string

    msg = message_from_string(raw)
    chunks = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if payload:
            chunks.append(payload.decode("utf-8", "replace"))
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.status = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def _bare_poi_manager():
    from services.poi_fallback import POIManager

    pm = POIManager.__new__(POIManager)
    pm.geoapify_key = "test-geoapify-key"
    pm.pexels_key = ""
    pm._cache = {}
    pm.overpass_endpoints = ["https://overpass.example/api/interpreter"]
    pm._ensure_provider_state()
    return pm


def _bare_workflow(**overrides):
    """A TravelGraph with stubbed agents and a controllable trip state."""
    from workflows.travel_graph import TravelGraph

    wf = TravelGraph.__new__(TravelGraph)
    wf.session_states = {}
    wf.state = {
        "user_info": {
            "origin_city": "Darjeeling", "city": "Darjeeling", "days": 3,
            "people": 2, "kids": 0, "budget": "medium", "budget_amount": 60000,
            "budget_strictness": "flexible", "start_date": "2026-11-01",
            "is_international": False,
        },
        "attractions": [{"id": "a1", "name": "Tiger Hill",
                         "location": {"lat": 27.0, "lng": 88.27}}],
        "selected_attractions": [{"id": "a1", "name": "Tiger Hill"}],
        "planned_attractions": [],
        "unscheduled_attractions": [],
        "daily_plan": {"day1": ["Tiger Hill"]},
        "itinerary": [],
        "budget": {},
        "budget_normalized": {},
        "should_rent_car": False,
        "rental_decision_source": "default",
        "car_rental_available": None,
        "car_rental_source": None,
        "transit_options": None,
        "selected_accommodation": None,
        "accommodations": [],
        "restaurants": [],
        "travel_tips": None,
        "provider_status": None,
        "weather_summary": None,
        "email_sent": True,          # never touch the auto-send path
        "current_step": "communication",
        "ai_recommendation_generated": True,
    }
    wf.state.update(overrides)

    wf.chat_agent = MagicMock()
    wf.chat_agent.required_fields = ["city", "days", "people", "budget"]
    wf.chat_agent.is_valid_field = lambda f, v: bool(v)
    wf.chat_agent.conversation_history = []
    wf.info_agent = MagicMock()
    wf.info_agent.search_car_rentals.return_value = []
    wf.info_agent.get_fuel_price.return_value = None
    wf.transit_agent = MagicMock()
    wf.transit_agent.get_transit_options.return_value = {
        "intercity_transport": [{"mode": "taxi", "estimated_cost": 4000,
                                 "cost_basis": "per_vehicle"}],
        "local_transport": [{"mode": "shared taxi", "estimated_cost": 300,
                             "cost_basis": "per_person"}],
        "recommended_mode": "taxi",
        "recommended_local_mode": "shared taxi",
    }
    wf.budget_agent = MagicMock()
    wf.budget_agent.estimate_budget.return_value = {
        "estimated_total": 21000.0, "accommodation": 9000.0, "food": 6000.0,
        "transport": 4000.0, "local_transport": 1200.0, "activities": 600.0,
        "miscellaneous": 200.0, "car_rental": 0.0, "fuel": 0.0,
        "budget_limit": 60000, "budget_type": "flexible",
        "within_budget": True, "remaining_budget": 39000.0,
    }
    wf.comm_agent = MagicMock()
    wf.comm_agent.generate_travel_tips_and_summary.return_value = "tips"
    wf.strategy_agent = MagicMock()
    wf.route_agent = MagicMock()
    wf.retrieval_agent = MagicMock()
    wf.recommend_agent = MagicMock()
    return wf


# ---------------------------------------------------------------------------
# A — Overpass is non-fatal and its health is explicit
# ---------------------------------------------------------------------------

def test_overpass_total_failure_still_returns_geoapify_results_with_status():
    pm = _bare_poi_manager()
    geo = {"features": [{"properties": {"name": "Tiger Hill", "lat": 27.0,
                                        "lon": 88.27, "place_id": "g1",
                                        "categories": ["sight.viewpoint"]}}]}

    with patch.object(pm, "_execute_overpass_query", return_value=[]), \
         patch("services.poi_fallback.requests.get",
               side_effect=lambda url, **kw: _Resp(geo)):
        pois = pm.get_attractions(27.0, 88.27, radius=20000, min_results=1)

    assert [p["name"] for p in pois] == ["Tiger Hill"], (
        "a dead Overpass cascade must not discard the results Geoapify DID return"
    )
    status = pm.get_provider_status()
    assert "overpass" in status and "geoapify" in status
    assert status["overpass"]["state"] == "empty"
    assert status["geoapify"]["state"] == "ok"
    assert status["overpass"]["checked_at"] > 0


def test_overpass_exception_is_caught_and_recorded_not_raised():
    pm = _bare_poi_manager()
    geo = {"features": [{"properties": {"name": "Batasia Loop", "lat": 27.01,
                                        "lon": 88.26, "place_id": "g2"}}]}

    def _boom(*a, **kw):
        raise RuntimeError("Overpass mirror refused the request (HTTP 504)")

    with patch.object(pm, "_fetch_osm_attractions", side_effect=_boom), \
         patch("services.poi_fallback.requests.get",
               side_effect=lambda url, **kw: _Resp(geo)):
        pois = pm.get_attractions(27.0, 88.26, radius=15000, min_results=1)

    assert len(pois) == 1
    assert pm.get_provider_status()["overpass"]["state"] == "error"


def test_all_providers_down_returns_empty_list_with_reason_not_fabrications():
    pm = _bare_poi_manager()

    with patch.object(pm, "_fetch_osm_attractions", return_value=[]), \
         patch("services.poi_fallback.requests.get",
               side_effect=lambda url, **kw: _Resp({"features": []})):
        pois = pm.get_attractions(27.0, 88.27, radius=20000, min_results=30,
                                  retrieved_rag_pois=None)

    assert pois == [], "with no live data the manager must return nothing, not guesses"
    health = pm.get_provider_status()
    assert health["overpass"]["state"] == "empty"
    assert health["geoapify"]["state"] == "empty"


# ---------------------------------------------------------------------------
# B — Nothing is fabricated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, None), (True, None), (False, None), ("", None), ("n/a", None),
    (0, None), (0.0, None), (-1, None), (6.2, None), ("4.7", 4.7), (5, 5.0),
])
def test_parse_rating_only_accepts_a_reported_value(value, expected):
    from services.poi_fallback import _parse_rating

    assert _parse_rating(value) == expected


def test_osm_accommodation_without_tags_exposes_unknown_instead_of_guesses():
    pm = _bare_poi_manager()
    pm._fetch_pexels_image = lambda *a, **kw: None
    elements = [
        {"type": "node", "id": 1, "lat": 12.0, "lon": 77.0,
         "tags": {"name": "Corner Guest House"}},
        {"type": "node", "id": 2, "lat": 12.1, "lon": 77.1,
         "tags": {"name": "Tagged Inn", "rating": "4.6", "internet_access": "wlan"}},
    ]

    with patch.object(pm, "_execute_overpass_query", return_value=elements):
        accs = pm._fetch_osm_accommodations(12.0, 77.0, 15000, "budget", 4)

    plain, tagged = accs[0], accs[1]
    assert "rating" not in plain, "an untagged stay must not be given a synthetic score"
    assert plain["rating_status"] == "unavailable"
    assert plain["price_per_night"] is None
    assert plain["currency"] is None, "inventing a currency is how ₹ turned into $"
    assert plain["price_status"] == "unavailable"
    assert plain["amenities"] == [] and plain["amenities_status"] == "unavailable"
    assert plain["address"] == "Address not provided"

    assert tagged["rating"] == pytest.approx(4.6)
    tagged["rating_status"] == "reported_by_source"
    assert tagged["amenities"] == ["Wi-Fi"]


def test_geoapify_attractions_emit_no_rating_or_price_when_absent():
    pm = _bare_poi_manager()
    payload = {"features": [{"properties": {
        "name": "View Point", "lat": 27.0, "lon": 88.2, "place_id": "g9",
        "categories": ["tourism.attraction.viewpoint"], "formatted": "Somewhere",
    }}]}

    with patch("services.poi_fallback.requests.get",
               side_effect=lambda url, **kw: _Resp(payload)):
        pois = pm._fetch_geoapify_attractions(27.0, 88.2, 20000, None)

    assert len(pois) == 1
    for fabricated in ("rating", "user_ratings_total", "price_level", "entry_fee"):
        assert fabricated not in pois[0], f"{fabricated} was invented by the fallback layer"


def test_frontend_does_not_render_default_ratings_or_mock_exchange_rates():
    js = _read_main_js()

    assert "fieldRating(a) {" in js or "function fieldRating" in js
    assert "return (isNaN(n) || n <= 0) ? null : n;" in js, (
        "an unknown rating must be null so the UI can say 'Not rated'"
    )
    assert "Fee not reported" in js
    assert "Amenities not reported" in js or "amenities_status" in js
    assert "a.stars ?? 4.5" not in js
    assert "currency === 'USD'" not in js, "no mock conversion rates may remain"


# ---------------------------------------------------------------------------
# C — The day/sequence/attraction_id contract
# ---------------------------------------------------------------------------

def test_normalize_itinerary_coerces_day_labels_and_stamps_the_contract():
    import main

    raw = [
        {"day": "day1", "date": "2026-11-01", "activities": [
            {"name": "Tiger Hill", "id": "g1", "location": {"lat": 27.0, "lng": 88.27}},
            {"name": "Batasia Loop", "id": 42, "latitude": 27.01, "longitude": 88.26},
        ]},
        {"day": "Day 2", "date": "2026-11-02", "spots": [
            {"name": "Peace Pagoda", "id": "g3"},
        ]},
        {"day": None, "activities": ["not-a-dict"]},
    ]

    out = main.normalize_itinerary_for_client(raw)["days"]

    assert [d["day"] for d in out] == [1, 2, 3], "every day label must become an int"
    assert len(out) == 3, "ALL days must be delivered, not just the active one"
    first = out[0]["activities"]
    assert [a["sequence"] for a in first] == [1, 2]
    assert first[0]["attraction_id"] == "g1" and first[1]["attraction_id"] == "42"
    assert first[0]["latitude"] == pytest.approx(27.0)
    assert all(isinstance(a, dict) for a in out[2]["activities"])
    assert out[1]["activities"][0]["day"] == 2


def test_normalize_itinerary_does_not_mutate_the_stored_server_state():
    import main

    stored = [{"day": 1, "activities": [{"name": "Tiger Hill", "id": "g1"}]}]
    main.normalize_itinerary_for_client(stored)

    assert "sequence" not in stored[0]["activities"][0], (
        "projection must copy, never write client fields into the authoritative state"
    )


# ---------------------------------------------------------------------------
# D — Transit guards
# ---------------------------------------------------------------------------

def test_same_city_trip_does_not_request_an_intercity_leg():
    wf = _bare_workflow()

    wf._process_communication(user_input="continue")

    assert wf.transit_agent.get_transit_options.call_count == 0, (
        "origin == destination must skip the intercity lookup entirely"
    )
    transit = wf.state["transit_options"]
    assert transit["same_city"] is True
    assert transit["intercity_transport"] == []
    assert transit["recommended_mode"] is None


def test_different_city_still_queries_intercity_options():
    wf = _bare_workflow()
    wf.state["user_info"]["origin_city"] = "Kolkata"

    wf._process_communication(user_input="continue")

    assert wf.transit_agent.get_transit_options.call_count == 1
    assert wf.state["transit_options"]["intercity_transport"]


def test_dead_rental_provider_never_erases_the_self_drive_decision():
    wf = _bare_workflow()
    wf.state["should_rent_car"] = True
    wf.state["rental_decision_source"] = "user_selection"
    wf.state["user_info"]["rental_choice"] = True
    wf.info_agent.search_car_rentals.return_value = []   # 403 / no live quotes

    wf._process_communication(user_input="continue")

    assert wf.state["should_rent_car"] is True, (
        "provider availability is data, not a decision - it must not flip the flag"
    )
    assert wf.state["car_rental_available"] is False
    assert wf.state["car_rental_source"] == "fallback_estimate"
    assert wf.state["rental_decision_source"] == "user_selection"


def test_rental_choice_of_false_is_not_overridden_by_the_agent():
    wf = _bare_workflow()
    wf.state["user_info"]["rental_choice"] = False
    # The agent's recommendation writes into user_info; it must lose to the user.
    wf.state["user_info"]["should_rent_car"] = True

    wf._process_communication(user_input="continue")

    assert wf.state["should_rent_car"] is False
    assert wf.info_agent.search_car_rentals.call_count == 0


def test_frontend_no_longer_recomputes_the_budget_on_transport_choice():
    js = _read_main_js()

    assert "function renderLocalTransit" in js
    assert "transit-empty" in js, "a same-city trip must explain the empty intercity list"
    assert "Regenerate your itinerary to apply the new cost" in js
    assert "state.budget.intercity_transport -=" not in js, (
        "the browser must not keep a second, private copy of the budget maths"
    )


# ---------------------------------------------------------------------------
# E — Budget invariants
# ---------------------------------------------------------------------------

def test_normalized_categories_sum_to_the_total():
    from services.budget_norm import normalize_budget

    nb = normalize_budget({
        "estimated_total": 21000, "accommodation": 9000, "food": 6000,
        "transport": 4000, "local_transport": 1200, "activities": 600,
        "miscellaneous": 200, "budget_limit": 60000,
    })

    displayed = sum(nb[k] for k in ("accommodation", "food", "transport",
                                    "local_transport", "attractions", "miscellaneous"))
    assert displayed == pytest.approx(nb["total"])
    assert nb["checksum_consistent"] is True
    assert nb["data_complete"] is True
    assert nb["currency"] == "INR"
    assert nb["status"] == "within" and nb["within_budget"] is True


def test_over_budget_is_never_reported_as_fitting_the_budget():
    from services.budget_norm import normalize_budget

    nb = normalize_budget({
        "total": 75000, "accommodation": 40000, "food": 20000, "transport": 10000,
        "local_transport": 3000, "activities": 2000, "miscellaneous": 0,
        "budget_limit": 60000,
        "within_budget": True,   # the raw agent said "fits" - the total says otherwise
    })

    assert nb["status"] == "over"
    assert nb["within_budget"] is False, (
        "an over-limit plan must never be labelled as fitting the budget"
    )
    assert nb["remaining_budget"] == pytest.approx(-15000)


def test_unknown_total_is_incomplete_rather_than_zero():
    from services.budget_norm import normalize_budget

    # Nothing at all reported -> no total, explicitly incomplete.
    empty = normalize_budget({})
    assert empty["total_is_valid"] is False
    assert empty["status"] == "incomplete"

    # One category known, five never reported -> the partial sum must be
    # labelled incomplete, never presented as the whole cost.
    partial = normalize_budget({"accommodation": 5000})
    assert partial["total_source"] == "derived_from_categories"
    assert partial["data_complete"] is False
    assert partial["status"] == "incomplete"
    assert "food" in partial["missing_categories"]
    assert partial["within_budget"] is not True, "incomplete data cannot prove affordability"

    # A checksum that disagrees with the announced total is incomplete too.
    mismatched = normalize_budget({"total": 99999, "accommodation": 5000, "food": 3000,
                                   "transport": 1000, "local_transport": 500,
                                   "activities": 200, "miscellaneous": 100})
    assert mismatched["checksum_consistent"] is False
    assert mismatched["data_complete"] is False


def test_a_genuine_zero_cost_is_not_confused_with_missing_data():
    from services.budget_norm import normalize_budget

    nb = normalize_budget({
        "total": 10000, "accommodation": 10000, "food": 0, "transport": 0,
        "local_transport": 0, "activities": 0, "miscellaneous": 0,
    })

    assert nb["food"] == 0 and "food" not in nb["missing_categories"]
    assert nb["data_complete"] is True


# ---------------------------------------------------------------------------
# F — Email payload and delivery truthfulness
# ---------------------------------------------------------------------------

_FULL_CTX = {
    "origin": "Kolkata", "duration": 3, "people": 2, "kids": 0,
    "start_date": "2026-11-01", "end_date": "2026-11-04",
    "tips": "Carry layers.", "accommodation_is_selection": True,
    "unscheduled": [{"name": "Rock Garden", "reason": "no_time"}],
    "rental_decision_source": "user_selection",
    "accommodation": [{"name": "Hotel Summit", "price_per_night": 3500,
                       "currency": "INR"}],
    "transport": {"intercity_transport": [
        {"mode": "train", "estimated_cost": 1200, "cost_basis": "per_person"}]},
    "food": [{"name": "Mom's Kitchen"}],
}


def _smtp_env(monkeypatch):
    for k, v in {"MAIL_SERVER": "smtp.test", "MAIL_PORT": "587",
                 "MAIL_USERNAME": "bot@test", "MAIL_PASSWORD": "secret",
                 "MAIL_USE_TLS": "True"}.items():
        monkeypatch.setenv(k, v)


def test_email_reports_success_only_after_smtp_accepted(monkeypatch):
    from services import email_service

    _smtp_env(monkeypatch)
    sent = {}

    class _Server:
        def __init__(self, *a, **kw): pass
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): sent["login"] = (u, p)
        def sendmail(self, frm, to, body): sent["send"] = (frm, to, len(body))
        def quit(self): sent["quit"] = True

    with patch.object(email_service.smtplib, "SMTP", _Server):
        ok = email_service.send_trip_email(
            "traveller@example.com", "Priya", "Darjeeling",
            [{"day": 1, "date": "2026-11-01", "activities": [{"name": "Tiger Hill"}]}],
            {"estimated_total": 21000, "accommodation": 9000, "food": 6000,
             "transport": 4000, "local_transport": 1200, "activities": 600,
             "miscellaneous": 200, "budget_limit": 60000},
            "Your plan is ready.", notes="", trip_context=_FULL_CTX)

    assert ok is True
    assert "send" in sent and sent["quit"] is True, (
        "success requires an accepted message and a closed connection"
    )


def test_smtp_failure_returns_false_and_leaks_no_credentials(monkeypatch, capsys):
    from services import email_service

    _smtp_env(monkeypatch)

    def _raise(*a, **kw):
        raise email_service.smtplib.SMTPSenderRefused(550, "rejected", "bot@test")

    with patch.object(email_service.smtplib, "SMTP", side_effect=_raise):
        ok = email_service.send_trip_email(
            "traveller@example.com", "Priya", "Darjeeling",
            [{"day": 1, "activities": [{"name": "Tiger Hill"}]}],
            {"estimated_total": 21000}, "Your plan is ready.", trip_context=_FULL_CTX)

    assert ok is False
    logged = capsys.readouterr().out
    assert "secret" not in logged, "the SMTP password must never reach the logs"


def test_pre_send_validation_names_the_missing_fields_and_refuses_an_empty_trip(
        monkeypatch, capsys):
    from services import email_service

    _smtp_env(monkeypatch)
    with patch.object(email_service.smtplib, "SMTP") as smtp:
        ok = email_service.send_trip_email(
            "traveller@example.com", "Priya", "", [], {}, "", trip_context={})

    assert ok is False, "an email with no itinerary and no budget must not be sent"
    logged = capsys.readouterr().out
    assert "[EMAIL][VALIDATION]" in logged
    assert "Missing/incomplete payload fields" in logged
    for field in ("city", "itinerary", "budget.total", "accommodation"):
        assert field in logged, f"{field} should be named in the pre-send validation log"


def test_email_body_carries_accommodation_transit_and_unscheduled_stops(
        monkeypatch):
    from services import email_service

    _smtp_env(monkeypatch)
    captured = {}

    class _Server:
        def __init__(self, *a, **kw): pass
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, frm, to, body): captured["body"] = body
        def quit(self): pass

    with patch.object(email_service.smtplib, "SMTP", _Server):
        email_service.send_trip_email(
            "traveller@example.com", "Priya", "Darjeeling",
            [{"day": 1, "date": "2026-11-01",
              "activities": [{"name": "Tiger Hill", "start_time": "08:00"}]}],
            {"estimated_total": 21000, "accommodation": 9000, "food": 6000,
             "transport": 4000, "local_transport": 1200, "activities": 600,
             "miscellaneous": 200, "budget_limit": 60000},
            "Your plan is ready.", trip_context=_FULL_CTX)

    body = _decoded_email_body(captured["body"])
    assert "Hotel Summit" in body, "the chosen stay must be in the email"
    assert "Tiger Hill" in body
    assert "\u20b9" in body or "&#8377;" in body, "amounts must be in INR"
    assert "Rock Garden" in body, "selected-but-unscheduled places must be disclosed"


# ---------------------------------------------------------------------------
# G — The persistent assistant renders persisted state only
# ---------------------------------------------------------------------------

def test_assistant_context_is_read_only_and_runs_no_agents():
    wf = _bare_workflow()
    wf.state["itinerary"] = [{"day": 1, "date": "2026-11-01",
                              "activities": [{"name": "Tiger Hill"}]}]
    wf.state["unscheduled_attractions"] = [{"name": "Rock Garden", "reason": "no_time"}]
    wf.state["selected_accommodation"] = {"name": "Hotel Summit"}

    ctx = wf.get_assistant_context()

    assert ctx["trip"]["destination"] == "Darjeeling"
    assert ctx["itinerary_days"] == [{"day": 1, "date": "2026-11-01",
                                      "activity_count": 1}]
    assert ctx["unscheduled_attractions"][0]["name"] == "Rock Garden"
    assert ctx["accommodation"]["name"] == "Hotel Summit"
    assert ctx["email_sent"] is True
    assert "budget" in ctx and "provider_status" in ctx
    for agent in ("strategy_agent", "route_agent", "retrieval_agent",
                  "recommend_agent", "transit_agent", "info_agent"):
        assert getattr(wf, agent).method_calls == [], (
            f"{agent} must not be re-run when the assistant renders navigation state"
        )


def test_assistant_endpoint_returns_context_without_a_session(monkeypatch):
    import main

    main._EMAIL_INFLIGHT.clear()
    app = main.app
    app.config.update({"TESTING": True})
    client = app.test_client()

    resp = client.get("/api/assistant")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "success"
    assert payload["context"]["has_trip"] is False
    assert payload["context"]["conversation"] == []


def test_assistant_endpoint_reads_the_sessions_own_state(monkeypatch):
    import main

    app = main.app
    app.config.update({"TESTING": True})
    wf = _bare_workflow()
    wf.get_assistant_context = lambda: {
        "step": "route", "trip": {"destination": "Darjeeling"},
        "budget_warnings": [], "provider_status": None, "email_sent": False,
    }
    wf.get_conversation_history = lambda: [{"role": "user", "content": "3 days in Darjeeling"}]
    main.workflows["assistant-sess"] = wf
    try:
        client = app.test_client()
        with client.session_transaction() as s:
            s["session_id"] = "assistant-sess"
        resp = client.get("/api/assistant")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["context"]["trip"]["destination"] == "Darjeeling"
        assert body["context"]["has_trip"] is True
        assert body["context"]["conversation"][0]["content"].startswith("3 days")
    finally:
        main.workflows.pop("assistant-sess", None)


def test_new_trip_resets_trip_data_but_keeps_identity_and_isolates_sessions():
    wf = _bare_workflow()
    wf.default_user_info = {"city": None, "name": "Priya", "email": "p@example.com"}
    wf.state["user_info"]["city"] = "Darjeeling"
    wf.state["itinerary"] = [{"day": 1}]
    wf.session_states["s1"] = wf.state

    fresh = wf.new_trip_state("s1")
    other = wf.get_session_state("s2")

    assert fresh["itinerary"] == [] and fresh["unscheduled_attractions"] == []
    assert fresh["email_sent"] is False and fresh["current_step"] == "chat"
    assert "city" not in json.dumps(other["user_info"]) or other["user_info"].get("city") is None
    assert other is not wf.session_states["s1"], "each session gets its own state object"


# ---------------------------------------------------------------------------
# H — One error shape, no leaked internals
# ---------------------------------------------------------------------------

def test_client_error_message_hedges_internal_details():
    import main

    generic = "Something went wrong on our side. Please try again."
    assert main.client_error_message(ValueError("NoneType has no attribute 'days'")) == generic
    assert main.client_error_message(RuntimeError('bad response from rapidapi key ABC123')) == generic
    assert main.client_error_message(Exception("   ")) == generic
    # A plainly worded, non-internal message is passed through untouched.
    assert main.client_error_message(Exception("Weather data unavailable")) == \
        "Weather data unavailable"
    # error_response() always prefers its explicit operator-written fallback.
    exc = Exception("something odd")
    with patch.object(main, "log_error", return_value="deadbeef"):
        app = main.app.test_request_context(json={})
        with app:
            payload, status = main.error_response(exc, "test", fallback="Weather is offline")
        assert status == 500
        assert payload.get_json()["error"] == "Weather is offline"


def test_process_endpoint_error_does_not_leak_the_exception(monkeypatch):
    import main

    app = main.app
    app.config.update({"TESTING": True})
    boom = MagicMock()
    boom.process_step.side_effect = RuntimeError("Gemini call failed: key sk-SECRET-VALUE")
    main.workflows["leak-sess"] = boom
    try:
        client = app.test_client()
        with client.session_transaction() as s:
            s["session_id"] = "leak-sess"
        resp = client.post("/api/process", json={"step": "chat", "user_input": "hi"})

        assert resp.status_code == 500
        body = resp.get_json()
        assert "sk-SECRET-VALUE" not in resp.get_data(as_text=True), (
            "provider credentials must never reach the client"
        )
        assert body["status"] == "error" and body["success"] is False
        assert body["error"] == body["message"]
        assert len(body["request_ref"]) == 8, "a reference is needed to correlate the log"
    finally:
        main.workflows.pop("leak-sess", None)


def test_state_endpoint_reports_selection_and_provider_health():
    import main

    app = main.app
    app.config.update({"TESTING": True})
    wf = _bare_workflow()
    wf.state["budget_normalized"] = {"total": 21000, "currency": "INR"}
    wf.state["provider_status"] = {"overpass": {"state": "ok", "detail": "12 POIs"}}
    main.workflows["state-sess"] = wf
    try:
        client = app.test_client()
        with client.session_transaction() as s:
            s["session_id"] = "state-sess"
        body = client.get("/api/state").get_json()

        assert body["status"] == "success"
        trip = body["trip"]
        assert trip["budget_normalized"]["currency"] == "INR"
        assert "unscheduled_attractions" in trip
        assert "provider_status" in trip
        assert "selected_accommodation" in trip
        assert isinstance(trip["itinerary"], dict) and "days" in trip["itinerary"], (
            "state must hand the SPA the same normalized day contract as the stream"
        )
    finally:
        main.workflows.pop("state-sess", None)
