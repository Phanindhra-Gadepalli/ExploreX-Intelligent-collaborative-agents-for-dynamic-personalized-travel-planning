"""
ExploreX end-to-end validation driver (headless, real multi-agent workflow).
============================================================================
Drives the ACTUAL Flask /api/process endpoint through the full agent pipeline
(chat -> information -> retrieval -> recommend -> strategy -> communication ->
route) using the Phase-10C representative scenario, then validates the final
itinerary / budget object.

It uses Flask's test_client so no network port is required, but every request
goes through the real route handler, real TravelGraph, real agents and (where
reachable) real Gemini / external APIs. Nothing is mocked here; live-API and
offline-fallback outcomes are both reported truthfully.
"""
import sys, io, json, warnings

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import logging
logging.getLogger("langchain_google_genai._function_utils").setLevel(logging.ERROR)

import main  # Flask app; imports the whole agent stack

client = main.app.test_client()

# A single stable session id so TravelGraph.session_states persists across the
# whole driven conversation (the real frontend reuses one session id too).
FIXED_SESSION_ID = "e2e-validation-session"

# Ensure a session/workflow exists (exercises the landing route too).
r = client.get("/")
print(f"[BOOT] GET / -> {r.status_code}")

SESSION_FIELDS = {
    "origin_city": "Delhi",
    "city": "Kerala",
    "days": 5,
    "people": 10,          # 10 adults
    "kids": 0,
    "health": "good",
    "hobbies": "viewpoints and photography",
    "start_date": "2026-11-05",
    "budget_amount": int(sys.argv[1]) if len(sys.argv) > 1 else 200000,
    "budget_strictness": "strict",
    "budget": "medium",
}

_BUDGET = SESSION_FIELDS["budget_amount"]

INITIAL_MESSAGE = (
    "Hi, I am Arjun. I want to plan a trip from Delhi to Kerala for 5 days "
    "starting on 2026-11-05. It is a group of 10 adults and 0 children. "
    f"My total strict budget for the whole group is {_BUDGET} rupees. "
    "We love viewpoints and photography. Everyone is in good health."
)


def post_step(step, extra=None):
    payload = {"step": step, "session_id": FIXED_SESSION_ID}
    if extra:
        payload.update(extra)
    resp = client.post("/api/process", json=payload)
    try:
        data = resp.get_json()
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Non-JSON response for step {step}: status={resp.status_code} body={resp.data[:400]}")
        raise
    if resp.status_code != 200 or (isinstance(data, dict) and data.get("error")):
        print(f"[ERROR] step={step} status={resp.status_code} error={data.get('error') if isinstance(data, dict) else data}")
    return data or {}


def drive():
    # 1. CHAT
    data = post_step("chat", {"user_input": INITIAL_MESSAGE})
    state = data.get("state", {})
    info = state.get("user_info", {})
    print(f"[CHAT] next_step={data.get('next_step')} missing={data.get('missing_fields')}")
    guard = 0
    while data.get("next_step") == "chat" and guard < 6:
        guard += 1
        missing = data.get("missing_fields") or []
        # Supply any still-missing fields explicitly.
        filler = " ".join(f"{f} is {SESSION_FIELDS[f]}" for f in missing if f in SESSION_FIELDS)
        if not filler:
            filler = "continue"
        print(f"[CHAT-LOOP] filling missing={missing}")
        data = post_step("chat", {"user_input": filler})
        state = data.get("state", {})
        info = state.get("user_info", {})

    print("[INFO] user_info after chat:", json.dumps(info, ensure_ascii=False))

    # 2. Follow the returned next_step chain deterministically.
    # information (auto) -> retrieval (auto) -> recommend (continue) ->
    # strategy (continue to generate) -> strategy (continue to proceed) ->
    # communication (continue tips) -> communication (proceed) -> route.
    step = data.get("next_step")
    hops = 0
    last = data
    comm_visits = 0
    while step and step not in ("complete", "error") and hops < 30:
        hops += 1
        if step == "information":
            last = post_step("information", {"user_input": "continue"})
        elif step == "retrieval":
            last = post_step("retrieval", {"user_input": "continue"})
        elif step == "recommend":
            last = post_step("recommend", {"user_input": "continue", "force_continue": "true"})
        elif step == "strategy":
            last = post_step("strategy", {"user_input": "continue"})
        elif step == "communication":
            # First visit: 'continue' -> generate travel tips (stays on step).
            # Second visit: anything else -> proceed to route / final itinerary.
            comm_visits += 1
            msg = "continue" if comm_visits == 1 else "proceed to itinerary"
            last = post_step("communication", {"user_input": msg})
        elif step == "route":
            last = post_step("route", {"user_input": "continue"})
        else:
            print(f"[WARN] Unexpected next_step: {step}")
            break
        step = last.get("next_step")
        print(f"[HOP {hops}] step->{last.get('next_step')}")

    return last


result = drive()
final_state = result.get("state") or (client.post("/api/process", json={"step": "route"}).get_json() or {}).get("state", {})

print("\n" + "=" * 70)
print("FINAL VALIDATION REPORT")
print("=" * 70)
itinerary = final_state.get("itinerary") or []
budget = final_state.get("budget") or {}
info = final_state.get("user_info") or {}

print(f"next_step           : {result.get('next_step')}")
print(f"destination         : {info.get('city')}")
print(f"days requested      : {info.get('days')}")
print(f"itinerary entries   : {len(itinerary)}")
print(f"budget estimated    : {budget.get('estimated_total')}")
print(f"budget limit        : {budget.get('budget_limit')}")
print(f"budget within       : {budget.get('within_budget')}")
print(f"budget infeasible   : {budget.get('budget_infeasible')}")
print(f"budget warning      : {budget.get('budget_warning')}")

dates = [d.get("date") for d in itinerary]
print(f"day dates           : {dates}")
uniq_dates = {d for d in dates if d}
print(f"unique dates count  : {len(uniq_dates)} (should equal itinerary length -> no repeated dates)")

names_flat = []
for d in itinerary:
    for a in d.get("activities", []) or []:
        names_flat.append(a.get("name"))
print(f"total activities    : {len(names_flat)}")
print(f"sample activities   : {names_flat[:8]}")

if itinerary:
    print("\nDAY-BY-DAY BREAKDOWN:")
    for d in itinerary:
        acts = [a.get("name") for a in d.get("activities", []) or []]
        print(f"  Day {d.get('day')}: {d.get('date')} -> {len(acts)} activities: {acts}")

print("\nRESULT:", "ITINERARY GENERATED" if itinerary else (
    "TRANSPARENT BUDGET INFEASIBILITY REPORT" if budget.get("budget_infeasible") else "NO ITINERARY"))
