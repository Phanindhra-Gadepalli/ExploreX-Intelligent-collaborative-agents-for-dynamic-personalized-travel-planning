import sys

# ---------------------------------------------------------------------------
# Console safety
# ---------------------------------------------------------------------------
# (1) Fix the Windows console encoding so non-ASCII provider text cannot raise
#     UnicodeEncodeError.  This must use ``reconfigure``: wrapping
#     ``sys.stdout.buffer`` in a second TextIOWrapper leaves the original
#     wrapper unreferenced when a launcher/IDE replaced the streams, so the
#     garbage collector closes the shared buffer.  Every later flush then
#     fails with ``OSError: [Errno 22] Invalid argument`` - typically inside
#     tqdm while the embedding model loads, which surfaced as a 500 on the
#     landing page.
# (2) Never let a broken/closed console turn a handled error into a crash.
#     ``safe_print``/``log_exception`` swallow stream errors so logging can
#     never take down a request.
def _configure_console_encoding():
    """Re-encode the console streams in place, never by proxying their buffer."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            encoding = (getattr(stream, "encoding", "") or "").lower()
            if encoding in ("utf-8", "utf8"):
                continue
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            pass


_configure_console_encoding()


def safe_print(*args, **kwargs):
    """``print`` that can never raise because the console was closed."""
    try:
        print(*args, **kwargs)
    except (OSError, ValueError, UnicodeError, AttributeError):
        pass


def log_exception(context=""):
    """Print the active traceback without depending on a working console."""
    try:
        text = traceback.format_exc()
    except Exception:  # noqa: BLE001 - logging must never raise
        text = "traceback unavailable"
    if context:
        safe_print(f"[ERROR] {context}")
    safe_print(text)

# Definitive fix for chromadb posthog telemetry errors and SQLite threading issues
from unittest.mock import MagicMock
sys.modules['posthog'] = MagicMock()

from flask import Flask, render_template, request, jsonify, session, send_from_directory, send_file, Response, redirect
from flask_session import Session
import os
import json
import time
import uuid
import queue
import threading
import traceback
import requests
from dotenv import load_dotenv
from workflows.travel_graph import TravelGraph

import warnings
from services.email_service import send_trip_email, is_valid_email, normalize_itinerary
from services import user_store
from services.user_store import AuthError

# Suppress the harmless schema title warning from langchain-google-genai
warnings.filterwarnings("ignore", message=".*Key 'title' is not supported in schema.*")

# Disable chromadb telemetry
os.environ["ANONYMIZED_TELEMETRY"] = "False"

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder="frontend/static", template_folder="frontend/templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "travel-ai-secret")

# Configure session
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Send the session cookie only over HTTPS when running in production (or when
# an explicit COOKIE_SECURE=1 is set); keep it off for plain-HTTP local dev.
app.config['SESSION_COOKIE_SECURE'] = (
    os.environ.get('COOKIE_SECURE') == '1'
    or os.environ.get('FLASK_ENV') == 'production'
    or os.environ.get('PRODUCTION') == '1'
)

# ── Startup side-effects that must run ONLY in the actual serving process ────
# When debug=True, Werkzeug spawns two processes:
#   • Parent (stat-watcher / reloader monitor) — runs main.py once to watch files
#   • Child  (WERKZEUG_RUN_MAIN=true)          — the real serving process
#
# Without this guard, stale-session cleanup and other one-time startup actions
# would run twice, which is the root cause of the double initialisation logs.
# ─────────────────────────────────────────────────────────────────────────────
import shutil
_is_serving_process = (os.environ.get("WERKZEUG_RUN_MAIN") == "true"
                       or os.environ.get("FLASK_ENV") == "production"
                       or os.environ.get("FLASK_DEBUG", "1") == "0")

if _is_serving_process:
    session_dir = app.config.get('SESSION_FILE_DIR', 'flask_session')
    if os.path.exists(session_dir):
        try:
            shutil.rmtree(session_dir)
            print(f"[STARTUP] Cleared stale session directory: {session_dir}")
        except Exception as e:
            print(f"[STARTUP] Could not clear session directory: {e}")
else:
    # This is the Werkzeug reloader/stat-watcher parent process.
    # It just monitors source files and respawns the child on changes.
    # No expensive initialisation should happen here.
    print("[STARTUP] Werkzeug reloader parent process — skipping session cleanup.")

# Initialize Flask-Session
Session(app)

# Add static file configuration
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create a session store for workflows
workflows = {}

# Bounded in-process store. Sessions use random ids, so the oldest entries can
# never be reused; dropping them keeps memory (and any per-workflow resources)
# finite in a long-running process.
MAX_WORKFLOWS = int(os.getenv("MAX_WORKFLOWS", "500"))
APP_STARTED_AT = time.time()


def prune_workflows(max_items=None):
    """Drop the oldest workflow objects when the store exceeds its bound.

    Returns the number of objects evicted. Safe to call on every session
    creation; no other module keeps a strong reference to an evicted workflow.
    """
    limit = max(1, int(max_items if max_items is not None else MAX_WORKFLOWS))
    evicted = 0
    try:
        while len(workflows) > limit:
            oldest = next(iter(workflows))
            workflows.pop(oldest, None)
            evicted += 1
    except Exception as exc:
        print(f"[HEALTH][WARN] workflow pruning failed: {type(exc).__name__}")
    if evicted:
        print(f"[HEALTH] Evicted {evicted} stale workflow(s); active={len(workflows)}")
    return evicted


def _health_payload():
    """Lightweight, secret-free liveness/readiness payload.

    Deliberately does not call an LLM, geocoder or provider: a health probe must
    be fast and must not consume quota. It reports configuration *presence*
    only, never key values.
    """
    checks = {
        "session_store": True,
        "workflow_store": len(workflows) <= max(1, MAX_WORKFLOWS),
        "llm_configured": bool(
            os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        ),
        "geocoder_configured": bool(
            os.getenv("GEOAPIFY_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
        ),
        "email_configured": bool(
            os.getenv("MAIL_SERVER") or os.getenv("SMTP_HOST") or os.getenv("EMAIL_HOST")
        ),
    }
    # Only the in-process store is a hard readiness requirement; optional
    # providers degrade features, they do not stop the app serving.
    ready = checks["session_store"] and checks["workflow_store"]
    return {
        "status": "ok" if ready else "degraded",
        "service": "explorex",
        "version": os.getenv("APP_VERSION", "1.0"),
        "uptime_seconds": round(time.time() - APP_STARTED_AT, 1),
        "workflow_count": len(workflows),
        "checks": checks,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health():
    """Liveness/readiness probe (no authentication, no provider calls)."""
    payload = _health_payload()
    return jsonify(payload), (200 if payload["status"] == "ok" else 503)

# Requirement 6: in-flight email guard keyed by session_id -> unix timestamp.
# Prevents a double-click or a client retry storm from dispatching the same
# itinerary multiple times to the SMTP provider. Entries expire after 30s.
_EMAIL_INFLIGHT = {}

# Human-readable trace labels for the real multi-agent loop. Each key is an
# actual process_step name; the value names the agent(s) that genuinely run in
# that step. Used only to label the SSE 'status' events — no behavior change.
AGENT_TRACE_LABELS = {
    "chat": "Travel Planning Agent",
    "information": "Destination / POI · Weather · Stay · Food Agents",
    "retrieval": "Knowledge Retrieval Agent (RAG)",
    "recommend": "Recommendation Agent",
    "strategy": "Strategy & Budget Agent",
    "communication": "Transportation & Budget Agents",
    "route": "Itinerary Agent",
}

# Truthful keep-alive labels for long-running steps. Long stages (e.g. the
# information stage doing live POI/weather/hotel lookups) can run for minutes
# without emitting a chunk; these periodic SSE 'progress' events tell the
# client the step is still genuinely executing and reset its idle watchdog.
AGENT_PROGRESS_MESSAGES = {
    "chat": "Understanding your request",
    "information": "Researching attractions, stays and food options",
    "retrieval": "Fetching destination background knowledge",
    "recommend": "Ranking attractions for your interests",
    "strategy": "Building your day-by-day plan",
    "budget": "Checking costs against your budget",
    "communication": "Writing your itinerary summary and travel tips",
    "route": "Optimising travel routes between attractions",
}
STREAM_KEEPALIVE_SECONDS = 15


# ── Phase 9: one error contract for every endpoint ───────────────────────────
# User-facing responses must never contain a Python exception string, a stack
# trace, a provider API key or another traveller's PII. The detail stays in the
# server log, correlated by a short reference the user can quote to support.
_INTERNAL_MARKERS = (
    "attributeerror", "none", "keyerror", "indexerror", "typeerror", "valueerror",
    "json decode", "traceback", "expected", "positional argument", "non-serializable",
    "connection aborted", "read timed out", "rapidapi", "api key", "bearer ",
)


def log_error(context, exc):
    """Log a full server-side trace and return a short correlation reference."""
    ref = uuid.uuid4().hex[:8]
    safe_print(f"[ERROR][{context}][ref={ref}] {type(exc).__name__}: {exc}")
    log_exception()
    return ref


def client_error_message(exc, fallback="Something went wrong on our side. Please try again."):
    """Return a safe, human-readable message, or the fallback if the exception
    text looks like an internal error or could expose secrets."""
    detail = " ".join(str(exc).split())[:200]
    if not detail:
        return fallback
    lowered = detail.lower()
    if any(marker in lowered for marker in _INTERNAL_MARKERS):
        return fallback
    return detail


def error_response(exc, context, status=500, fallback=None, extra=None):
    """Build the standard error payload: {status, success, error, message,
    request_ref}. The 'error' key is kept because the existing SPA reads it."""
    ref = log_error(context, exc)
    message = fallback or client_error_message(exc)
    payload = {
        "status": "error",
        "success": False,
        "error": message,
        "message": message,
        "request_ref": ref,
    }
    if extra:
        payload.update(extra)
    return jsonify(payload), status


@app.route('/test-image')
def test_image():
    return send_file('frontend/static/images/background.jpg', mimetype='image/jpeg')

@app.route('/static/images/<path:filename>')
def serve_image(filename):
    return send_from_directory('frontend/static/images', filename)



def normalize_itinerary_for_client(raw_itinerary):
    """Project the stored itinerary onto the contract the SPA expects.

    Day keys arrive from the planner in several shapes (1, "1", "day1",
    "Day 2"). The frontend needs ONE representation - a positive integer -
    otherwise the All Days / Day N filters silently mismatch and show nothing.

    The result is ALWAYS ``{"days": [...]}`` - including for a trip that has not
    been planned yet - so no consumer has to guess which shape it received.
    """
    if not raw_itinerary or not isinstance(raw_itinerary, list):
        return {"days": []}
    import re as _re
    days = []
    for idx, day in enumerate(raw_itinerary, start=1):
        if not isinstance(day, dict):
            continue
        # Resolve the day NUMBER: prefer an explicit field, fall back to order.
        raw_day = day.get("day", day.get("day_number"))
        match = _re.search(r"(\d+)", str(raw_day if raw_day is not None else ""))
        day_number = int(match.group(1)) if match else idx

        # Route agent writes "activities"; keep "spots" as a legacy alias.
        # Work on shallow copies so projecting the client contract can never
        # mutate the authoritative server-side itinerary.
        raw_spots = [dict(s) for s in (day.get("activities") or day.get("spots") or []) if isinstance(s, dict)]
        d = {
            "day": day_number,
            "date": day.get("date"),
            "is_suggested_date": day.get("is_suggested_date"),
            "morning": [],
            "afternoon": [],
            "evening": [],
            "night": [],
            "meals": [],
            "transport": day.get("transport") or {},
            "activities": raw_spots,
            "spots": raw_spots,
            "unscheduled": day.get("unscheduled") or [],
        }
        # Routing provenance is computed per day by the route agent. Dropping it
        # here would make the client contract less informative than the state it
        # projects, so carry it through unchanged (including explicit nulls).
        for field in ("routing_source", "routing_status", "routing_optimized", "routing_note"):
            if field in day:
                d[field] = day[field]
        for seq, spot in enumerate(raw_spots, start=1):
            # Every activity carries its day + position so the map, the itinerary
            # page and the email can all reference the same stop unambiguously.
            spot.setdefault("day", day_number)
            spot.setdefault("sequence", seq)
            if spot.get("latitude") is None and isinstance(spot.get("location"), dict):
                spot["latitude"] = spot["location"].get("lat") or spot["location"].get("latitude")
            if spot.get("longitude") is None and isinstance(spot.get("location"), dict):
                spot["longitude"] = spot["location"].get("lng") or spot["location"].get("longitude")
            if spot.get("attraction_id") is None and spot.get("id") is not None:
                spot["attraction_id"] = str(spot["id"])
            if spot.get("is_meal"):
                d["meals"].append(spot)
                continue
            t = str(spot.get("start_time") or spot.get("time") or "").lower()
            b = "afternoon"
            if "am" in t or t.startswith("0") or t.startswith("10") or t.startswith("11"):
                if t.startswith("0") and "am" not in t and "pm" not in t:
                    try:
                        hr = int(t.split(":")[0])
                        b = "morning" if hr < 12 else "afternoon"
                    except: b = "morning"
                elif "am" in t: b = "morning"
            if "pm" in t:
                try:
                    hr = int(''.join(filter(str.isdigit, t.split(":")[0])))
                    if hr == 12 or hr < 5: b = "afternoon"
                    elif hr >= 5 and hr < 8: b = "evening"
                    else: b = "night"
                except: b = "afternoon"
            d[b].append(spot)
        days.append(d)
    return {"days": days}

def _attach_user_to_workflow(name, email):
    """Personalize the active workflow (if one exists) with the signed-in user."""
    session_id = session.get('session_id')
    if session_id and session_id in workflows:
        wf = workflows[session_id]
        try:
            wf.user_name = name
            wf.user_email = email
            if hasattr(wf, 'state') and isinstance(wf.state, dict):
                wf.state.setdefault('user_info', {})
                wf.state['user_info']['name'] = name
                wf.state['user_info']['email'] = email
        except Exception as e:
            print(f"[WARN] Could not attach user to workflow: {e}")


def _start_session(identity, is_guest=False):
    session['authenticated'] = True
    session['user_name'] = identity['name']
    session['user_email'] = identity['email']
    session['auth_provider'] = identity.get('provider', 'email')
    session['is_guest'] = bool(is_guest)
    _attach_user_to_workflow(identity['name'], identity['email'])
    return {
        "status": "success",
        "provider": identity.get('provider', 'email'),
        "name": identity['name'],
        "email": identity['email'],
        "is_guest": bool(is_guest),
    }


def _verify_google_id_token(id_token):
    """Verify a Google Identity Services id_token against Google's tokeninfo endpoint."""
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '')
    if not client_id:
        raise AuthError('Google sign-in is not configured in this deployment.', status=503)
    resp = requests.get('https://oauth2.googleapis.com/tokeninfo', params={'id_token': id_token}, timeout=10)
    if resp.status_code != 200:
        raise AuthError('Google could not verify this sign-in attempt. Please try again.', status=502)
    info = resp.json()
    if info.get('aud') != client_id:
        raise AuthError('Google token audience mismatch.', status=401)
    return user_store.find_or_create_oauth_user(info.get('name'), info.get('email'), 'google')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Render the login page or handle login submission (email / guest / OAuth)."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if not data and request.form:
            data = request.form.to_dict()
        provider = (data.get('provider') or 'email').strip().lower()
        try:
            if provider == 'guest':
                identity = {'name': (data.get('name') or '').strip() or 'Guest Traveler',
                            'email': (data.get('email') or '').strip() or 'guest@explorex.local',
                            'provider': 'guest'}
                return jsonify(_start_session(identity, is_guest=True))

            if provider == 'email':
                action = (data.get('action') or 'signin').strip().lower()
                if action in ('signup', 'register', 'create'):
                    if (data.get('password') or '') != (data.get('confirm_password') or ''):
                        raise AuthError('Passwords do not match.', field='confirm_password')
                    identity = user_store.create_user(
                        data.get('name'), data.get('email'), data.get('password'), provider='email')
                    return jsonify(_start_session(identity)), 201
                identity = user_store.verify_credentials(data.get('email'), data.get('password'))
                return jsonify(_start_session(identity))

            if provider == 'google':
                credential = (data.get('credential') or data.get('id_token') or '').strip()
                if not credential:
                    raise AuthError('Google did not return a credential. Try again or use email / guest.', status=400)
                return jsonify(_start_session(_verify_google_id_token(credential)))



            raise AuthError(f'Unsupported sign-in provider: {provider}', status=400)
        except AuthError as e:
            return jsonify({"status": "error", "message": e.message, "field": e.field}), e.status
        except requests.RequestException:
            return jsonify({"status": "error", "message": "Could not reach the sign-in provider. Please try again."}), 502

    if session.get('authenticated'):
        return redirect('/')

    return render_template(
        'login.html',
        google_client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
        apple_client_id=os.environ.get('APPLE_CLIENT_ID', ''),
    )
@app.route('/api/auth/register', methods=['POST'])
def register():
    """Create an email account and sign the new user in."""
    data = request.json or {}
    try:
        if (data.get('password') or '') != (data.get('confirm_password') or ''):
            raise AuthError('Passwords do not match.', field='confirm_password')
        identity = user_store.create_user(
            data.get('name'), data.get('email'), data.get('password'), provider='email')
        return jsonify(_start_session(identity)), 201
    except AuthError as e:
        return jsonify({"status": "error", "message": e.message, "field": e.field}), e.status


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Drop the workflow and wipe the entire session, including the cookie payload."""
    session_id = session.get('session_id')
    if session_id and session_id in workflows:
        del workflows[session_id]
    session.clear()
    response = jsonify({"status": "success", "message": "Signed out"})
    response.delete_cookie(app.config.get('SESSION_COOKIE_NAME', 'session'))
    return response

@app.route('/')
def index():
    """Render the main page"""
    # Always create a new session on page load to prevent stale state
    session_id = session.get('session_id')
    
    # Save auth data before clearing workflow state
    is_auth = session.get('authenticated', False)
    u_name = session.get('user_name')
    u_email = session.get('user_email')
    
    if session_id and session_id not in workflows:
        # Recreate workflow if it was lost from memory but session exists.
        # This is best-effort: if agent initialisation fails (e.g. an
        # incompatible ChromaDB build or a missing credential), the landing
        # page must still render and the workflow is created lazily when the
        # user actually starts planning (see /process). A hard failure here
        # previously returned a 500 on the home page.
        try:
            workflows[session_id] = TravelGraph(user_name=u_name, user_email=u_email)
            print(f"[DEBUG] Restored workflow on page reload: {session_id}")
        except Exception as e:
            safe_print(f"[ERROR] Failed to restore TravelGraph for {session_id}: {str(e)}")
            log_exception()
    
    # Restore auth data
    if is_auth:
        session['authenticated'] = True
        session['user_name'] = u_name
        session['user_email'] = u_email
        
    if not session_id:
        session_id = os.urandom(16).hex()
        session['session_id'] = session_id
        try:
            workflows[session_id] = TravelGraph(user_name=u_name, user_email=u_email)
            print(f"[DEBUG] Created fresh session on page load: {session_id}")
        except Exception as e:
            safe_print(f"[ERROR] Failed to create TravelGraph: {str(e)}")
            log_exception()
    
    # Load popular attractions
    try:
        with open('frontend/data/popular_attractions.json', 'r') as f:
            popular_attractions = json.load(f)
    except FileNotFoundError:
        popular_attractions = []
    
    prune_workflows()

    return render_template(
        'index.html',
        popular_attractions=popular_attractions,
        authenticated=bool(is_auth),
        user_name=u_name or '',
        user_email=u_email or '',
        is_guest=bool(session.get('is_guest', False)),
    )

@app.route('/api/reset', methods=['POST'])
def reset_session():
    """Clear the current session to start a new trip"""
    session_id = session.get('session_id')
    if session_id and session_id in workflows:
        del workflows[session_id]
        print(f"[DEBUG] Reset workflow for session: {session_id}")
    
    # Save auth data
    is_auth = session.get('authenticated', False)
    u_name = session.get('user_name')
    u_email = session.get('user_email')
    is_guest = session.get('is_guest', False)
    
    session.clear()
    
    new_session_id = os.urandom(16).hex()
    session['session_id'] = new_session_id
    # A new trip gets a brand-new TravelGraph instance, so no trip-specific
    # state (destination, selections, budget, email status) can survive it.
    workflows[new_session_id] = TravelGraph(user_name=u_name, user_email=u_email)
    
    # Restore auth data
    if is_auth:
        session['authenticated'] = True
        session['user_name'] = u_name
        session['user_email'] = u_email
        session['is_guest'] = bool(is_guest)

    # Bound the in-process workflow map: sessions are random ids, so a stale
    # entry can never be reused - dropping the oldest keeps memory finite.
    prune_workflows()

    return jsonify({"status": "success", "message": "Session reset", "session_id": new_session_id})

@app.route('/api/state', methods=['GET'])
def get_state():
    """Return the current trip state so the SPA can restore it after a refresh.

    Read-only: never mutates the workflow. Returns trip=None when there is no
    active session/workflow yet (e.g. immediately after New Trip)."""
    session_id = request.args.get('session_id') or session.get('session_id')
    if not session_id or session_id not in workflows:
        return jsonify({"status": "success", "session_id": session_id, "trip": None})

    state = workflows[session_id].get_current_state() or {}
    info = state.get("user_info", {}) or {}

    def _clean(v):
        return v if v is not None else None

    trip = {
        "destination": _clean(info.get("city")),
        "origin": _clean(info.get("origin_city")),
        "days": _clean(info.get("days")),
        "start_date": _clean(info.get("start_date")),
        "budget_level": _clean(info.get("budget")),
        "people": _clean(info.get("people")),
        "kids": _clean(info.get("kids")),
        "interests": _clean(info.get("hobbies")),
        "attractions": state.get("attractions") or [],
        "selected_attractions": state.get("selected_attractions") or [],
        "accommodations": state.get("accommodations") or [],
        "selected_accommodation": state.get("selected_accommodation"),
        "restaurants": state.get("restaurants") or [],
        "weather": state.get("weather_forecast") or [],
        "itinerary": normalize_itinerary_for_client(state.get("itinerary") or []),
        "budget": state.get("budget") or {},
        # ONE canonical breakdown, identical to what the budget page and the
        # email use, so a page refresh can never show a different total.
        "budget_normalized": state.get("budget_normalized") or {},
        "transit_options": state.get("transit_options"),
        "travel_tips": state.get("travel_tips"),
        "unscheduled_attractions": state.get("unscheduled_attractions") or [],
        "provider_status": state.get("provider_status") or {},
        "destination_scope": (info.get("destination_scope") or state.get("destination_scope")),
        "discovery_status": state.get("discovery_status"),
        "current_step": state.get("current_step"),
        "email_sent": bool(state.get("email_sent")),
    }
    return jsonify({"status": "success", "session_id": session_id, "trip": trip})


@app.route('/api/assistant', methods=['GET'])
def get_assistant_context():
    """Read the persisted trip state for the floating AI Travel Assistant.

    The assistant must never re-run the LLM pipeline on navigation: every value
    here is already in the session's workflow state. When there is no trip the
    endpoint still succeeds with an empty context so the panel can show its
    idle state instead of erroring.
    """
    session_id = session.get('session_id')
    if not session_id or session_id not in workflows:
        return jsonify({
            "status": "success",
            "session_id": session_id,
            "context": {"step": None, "trip": None, "has_trip": False,
                        "conversation": [], "missing_fields": []},
        })
    try:
        context = workflows[session_id].get_assistant_context()
        history = workflows[session_id].get_conversation_history()
        context["conversation"] = history
        context["has_trip"] = bool((context.get("trip") or {}).get("destination"))
        return jsonify({"status": "success", "session_id": session_id, "context": context})
    except Exception as e:
        print(f"[ERROR] assistant context unavailable: {type(e).__name__}: {e}")
        return jsonify({"status": "error", "message": "Assistant context is temporarily unavailable."}), 500

@app.route('/api/process', methods=['POST'])
def process():
    """Process a step in the travel planning workflow"""
    try:
        data = request.json
        session_id = session.get('session_id')

        if not session_id:
            session_id = os.urandom(16).hex()
            session['session_id'] = session_id
            workflows[session_id] = TravelGraph(user_name=session.get('user_name'), user_email=session.get('user_email'))
            print(f"[DEBUG] Created new session: {session_id}")
        else:
            print(f"[DEBUG] Using existing session: {session_id}")
        if session_id not in workflows:
            workflows[session_id] = TravelGraph(user_name=session.get('user_name'), user_email=session.get('user_email'))
            print(f"[DEBUG] Recreated workflow for session: {session_id}")
        workflow = workflows[session_id]
        # Keep only critical step information for logging
        print(f"[DEBUG] Processing step: {data.get('step', 'chat')} for session: {session_id}")
        # Process the current step
        step_name = data.get('step', 'chat')
        print(f"[DEBUG] About to call process_step with step_name={step_name}")
        result = workflow.process_step(step_name, **data)
        print(f"[DEBUG] process_step returned successfully")
        # Add the current state to the result
        result['state'] = workflow.get_current_state()

        # Strip out non-serializable stream generator; collect text if needed
        if 'stream' in result:
            stream_gen = result.pop('stream')
            if stream_gen is not None and 'response' not in result:
                try:
                    collected = ""
                    for chunk in stream_gen:
                        c = chunk.content if hasattr(chunk, 'content') else chunk
                        if isinstance(c, list):
                            for part in c:
                                if isinstance(part, dict) and 'text' in part:
                                    collected += part['text']
                        elif isinstance(c, str):
                            collected += c
                    result['response'] = collected
                except Exception as se:
                    print(f"[WARN] Could not collect stream text: {se}")

        return jsonify(result)
    except Exception as e:
        # The exception text is logged with a correlation reference and never
        # returned verbatim: it can contain provider responses or PII.
        return error_response(
            e, "api/process",
            fallback="That step could not be completed. Your progress so far is saved - please try again.",
            extra={"step": (request.json or {}).get('step', 'chat') if request.is_json else None},
        )

@app.route('/api/destinations', methods=['POST'])
def recommend_destinations():
    """Recommend global destinations based on user interests."""
    try:
        data = request.json
        interests = data.get('interests', [])
        weight = data.get('weight', 80)
        
        session_id = session.get('session_id')
        
        # Simple cache key
        cache_key = f"{','.join(sorted(interests))}_{weight}"
        
        if not session_id or session_id not in workflows:
            # Fallback for explore tab when no session exists yet
            from agents.recommend_agent import RecommendAgent
            agent = RecommendAgent()
            destinations = agent.recommend_destinations(interests, weight)
        else:
            workflow = workflows[session_id]
            if 'destinations_cache' not in workflow.state:
                workflow.state['destinations_cache'] = {}
                
            if cache_key in workflow.state['destinations_cache']:
                return jsonify({"status": "success", "destinations": workflow.state['destinations_cache'][cache_key]})
                
            if 'user_info' not in workflow.state:
                workflow.state['user_info'] = {}
            workflow.state['user_info']['interests'] = interests
            agent = workflow.recommend_agent
            destinations = agent.recommend_destinations(interests, weight)
            
            # Cache the result
            workflow.state['destinations_cache'][cache_key] = destinations
            
        return jsonify({"status": "success", "destinations": destinations})
    except Exception as e:
        return error_response(e, "api/destinations",
                              fallback="Destination suggestions are unavailable right now. Please try again.")

@app.route('/api/attractions/<city>')
def get_attractions(city):
    """Get attractions for a specific city"""
    session_id = session.get('session_id')

    if not session_id or session_id not in workflows:
        return jsonify({"status": "error", "success": False,
                        "error": "Session not found", "message": "Session not found"}), 404

    workflow = workflows[session_id]
    try:
        attractions = workflow.info_agent.get_attractions(city)
    except Exception as e:
        return error_response(e, "api/attractions",
                              fallback=f"We could not load attractions for {city} right now.")
    return jsonify(attractions)

@app.route('/api/email', methods=['POST'])
def send_email_api():
    """Manually trigger itinerary email delivery.

    Requirement 6 hardening:
      * Recipient is validated on the server (never trust the frontend alone).
      * The itinerary sent is the *server-side* itinerary for the current
        session — a client cannot smuggle in another user's plan by tampering
        with the request body.
      * In-flight + already-sent guards prevent a double-click (or a
        malicious retry loop) from sending the same itinerary twice.
      * Missing SMTP configuration produces an explicit, actionable error
        rather than a silent False.

    Returns a clean JSON contract:
      Success -> {"success": true,  "message": "Itinerary sent successfully"}
      Failure -> {"success": false, "message": "Unable to send itinerary", "error": "..."}
    Never exposes SMTP credentials or stack traces to the frontend.
    """
    data = request.get_json(silent=True) or {}

    # ---- Validate recipient ------------------------------------------------
    email = (data.get('email') or '').strip()
    if not email:
        print("[EMAIL][VALIDATION] /api/email called without an email address.")
        return jsonify({
            "success": False,
            "message": "Unable to send itinerary",
            "error": "Email is required"
        }), 400
    if not is_valid_email(email):
        print(f"[EMAIL][VALIDATION] /api/email rejected malformed address: {email!r}")
        return jsonify({
            "success": False,
            "message": "Unable to send itinerary",
            "error": "Please enter a valid email address"
        }), 400

    # ---- Session / workflow ----------------------------------------------
    session_id = session.get('session_id')
    if not session_id or session_id not in workflows:
        print("[EMAIL] /api/email called with no active session.")
        return jsonify({
            "success": False,
            "message": "Unable to send itinerary",
            "error": "No active session. Please plan a trip first."
        }), 400

    workflow = workflows[session_id]
    state = workflow.get_current_state() or {}
    user_info = state.get("user_info", {}) or {}

    # ---- Authorization: only the owner of the session can send its itinerary.
    # If an authenticated user is signed in, the itinerary is bound to their
    # Flask session id; a body-supplied "itinerary" from a different session
    # therefore cannot be dispatched (defense in depth vs. IDOR).
    signed_in_email = (session.get('user_email') or '').strip().lower()
    if signed_in_email and email.lower() != signed_in_email:
        # Allow sending to the account's own address only. Cross-account
        # delivery would leak another traveller's plan.
        print(f"[EMAIL][AUTH] Rejected: signed-in={signed_in_email} requested={email}")
        return jsonify({
            "success": False,
            "message": "Unable to send itinerary",
            "error": "You can only email your itinerary to the address associated with your account."
        }), 403

    # ---- Duplicate-send prevention ---------------------------------------
    # 1) Refuse if we already delivered this exact itinerary in this session.
    # 2) Refuse if a send is currently in flight (double-click / retry storm).
    already_sent = bool(state.get("email_sent"))
    sent_to = (state.get("email_sent_to") or "").lower()
    in_flight = _EMAIL_INFLIGHT.get(session_id)
    now = time.time()
    if in_flight and (now - in_flight) < 30:
        return jsonify({
            "success": False,
            "message": "Unable to send itinerary",
            "error": "An email is already being sent. Please wait."
        }), 429
    if already_sent and sent_to == email.lower():
        # Idempotent response so the frontend can display a friendly notice
        # without re-attempting SMTP credentials every click.
        return jsonify({
            "success": True,
            "status": "already_sent",
            "message": "This itinerary was already sent to that address."
        }), 200
    _EMAIL_INFLIGHT[session_id] = now

    # ---- Itinerary -------------------------------------------------------
    # Server-side is authoritative. The client body value is only consulted
    # as a fallback for shape/normalisation hints — never as the source of
    # truth (that would let a caller ship an arbitrary itinerary).
    itinerary = state.get("itinerary") or data.get("itinerary")
    normalized_days = normalize_itinerary(itinerary)
    if not normalized_days:
        _EMAIL_INFLIGHT.pop(session_id, None)
        print("[EMAIL][PAYLOAD] /api/email received no usable itinerary.")
        return jsonify({
            "success": False,
            "message": "Unable to send itinerary",
            "error": "Itinerary not generated yet"
        }), 400

    budget = state.get("budget") or state.get("budget_estimate") or data.get("budget") or {}
    notes = (data.get("notes") or "").strip()

    # ---- Trip context for a rich email -----------------------------------
    accommodation = state.get("selected_accommodation")
    accommodation_is_selection = bool(accommodation)
    if not accommodation:
        accs = state.get("accommodations") or []
        accommodation = accs[0] if accs else None

    trip_context = {
        "origin": user_info.get("origin_city") or state.get("origin_city"),
        "days": user_info.get("days") or state.get("days"),
        "people": user_info.get("people") or state.get("people"),
        "kids": user_info.get("kids") or state.get("kids"),
        "start_date": user_info.get("start_date") or state.get("start_date"),
        "end_date": state.get("end_date"),
        "accommodation": accommodation,
        # Tells the email renderer whether this stay was actually chosen by the
        # traveller or is merely the first recommendation (never say "booked").
        "accommodation_is_selection": accommodation_is_selection,
        "food": state.get("restaurants") or [],
        "transport": state.get("transit_options"),
        "tips": state.get("travel_tips") or state.get("tips"),
        "unscheduled": state.get("unscheduled_attractions") or [],
        "rental_decision_source": state.get("rental_decision_source"),
    }

    user_name = user_info.get("name") or "Traveler"
    city = user_info.get("city") or state.get("city") or "your destination"
    days = trip_context["days"] or len(normalized_days)
    confirmation = (
        f"Your {days}-day trip to {city} has been planned, {user_name}! "
        f"Check your itinerary below."
    )

    print(f"[EMAIL] /api/email dispatching to={email} city={city!r} "
          f"itinerary_days={len(normalized_days)}")

    # ---- Explicit credential check so the user sees an actionable message ---
    missing_creds = [k for k in ("MAIL_SERVER", "MAIL_USERNAME")
                     if not (os.environ.get(k) or "").strip()]
    if missing_creds:
        _EMAIL_INFLIGHT.pop(session_id, None)
        print(f"[EMAIL][CONFIG] Missing environment variables: {missing_creds}")
        return jsonify({
            "success": False,
            "message": "Unable to send itinerary",
            "error": ("Email delivery is not configured on this server "
                      f"(missing {', '.join(missing_creds)}). "
                      "Set them in your .env file and restart.")
        }), 503

    # ---- Send ------------------------------------------------------------
    try:
        success = send_trip_email(
            email, user_name, city, itinerary, budget, confirmation,
            notes=notes, trip_context=trip_context
        )
    except Exception as exc:
        # Log detail server-side only; never leak it to the client.
        safe_print(f"[EMAIL][ERROR] Unexpected failure sending to {email}: {exc}")
        log_exception()
        success = False
    finally:
        _EMAIL_INFLIGHT.pop(session_id, None)

    if success:
        # Record the delivery on the workflow state so a page refresh or a
        # duplicate click cannot trigger a second send for the same recipient.
        try:
            workflow.set_state_field("email_sent", True)
            workflow.set_state_field("email_sent_to", email.lower())
        except Exception:
            # Older TravelGraph builds may not expose set_state_field — best-effort.
            try:
                state["email_sent"] = True
                state["email_sent_to"] = email.lower()
            except Exception:
                pass
        print(f"[EMAIL][SUCCESS] Itinerary delivered to {email}.")
        return jsonify({
            "success": True,
            "status": "success",
            "message": "Itinerary sent successfully"
        }), 200

    print(f"[EMAIL][ERROR] Delivery failed for {email}.")
    return jsonify({
        "success": False,
        "message": "Unable to send itinerary",
        "error": "We couldn't send the email right now. Please check the address and try again, or contact support if the problem continues."
    }), 500

@app.route('/api/stream')
def stream():
    """Handle streaming responses"""

    # Priority: URL param first (EventSource doesn't reliably send cookies), then cookie
    session_id = request.args.get('session_id', '').strip() or session.get('session_id')

    if not session_id or session_id not in workflows:
        # Create a new session only if we truly don't have one
        if not session_id:
            session_id = os.urandom(16).hex()
            print(f"[DEBUG] Created new session_id: {session_id}")
        else:
            print(f"[DEBUG] session_id {session_id} not in workflows, recreating")
        session['session_id'] = session_id
        try:
            workflows[session_id] = TravelGraph(user_name=session.get('user_name'), user_email=session.get('user_email'))
            print(f"[DEBUG] Created new TravelGraph for session: {session_id}")
        except Exception as e:
            # The exception text can contain absolute paths or provider detail;
            # log it here and give the client a generic, safe message instead.
            safe_print(f"[ERROR] Failed to create TravelGraph: {type(e).__name__}: {e}")
            log_exception()
            return Response('data: {"type": "error", "error": "Failed to initialize workflow. Please refresh and try again."}', mimetype='text/event-stream')
    else:
        print(f"[DEBUG] Reusing existing session: {session_id}")

    workflow = workflows[session_id]
    print(f"[DEBUG] Streaming step for session: {session_id}")
    # Get parameters from request
    step_name = request.args.get('step', 'chat')
    user_input = request.args.get('user_input', '')
    selected_attraction_ids = request.args.get('selected_attraction_ids')
    if selected_attraction_ids:
        try:
            selected_attraction_ids = json.loads(selected_attraction_ids)
        except json.JSONDecodeError:
            selected_attraction_ids = None
            
    # Check if the user is confirming satisfaction with the recommendation
    satisfaction_message = 'satisfied with your recommendation' in user_input.lower()
    
    if satisfaction_message:
        print(f"[CRITICAL] Detected satisfaction message: '{user_input}'")
        
    # Parse kwargs outside generate to avoid working outside request context
    kwargs = request.args.to_dict()
    if 'selected_attraction_ids' in kwargs:
        try:
            kwargs['selected_attraction_ids'] = json.loads(kwargs['selected_attraction_ids'])
        except json.JSONDecodeError:
            kwargs['selected_attraction_ids'] = None
            
    # Remove keys that are explicitly passed as main args
    kwargs.pop('step', None)
    kwargs.pop('session_id', None)
    kwargs.pop('user_input', None)
    
    def generate():
        try:
            current_step = step_name
            current_user_input = user_input
            current_selected_attraction_ids = selected_attraction_ids

            # Helper: extract plain text from Gemini chunk content (may be str or list of dicts)
            def extract_text(content):
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, dict) and 'text' in part:
                            parts.append(part['text'])
                        elif isinstance(part, str):
                            parts.append(part)
                    return ''.join(parts)
                return str(content) if content else ''

            loop_count = 0
            while True:
                loop_count += 1
                print(f"[DIAGNOSTIC] Stream loop iteration {loop_count} for session {session_id}. current_step='{current_step}'")

                # Emit a real agent-trace event for the step about to run. This
                # reflects the actual multi-agent loop (no fabrication): each
                # iteration corresponds to one process_step call on a real agent.
                agent_label = AGENT_TRACE_LABELS.get(current_step, current_step.replace('_', ' ').title())
                yield f"data: {json.dumps({'type': 'status', 'step': current_step, 'agent': agent_label})}\n\n"

                # Use kwargs initialized outside generate()
                current_kwargs = kwargs.copy()
                # Clear for auto transitions so we don't pass them to the next steps
                if loop_count > 1:
                    current_kwargs.pop('force_continue', None)
                    current_kwargs.pop('selected_attraction_ids', None)
                    
                # Run the step in a daemon worker thread so a slow stage cannot
                # block this generator: the request thread keeps pumping the
                # queue, emits truthful keep-alive 'progress' events while the
                # real agent work continues, and stays interruptible when the
                # client disconnects (state still persists server-side).
                step_queue = queue.Queue()
                step_box = {}

                def _run_step(step=current_step, text_input=current_user_input, step_kwargs=current_kwargs):
                    try:
                        r = workflow.process_step(
                            step,
                            session_id=session_id,
                            user_input=text_input,
                            **step_kwargs
                        )
                        if 'stream' in r and r['stream']:
                            try:
                                for chunk in r['stream']:
                                    if hasattr(chunk, 'content') and chunk.content:
                                        text = extract_text(chunk.content)
                                        if text:
                                            step_queue.put(('chunk', text))
                            except Exception as stream_err:
                                safe_print(f"[ERROR] Exception while consuming stream for step '{step}': {stream_err}")
                                log_exception()
                        step_box['result'] = r
                    except Exception as step_err:
                        safe_print(f"[ERROR] Step '{step}' failed: {step_err}")
                        log_exception()
                        step_box['error'] = step_err
                    finally:
                        step_queue.put(('done', None))

                worker = threading.Thread(target=_run_step, daemon=True, name=f"step-{current_step}")
                worker.start()

                step_started = time.time()
                base_progress_msg = AGENT_PROGRESS_MESSAGES.get(current_step, f"Processing {current_step}")
                while True:
                    try:
                        kind, payload = step_queue.get(timeout=STREAM_KEEPALIVE_SECONDS)
                    except queue.Empty:
                        elapsed = int(time.time() - step_started)
                        progress_msg = f"{base_progress_msg} ({elapsed}s)"
                        yield f"data: {json.dumps({'type': 'progress', 'step': current_step, 'agent': agent_label, 'elapsed': elapsed, 'message': progress_msg})}\n\n"
                        continue
                    if kind == 'done':
                        break
                    if kind == 'chunk':
                        yield f"data: {{\"type\": \"chunk\", \"content\": {json.dumps(payload)} }}\n\n"
                        time.sleep(0.01)

                worker.join(timeout=5)
                if 'error' in step_box:
                    raise step_box['error']
                result = step_box['result']

                # Check the should_rent_car status right after processing
                current_should_rent_car = workflow.get_current_state().get('should_rent_car', False)
                print(f"[DEBUG] After processing step {current_step}, should_rent_car = {current_should_rent_car}")
                
                next_step = result.get('next_step')
                print(f"[DIAGNOSTIC] Step '{current_step}' completed. Returned next_step='{next_step}'")
                
                # Auto-transition logic ON THE BACKEND
                # If the next step is information, retrieval, or recommend (from retrieval), or route (from strategy), loop immediately!
                if next_step in ['information', 'retrieval'] or \
                   (current_step == 'retrieval' and next_step == 'recommend') or \
                   (current_step == 'recommend' and next_step == 'strategy') or \
                   (current_step == 'strategy' and next_step == 'communication') or \
                   (current_step == 'communication' and next_step == 'route'):
                    current_step = next_step
                    if current_step == 'recommend':
                        current_user_input = ""
                    else:
                        current_user_input = "continue"
                    current_selected_attraction_ids = None  # Clear kwargs for auto transitions
                    print(f"[DIAGNOSTIC] Auto-transitioning loop to '{current_step}'")
                    continue
                
                print(f"[DIAGNOSTIC] Breaking out of backend loop. Will send 'complete' for step='{current_step}' with next_step='{next_step}'")
                # If we reach here, we break and yield complete
                break
            
            # Send completion data using the LAST result
            completion_data = {
                'type': 'complete',
                'session_id': session_id,   # Always send back so frontend can reuse it
                'next_step': result.get('next_step'),
                'ui_action': result.get('ui_action'),
                'validation_warning': result.get('validation_warning'),
                'required_count': result.get('required_count'),
                'selected_count': result.get('selected_count'),
                'missing_fields': result.get('missing_fields', []),
                'state': result.get('state'),
                'attractions': result.get('recommended_attractions') or result.get('attractions'),
                'map_data': result.get('map_data'),
                'itinerary': normalize_itinerary_for_client(result.get('itinerary')),
                'budget': result.get('budget'),
                'response': result.get('response'),
                'optimal_route': result.get('optimal_route'),
                'rental_post': result.get('rental_post'),
                'transit_options': result.get('transit_options'),
                'accommodations': result.get('accommodations'),
                'restaurants': result.get('restaurants'),
                'recommended_attractions': result.get('recommended_attractions'),
                'rag_sources': (result.get('rag_sources')
                                or workflow.get_current_state().get('rag_sources')
                                or [])
            }
            
            import math
            def scrub_floats(obj):
                if isinstance(obj, float):
                    if math.isnan(obj) or math.isinf(obj):
                        return None
                    return obj
                elif isinstance(obj, dict):
                    return {k: scrub_floats(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [scrub_floats(i) for i in obj]
                return obj
            
            completion_data = scrub_floats(completion_data)
            
            try:
                completion_json = json.dumps(completion_data)
                yield f"data: {completion_json}\n\n"
            except TypeError as e:
                log_exception(f"JSON serialization failed: {e}")
                for k, v in completion_data.items():
                    try:
                        json.dumps(v)
                    except TypeError as err:
                        print(f"[DIAGNOSTIC] Key '{k}' failed to serialize: {err}")
                # Send a safe error message
                yield f"data: {{\"type\": \"error\", \"error\": \"Serialization Error\"}}\n\n"
            
            # Verify the final decision after sending the completion data
            final_next_step = completion_data.get('next_step')
            print(f"[DEBUG] Final decision: next_step = {final_next_step}, should_rent_car = {workflow.get_current_state().get('should_rent_car', False)}")
            
        except Exception as e:
            ref = log_error("api/stream", e)
            # Same contract as the JSON endpoints: a safe message plus a
            # reference, never the raw exception text.
            safe = client_error_message(
                e, "That step could not be completed. Your progress so far is saved - please try again.")
            yield f"data: {{\"type\": \"error\", \"error\": {json.dumps(safe)}, \"request_ref\": {json.dumps(ref)} }}\n\n"
    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/nearby/<attraction_id>')
def get_nearby_places(attraction_id):
    """Return real eateries around one attraction coordinate.

    ``attraction_id`` is the ``lat,lng`` pair of the selected attraction (the
    same contract as before). The response keeps the existing ``restaurants``
    list and adds verified straight-line distances, coordinates and map links.
    A session is no longer required, so the modal also works for guests.
    """
    try:
        lat_str, lng_str = attraction_id.split(',', 1)
        lat, lng = float(lat_str), float(lng_str)
    except (AttributeError, TypeError, ValueError):
        return jsonify({"error": "Invalid coordinates format. Use 'lat,lng'."}), 400

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return jsonify({"error": "Coordinates are outside the valid latitude/longitude range."}), 400

    try:
        radius = int(request.args.get('radius', 1000))
    except (TypeError, ValueError):
        radius = 1000

    poi_manager = None
    session_id = session.get('session_id')
    if session_id:
        workflow = workflows.get(session_id)
        if workflow is not None:
            info_agent = getattr(workflow, 'info_agent', None)
            poi_manager = getattr(info_agent, 'poi_manager', None)
    if poi_manager is None:
        from services.poi_fallback import get_shared_poi_manager
        poi_manager = get_shared_poi_manager()

    try:
        result = poi_manager.get_nearby_eateries(lat, lng, radius=radius)
        return jsonify(result)
    except Exception as e:
        return error_response(e, "api/nearby",
                              fallback="Nearby places could not be loaded for this stop right now.")

@app.route('/api/attraction-image', methods=['GET'])
def get_attraction_image_endpoint():
    """Resolve one attraction image on demand.

    Query params: ``name`` (required), ``city``, ``country``, ``lat``, ``lng``.
    Returns ``image_url`` from a provider image, a verified Wikimedia source or
    an attraction-specific Pexels photo.  Only when none of those exists does
    the representative chain run (a name-corroborated Commons file, a
    Pexels photo whose own text carries both the place's category and its
    destination, or a generated travel card).  ``source`` reports which stage
    supplied it; ``image_is_representative`` is true when the image is not a
    verified photo of the exact attraction.
    """
    from services.attraction_images import get_image_resolver

    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({'status': 'error', 'error': 'name parameter is required'}), 400

    city = (request.args.get('city') or '').strip()
    country = (request.args.get('country') or '').strip()
    state = (request.args.get('state') or '').strip()

    def _coord(key):
        try:
            value = float(request.args.get(key))
        except (TypeError, ValueError):
            return None
        return value

    poi = {
        'name': name,
        'city': city,
        'state': state,
        'country': country,
        'latitude': _coord('lat'),
        'longitude': _coord('lng'),
    }
    try:
        resolver = get_image_resolver()
        image_url = resolver.resolve(poi, city=city or None, guarantee=True)
        source = None
        if image_url:
            source = poi.get('image_source') or 'verified'
        return jsonify({
            'status': 'success',
            'image_url': image_url,
            'source': source,
            'image_is_representative': bool(poi.get('image_is_representative')),
            'diagnostics': dict(resolver.last_status),
        })
    except Exception as e:
        return error_response(e, "api/attraction-image",
                              fallback="The attraction image could not be resolved right now.")


@app.route('/api/attraction-details', methods=['GET'])
def get_attraction_details_endpoint():
    """
    Return enriched attraction details for a given POI name + coordinates.

    Query params:
        name  (str)  — attraction name, e.g. "Taj Mahal"
        lat   (float) — latitude
        lng   (float) — longitude

    The endpoint:
      1. Looks up the POI in the current session's attraction list (if any).
      2. Calls the Wikipedia-backed attraction_details service.
      3. Returns the merged enriched object — no hallucination.
    """
    from services.attraction_details import get_attraction_details

    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'error': 'name parameter is required'}), 400

    try:
        lat = float(request.args.get('lat', 0) or 0)
        lng = float(request.args.get('lng', 0) or 0)
    except (ValueError, TypeError):
        lat, lng = None, None

    # Try to find the existing POI object from the active session
    existing_data = None
    session_id = session.get('session_id')
    if session_id and session_id in workflows:
        workflow = workflows[session_id]
        state = workflow.get_current_state() if hasattr(workflow, 'get_current_state') else {}
        all_attractions = state.get('recommended_attractions') or state.get('attractions') or []
        # Search by name (case-insensitive) since IDs vary by provider
        for a in all_attractions:
            if a.get('name', '').strip().lower() == name.lower():
                existing_data = a
                break

    try:
        details = get_attraction_details(name=name, lat=lat or None, lng=lng or None, existing_data=existing_data)
        return jsonify(details)
    except Exception as e:
        return error_response(e, "api/attraction-details",
                              fallback=f"Details for {name} are unavailable right now.")


if __name__ == '__main__':
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)

    # ── Deployment-aware startup ───────────────────────────────────────────
    # Everything below is driven by environment variables so the SAME entry
    # point works for local development and for a production host (Render /
    # Railway / Fly.io / a container). Defaults preserve the original local
    # dev behaviour (127.0.0.1:8000, debug on).
    #   FLASK_DEBUG=0            -> turn off the reloader/debugger
    #   HOST=0.0.0.0 PORT=8000   -> bind like a PaaS expects
    #   PRODUCTION=1 (or FLASK_ENV=production)
    #                            -> serve via Waitress (a real WSGI server)
    host = os.environ.get("HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("PORT", "8000"))
    except (TypeError, ValueError):
        port = 8000

    is_production = (
        os.environ.get("PRODUCTION") == "1"
        or os.environ.get("FLASK_ENV") == "production"
    )
    debug = (os.environ.get("FLASK_DEBUG", "1") == "1") and not is_production

    if is_production:
        # Waitress is a battle-tested, pure-Python WSGI server that runs the
        # same on Windows and Linux, so it avoids shipping the Werkzeug
        # development server (which explicitly warns against production use).
        try:
            from waitress import serve
            print(f"[STARTUP] Serving ExploreX with Waitress on http://{host}:{port}")
            serve(app, host=host, port=port, threads=int(os.environ.get("WEB_CONCURRENCY", "8")))
        except ImportError:
            print("[STARTUP] Waitress not installed; falling back to Flask's server "
                  "with debug disabled. Run `pip install waitress` for production.")
            app.run(host=host, port=port, debug=False)
    else:
        app.run(host=host, port=port, debug=debug)
