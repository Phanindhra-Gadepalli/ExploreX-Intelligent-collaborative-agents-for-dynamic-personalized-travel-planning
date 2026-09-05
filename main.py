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

from flask import Flask, render_template, request, jsonify, session, send_from_directory, send_file, Response
from flask_session import Session
import os
import json
from dotenv import load_dotenv
from workflows.travel_graph import TravelGraph
import requests
import time

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

# Clean up stale sessions on startup to prevent persistence bugs across restarts
import shutil
session_dir = app.config.get('SESSION_FILE_DIR', 'flask_session')
if os.path.exists(session_dir):
    try:
        shutil.rmtree(session_dir)
        print(f"[INFO] Cleared persistent session directory on startup: {session_dir}")
    except Exception as e:
        print(f"[WARN] Failed to clear session directory: {e}")

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

@app.route('/api/reset', methods=['POST'])
def reset_session():
    """Fully reset the user's session and workflow state"""
    session_id = session.get('session_id')
    if session_id and session_id in workflows:
        del workflows[session_id]
        print(f"[DEBUG] Removed workflow for session: {session_id}")
    session.clear() # Clear all Flask session data (including cookies and filesystem)
    return jsonify({"status": "success", "message": "Session reset completely."})

@app.route('/')
def index():
    """Render the main page"""
    # Always create a new session on page load to prevent stale state
    session_id = session.get('session_id')
    if session_id and session_id in workflows:
        del workflows[session_id]
        print(f"[DEBUG] Removed old workflow on page reload: {session_id}")
    
    session.clear()
    session_id = os.urandom(16).hex()
    session['session_id'] = session_id
    
    try:
        workflows[session_id] = TravelGraph()
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
    
    return render_template('index.html', popular_attractions=popular_attractions)

@app.route('/api/process', methods=['POST'])
def process():
    """Process a step in the travel planning workflow"""
    try:
        data = request.json
        session_id = session.get('session_id')

        if not session_id:
            session_id = os.urandom(16).hex()
            session['session_id'] = session_id
            workflows[session_id] = TravelGraph()
            print(f"[DEBUG] Created new session: {session_id}")
        else:
            print(f"[DEBUG] Using existing session: {session_id}")
        if session_id not in workflows:
            workflows[session_id] = TravelGraph()
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
            workflows[session_id] = TravelGraph()
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
                print(f"[CRITICAL] After processing step {current_step}, should_rent_car = {current_should_rent_car}")
                
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
            print(f"[CRITICAL] Final decision: next_step = {final_next_step}, should_rent_car = {workflow.get_current_state().get('should_rent_car', False)}")
            
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
