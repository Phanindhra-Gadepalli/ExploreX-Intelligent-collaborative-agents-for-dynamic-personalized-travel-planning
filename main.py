import sys
import io
# Fix Windows console encoding - prevents UnicodeEncodeError from non-ASCII Gemini responses
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Definitive fix for chromadb posthog telemetry errors and SQLite threading issues
from unittest.mock import MagicMock
sys.modules['posthog'] = MagicMock()

from flask import Flask, render_template, request, jsonify, session, send_from_directory, send_file, Response, redirect
from flask_session import Session
import os
import json
from dotenv import load_dotenv
from workflows.travel_graph import TravelGraph
import requests
import time
import warnings
from services.email_service import send_trip_email
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
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

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


@app.route('/test-image')
def test_image():
    return send_file('frontend/static/images/background.jpg', mimetype='image/jpeg')

@app.route('/static/images/<path:filename>')
def serve_image(filename):
    return send_from_directory('frontend/static/images', filename)



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

            if provider == 'apple':
                raise AuthError(
                    'Apple sign-in requires APPLE_CLIENT_ID and a registered Return URL on this host. '
                    'Use email or continue as Guest.',
                    status=503,
                )

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
        # Recreate workflow if it was lost from memory but session exists
        workflows[session_id] = TravelGraph(user_name=u_name, user_email=u_email)
        print(f"[DEBUG] Restored workflow on page reload: {session_id}")
    
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
            print(f"[ERROR] Failed to create TravelGraph: {str(e)}")
            import traceback
            traceback.print_exc()
    
    # Load popular attractions
    try:
        with open('frontend/data/popular_attractions.json', 'r') as f:
            popular_attractions = json.load(f)
    except FileNotFoundError:
        popular_attractions = []
    
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
    
    # Restore auth data
    if is_auth:
        session['authenticated'] = True
        session['user_name'] = u_name
        session['user_email'] = u_email
        session['is_guest'] = bool(is_guest)
        
    return jsonify({"status": "success", "message": "Session reset"})

@app.route('/api/state', methods=['GET'])
def get_state():
    """Return the current trip state so the SPA can restore it after a refresh.

    Read-only: never mutates the workflow. Returns trip=None when there is no
    active session/workflow yet (e.g. immediately after New Trip)."""
    session_id = session.get('session_id')
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
        "weather": state.get("weather_forecast") or [],
        "itinerary": state.get("itinerary") or [],
        "budget": state.get("budget") or {},
        "transit_options": state.get("transit_options"),
    }
    return jsonify({"status": "success", "session_id": session_id, "trip": trip})

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
        import traceback
        print(f"[ERROR] in process route: {str(e)}")
        print(f"[ERROR] Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/attractions/<city>')
def get_attractions(city):
    """Get attractions for a specific city"""
    session_id = session.get('session_id')
    
    if not session_id or session_id not in workflows:
        return jsonify({"error": "Session not found"}), 404
    
    workflow = workflows[session_id]
    info_agent = workflow.info_agent
    
    attractions = info_agent.get_attractions(city)
    return jsonify(attractions)

@app.route('/api/email', methods=['POST'])
def send_email_api():
    """Manually trigger itinerary email delivery"""
    try:
        data = request.json
        email = data.get('email', '').strip()
        if not email:
            return jsonify({"status": "error", "message": "Email is required"}), 400

        if session.get('is_guest'):
            return jsonify({
                "status": "error",
                "message": "Email delivery is available to signed-in accounts only. Create a free account or sign in to send your itinerary by email — everything else works in guest mode."
            }), 403

        session_id = session.get('session_id')
        if not session_id or session_id not in workflows:
            return jsonify({"status": "error", "message": "No active session"}), 400
            
        workflow = workflows[session_id]
        state = workflow.get_current_state()
        
        itinerary = state.get("itinerary")
        budget = state.get("budget") or state.get("budget_estimate") or {}
        
        if not itinerary:
            return jsonify({"status": "error", "message": "Itinerary not generated yet"}), 400
            
        from services.email_service import send_trip_email
        user_name = state.get("user_info", {}).get("name", "Traveler")
        city = state.get("user_info", {}).get("city", "your destination")
        days = state.get("user_info", {}).get("days", "?")
        confirmation = f"Your {days}-day trip to {city} has been planned, {user_name}! Check your itinerary below."
        
        success = send_trip_email(email, user_name, city, itinerary, budget, confirmation)
        if success:
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": "Failed to send email. Check configuration."}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stream')
def stream():
    """Handle streaming responses"""
    print(f"[DEBUG] /api/stream endpoint called")

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
            print(f"[ERROR] Failed to create TravelGraph: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(f"data: {{\"type\": \"error\", \"error\": \"Failed to initialize workflow: {str(e)}\"}}", mimetype='text/event-stream')
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
            
            loop_count = 0
            while True:
                loop_count += 1
                print(f"[DIAGNOSTIC] Stream loop iteration {loop_count} for session {session_id}. current_step='{current_step}'")
                
                # Use kwargs initialized outside generate()
                current_kwargs = kwargs.copy()
                # Clear for auto transitions so we don't pass them to the next steps
                if loop_count > 1:
                    current_kwargs.pop('force_continue', None)
                    current_kwargs.pop('selected_attraction_ids', None)
                    
                # Process the step - pass session_id so correct state is reused
                result = workflow.process_step(
                    current_step,
                    session_id=session_id,
                    user_input=current_user_input,
                    **current_kwargs
                )
                
                # Check the should_rent_car status right after processing
                current_should_rent_car = workflow.get_current_state().get('should_rent_car', False)
                print(f"[DEBUG] After processing step {current_step}, should_rent_car = {current_should_rent_car}")
                
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

                # Handle streaming response
                if 'stream' in result and result['stream']:
                    print(f"[DEBUG] Starting to consume stream for step '{current_step}'")
                    try:
                        for chunk in result['stream']:
                            if hasattr(chunk, 'content') and chunk.content:
                                text = extract_text(chunk.content)
                                if text:
                                    yield f"data: {{\"type\": \"chunk\", \"content\": {json.dumps(text)} }}\n\n"
                                    time.sleep(0.01)
                    except Exception as stream_err:
                        import traceback
                        print(f"[ERROR] Exception while consuming stream for step '{current_step}': {stream_err}")
                        traceback.print_exc()
                    print(f"[DEBUG] Stream consumption complete for step '{current_step}'")
                
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
                'validation_warning': result.get('validation_warning'),
                'required_count': result.get('required_count'),
                'selected_count': result.get('selected_count'),
                'missing_fields': result.get('missing_fields', []),
                'state': result.get('state'),
                'attractions': result.get('recommended_attractions') or result.get('attractions'),
                'map_data': result.get('map_data'),
                'itinerary': result.get('itinerary'),
                'budget': result.get('budget'),
                'response': result.get('response'),
                'optimal_route': result.get('optimal_route'),
                'rental_post': result.get('rental_post'),
                'transit_options': result.get('transit_options'),
                'accommodations': result.get('accommodations'),
                'restaurants': result.get('restaurants'),
                'recommended_attractions': result.get('recommended_attractions')
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
                import traceback
                traceback.print_exc()
                print(f"[DIAGNOSTIC] JSON serialization failed! Error: {e}")
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
            print(f"[ERROR] in stream route: {str(e)}")
            yield f"data: {{\"type\": \"error\", \"error\": {json.dumps(str(e))} }}\n\n"
    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/nearby/<attraction_id>')
def get_nearby_places(attraction_id):
    """Get nearby restaurants and street information for an attraction"""
    session_id = session.get('session_id')
    
    if not session_id or session_id not in workflows:
        return jsonify({"error": "Session not found"}), 404
    
    workflow = workflows[session_id]
    info_agent = workflow.info_agent
    
    # Parse coordinates from attraction_id
    try:
        lat_str, lng_str = attraction_id.split(',')
        lat, lng = float(lat_str), float(lng_str)
    except Exception:
        return jsonify({"error": "Invalid coordinates format. Use 'lat,lng'."}), 400
    
    try:
        result = info_agent.search_nearby_places(lat, lng)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Failed to get nearby places: {str(e)}"}), 500
    
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
        import traceback
        print(f"[ERROR] get_attraction_details_endpoint: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Create a sample attractions.json file if it doesn't exist
    if not os.path.exists('data/attractions.json'):
        sample_data = {
            "Delhi": [
                {
                    "id": "qutub_minar",
                    "name": "Qutub Minar",
                    "category": "landmark",
                    "location": {"lat": 28.5245, "lng": 77.1855},
                    "estimated_duration": 3,
                    "price_level": 2
                },
                {
                    "id": "red_fort",
                    "name": "Red Fort",
                    "category": "landmark",
                    "location": {"lat": 28.6562, "lng": 77.2410},
                    "estimated_duration": 3,
                    "price_level": 2
                }
            ],
            "Jaipur": [
                {
                    "id": "hawa_mahal",
                    "name": "Hawa Mahal",
                    "category": "landmark",
                    "location": {"lat": 26.9239, "lng": 75.8267},
                    "estimated_duration": 2,
                    "price_level": 1
                },
                {
                    "id": "amber_fort",
                    "name": "Amber Fort",
                    "category": "landmark",
                    "location": {"lat": 26.9855, "lng": 75.8513},
                    "estimated_duration": 4,
                    "price_level": 2
                }
            ]
        }
        
        with open('data/attractions.json', 'w') as f:
            json.dump(sample_data, f)
    
    # Run the app
    app.run(host="127.0.0.1", port=8000, debug=True)
