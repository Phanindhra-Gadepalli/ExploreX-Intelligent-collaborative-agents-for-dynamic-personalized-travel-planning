"""
Regression tests for the Transport-options and Budget-page repairs.

Covered here:

  * ``services.transit_norm.normalize_transit_options`` — the journey
    (origin → destination) is always present, prices are never silently
    relabelled as rupees, an unusable fare becomes ``None`` instead of a free
    ticket, the function is idempotent, and legacy shapes are still readable.
  * ``BudgetAgent._calculate_intercity_transport_cost`` — only genuinely-INR
    quotes feed the budget, and the shared transit payload is not mutated.
  * ``TransitAgent.get_transit_options`` (mocked LLM) — international routes are
    requested in INR, one-way, and the reply is returned in the canonical shape.
  * Budget page layout — the wrap-up/greetings block is a sibling *below*
    ``.budget-container`` so it can never render beside the money again.
  * Transport tab rendering (source guards) — the route is shown, the currency
    comes from the data and the raw ``per_person`` token is no longer printed.
  * Email transport section — names the route and formats each fare in its own
    currency.

Nothing here touches the network: the LLM, SMTP and HTTP layers are all mocked.
"""
import json
import os

from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_JS = os.path.join(ROOT, "frontend", "static", "js", "main.js")
INDEX_HTML = os.path.join(ROOT, "frontend", "templates", "index.html")
STYLES_CSS = os.path.join(ROOT, "frontend", "static", "css", "styles.css")

from services.transit_norm import normalize_transit_options  # noqa: E402


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _option(**overrides):
    opt = {
        "mode": "flight",
        "provider": "Major international carrier",
        "duration": "4h 15m",
        "estimated_cost": 14500,
        "cost_basis": "per_person",
        "currency": "INR",
        "is_estimate": True,
    }
    opt.update(overrides)
    return opt


# ---------------------------------------------------------------------------
# 1. normalize_transit_options — the journey and the money are always explicit
# ---------------------------------------------------------------------------

def test_normalize_stamps_the_journey_on_the_payload_and_every_option():
    raw = {"intercity_transport": [_option(), _option(mode="train", estimated_cost=6000)]}
    out = normalize_transit_options(raw, origin="Delhi", destination="Dubai", num_people=10)

    assert out["origin"] == "Delhi"
    assert out["destination"] == "Dubai"
    assert out["route_label"] == "Delhi → Dubai"
    assert out["trip_basis"] == "one_way"
    for opt in out["intercity_transport"]:
        assert opt["origin"] == "Delhi" and opt["destination"] == "Dubai"


def test_session_origin_and_destination_win_over_the_model_reply():
    raw = {"origin": "Some Other City", "destination": "",
           "intercity_transport": [_option()]}
    out = normalize_transit_options(raw, origin="Delhi", destination="Dubai")
    assert out["origin"] == "Delhi"
    assert out["destination"] == "Dubai"
    assert out["intercity_transport"][0]["origin"] == "Delhi"


def test_group_cost_is_derived_arithmetic_not_a_second_guess():
    out = normalize_transit_options({"intercity_transport": [_option()]},
                                    origin="Delhi", destination="Dubai", num_people=10)
    opt = out["intercity_transport"][0]
    assert opt["estimated_cost"] == 14500
    assert opt["group_cost"] == 145000
    assert opt["cost_status"] == "estimated"
    assert opt["price_inr"] == 14500


def test_a_foreign_currency_quote_is_kept_honest_and_excluded_from_inr():
    """A USD fare rendered as ₹320 was the reported transport bug."""
    out = normalize_transit_options(
        {"intercity_transport": [_option(estimated_cost=320, currency="USD")]},
        origin="Delhi", destination="Dubai", num_people=10)
    opt = out["intercity_transport"][0]
    assert opt["currency"] == "USD"
    assert opt["cost_status"] == "non_inr_currency"
    assert opt["price_inr"] is None
    assert opt["group_cost"] is None


def test_a_missing_or_zero_fare_becomes_no_price_rather_than_a_free_ticket():
    out = normalize_transit_options(
        {"intercity_transport": [_option(estimated_cost=0), _option(mode="train", estimated_cost=None)]},
        origin="Delhi", destination="Dubai")
    statuses = [o["cost_status"] for o in out["intercity_transport"]]
    assert statuses == ["unavailable", "unavailable"]
    assert all(o["estimated_cost"] is None for o in out["intercity_transport"])


def test_normalize_is_idempotent_and_preserves_a_provider_failure():
    first = normalize_transit_options({"intercity_transport": [_option()]},
                                      origin="Delhi", destination="Dubai", num_people=4)
    second = normalize_transit_options(first, origin="Delhi", destination="Dubai", num_people=4)
    assert second == first

    failed = normalize_transit_options(None, origin="Delhi", destination="Dubai")
    failed["provider_status"] = "error"
    again = normalize_transit_options(failed, origin="Delhi", destination="Dubai")
    assert again["provider_status"] == "error"
    assert again["intercity_transport"] == []


def test_legacy_bucket_and_options_shapes_are_still_readable():
    legacy = normalize_transit_options({"flights": [{"provider": "Carrier", "estimated_cost": 14500}],
                                        "trains": []}, origin="Delhi", destination="Dubai")
    assert [o["mode"] for o in legacy["intercity_transport"]] == ["flight"]

    front_end_shape = normalize_transit_options(
        {"options": [{"mode": "bus", "estimated_cost": 900}]},
        origin="Pune", destination="Goa")
    assert front_end_shape["intercity_transport"][0]["mode"] == "bus"


def test_same_city_trips_have_no_intercity_leg_at_all():
    out = normalize_transit_options(
        {"intercity_transport": [_option(origin="Guwahati", destination="Guwahati")]},
        origin="Guwahati", destination="Guwahati", same_city=True)
    assert out["same_city"] is True
    assert out["intercity_transport"] == []


def test_a_recommendation_with_no_matching_card_is_dropped():
    out = normalize_transit_options(
        {"intercity_transport": [_option()], "recommended_mode": "helicopter",
         "recommended_local_mode": "rental_car",
         "local_transport": [{"mode": "rental_car", "estimated_cost": 2500,
                              "cost_basis": "per_day"}]},
        origin="Delhi", destination="Dubai")
    assert out["recommended_mode"] is None
    assert out["recommended_local_mode"] == "rental_car"


def test_duplicate_options_are_collapsed():
    out = normalize_transit_options(
        {"intercity_transport": [_option(), _option()],
         "recommended_mode": "flight"},
        origin="Delhi", destination="Dubai")
    assert len(out["intercity_transport"]) == 1


# ---------------------------------------------------------------------------
# 2. BudgetAgent — only INR quotes feed the ₹ budget, and nothing is mutated
# ---------------------------------------------------------------------------

def _budget_agent():
    from agents.budget_agent import BudgetAgent
    return BudgetAgent()


def test_budget_counts_only_genuinely_inr_fares_and_prices_a_return_trip():
    agent = _budget_agent()
    payload = normalize_transit_options(
        {"intercity_transport": [_option(), _option(mode="train", estimated_cost=4000,
                                                    currency="USD")],
         "recommended_mode": "flight"},
        origin="Delhi", destination="Dubai", num_people=10)
    cost, mode = agent._calculate_intercity_transport_cost(
        payload, "medium", 10, budget_amount=None, subtotal=0.0)
    # 14,500 one way x 2 legs x 10 travellers; the USD train is not convertible here.
    assert mode == "flight"
    assert cost == 14500 * 2 * 10


def test_budget_returns_zero_when_no_fare_is_in_rupees():
    agent = _budget_agent()
    payload = normalize_transit_options(
        {"intercity_transport": [_option(currency="USD", estimated_cost=320)]},
        origin="Delhi", destination="Dubai")
    cost, mode = agent._calculate_intercity_transport_cost(
        payload, "medium", 10, budget_amount=None, subtotal=0.0)
    assert cost == 0.0 and mode == "N/A"


def test_budget_no_longer_writes_price_inr_back_into_the_transit_payload():
    agent = _budget_agent()
    payload = normalize_transit_options(
        {"intercity_transport": [_option()]}, origin="Delhi", destination="Dubai")
    before = json.loads(json.dumps(payload, default=str))
    agent._calculate_intercity_transport_cost(payload, "medium", 2,
                                              budget_amount=None, subtotal=0.0)
    assert payload == before


# ---------------------------------------------------------------------------
# 3. TransitAgent (mocked LLM) — INR + one-way, even for international routes
# ---------------------------------------------------------------------------

def _bare_transit_agent():
    from agents.transit_agent import TransitAgent
    agent = TransitAgent.__new__(TransitAgent)
    agent.model = MagicMock()
    return agent


def test_international_routes_are_requested_in_inr_and_one_way():
    agent = _bare_transit_agent()
    resp = MagicMock()
    resp.content = json.dumps({"intercity_transport": [_option()]})
    agent.model.invoke.return_value = resp

    agent.get_transit_options(origin="Delhi", destination="Dubai", start_date="2026-11-01",
                              budget_level="medium", is_international=True, num_people=10)
    prompt = agent.model.invoke.call_args[0][0][-1].content

    assert "Quote every price in Indian Rupees (INR)" in prompt
    assert "Do NOT return" in prompt and "USD" in prompt          # explicit prohibition
    assert "ONE-WAY price per person" in prompt
    assert '"currency": "USD"' not in prompt                      # the old USD schema is gone
    assert "Delhi" in prompt and "Dubai" in prompt


def test_transit_agent_returns_the_canonical_payload_for_a_good_reply():
    agent = _bare_transit_agent()
    resp = MagicMock()
    resp.content = "```json\n" + json.dumps({
        "origin": "Delhi", "destination": "Dubai", "currency": "INR",
        "intercity_transport": [_option()],
        "local_transport": [{"mode": "app_cab", "estimated_cost": 150, "cost_basis": "per_trip"}],
        "recommended_mode": "flight",
    }) + "\n```"
    agent.model.invoke.return_value = resp

    out = agent.get_transit_options("Delhi", "Dubai", "2026-11-01",
                                    is_international=True, num_people=10)
    assert out["route_label"] == "Delhi → Dubai"
    assert out["provider_status"] == "ok"
    assert out["intercity_transport"][0]["group_cost"] == 145000
    assert out["local_transport"][0]["origin"] == "Delhi"


# ---------------------------------------------------------------------------
# 4. Budget page layout — greetings below the money, never beside it
# ---------------------------------------------------------------------------

def _budget_container_span(html):
    """Return (start, end) offsets of the ``.budget-container`` element."""
    start = html.index('<div class="budget-container')
    depth = 0
    idx = start
    while True:
        opening = html.find("<div", idx)
        closing = html.find("</div>", idx)
        if closing == -1:
            raise AssertionError("unterminated .budget-container in index.html")
        if opening != -1 and opening < closing:
            depth += 1
            idx = opening + 4
            continue
        depth -= 1
        idx = closing + 6
        if depth == 0:
            return start, idx


def test_budget_closing_is_a_sibling_below_the_budget_container():
    html = _read(INDEX_HTML)
    start, end = _budget_container_span(html)
    closing = html.index('id="budget-closing"')
    assert closing > end, (
        "The wrap-up block must live outside .budget-container; that container is "
        "a flex row, so nesting it there renders the greetings beside the budget."
    )
    section_end = html.index("</section>", closing)
    assert closing < section_end, "budget-closing must stay inside the budget view"


def test_budget_closing_css_lays_it_out_full_width_below():
    css = _read(STYLES_CSS)
    assert ".budget-container { display: flex" in css
    rule_start = css.index(".budget-closing {")
    rule = css[rule_start:css.index("}", rule_start)]
    assert "display: block" in rule and "width: 100%" in rule and "margin-top" in rule


# ---------------------------------------------------------------------------
# 5. Transport tab (source guards) — route visible, currency from the data
# ---------------------------------------------------------------------------

def test_transport_tab_shows_the_route_it_is_pricing():
    js = _read(MAIN_JS)
    assert "setAttribute('data-testid', 'transit-route')" in js
    assert "Getting from" in js
    assert 'data-testid="transit-leg"' in js
    assert "function transitLegText" in js


def test_transport_tab_no_longer_hardcodes_a_rupee_sign_onto_every_fare():
    js = _read(MAIN_JS)
    assert "'₹' + String(opt.estimated_cost)" not in js
    assert "'₹' + String(lo.estimated_cost)" not in js
    assert "const CURRENCY_SYMBOLS" in js
    assert "function money(value, currency)" in js


def test_cost_basis_is_printed_in_words_not_as_a_snake_case_token():
    js = _read(MAIN_JS)
    assert "per person" in js and "per vehicle" in js
    assert "escapeHtml(String(opt.cost_basis))" not in js
    assert "escapeHtml(String(lo.cost_basis))" not in js


def test_transport_tab_still_never_claims_a_live_booking():
    js = _read(MAIN_JS)
    assert "Planning estimates, not live bookings." in js
    assert "'transit-estimate-banner'" in js


# ---------------------------------------------------------------------------
# 6. Email transport section — route named, fare in its own currency
# ---------------------------------------------------------------------------

def test_email_transit_section_names_the_route_and_uses_the_right_symbol():
    from services.email_service import _render_transit
    payload = normalize_transit_options(
        {"intercity_transport": [_option()],
         "local_transport": [{"mode": "app_cab", "estimated_cost": 150, "cost_basis": "per_trip"}],
         "recommended_mode": "flight"},
        origin="Delhi", destination="Dubai", num_people=10)
    html, text = _render_transit(payload)
    assert "Getting There &amp; Back (Delhi → Dubai)" in html
    assert "Getting There & Back (Delhi → Dubai)" in text
    assert "14,500" in text and "₹" in text
    assert "per person, one way" in text
    assert "150" in text


def test_email_does_not_relabel_a_usd_fare_as_rupees():
    from services.email_service import _render_transit
    payload = normalize_transit_options(
        {"intercity_transport": [_option(estimated_cost=320, currency="USD")]},
        origin="Delhi", destination="Dubai")
    _html, text = _render_transit(payload)
    assert "$320" in text
    assert "₹320" not in text
