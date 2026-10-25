"""
Diagnostic: run just the CHAT step in-process to surface the real exception.
Usage:  .venv311\Scripts\python.exe diag_chat_step.py
"""
import sys, io, traceback, json, os

# mirror main.py encoding fix
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# silence chromadb telemetry
from unittest.mock import MagicMock
sys.modules['posthog'] = MagicMock()
os.environ["ANONYMIZED_TELEMETRY"] = "False"

from dotenv import load_dotenv
load_dotenv()

print("=== DIAGNOSTIC: importing TravelGraph ===")
try:
    from workflows.travel_graph import TravelGraph
    print("TravelGraph imported OK")
except Exception:
    traceback.print_exc()
    sys.exit(1)

print("\n=== DIAGNOSTIC: instantiating TravelGraph ===")
try:
    wf = TravelGraph(user_name="Phani", user_email="phanindhra3406@gmail.com")
    print("TravelGraph instantiated OK")
except Exception:
    traceback.print_exc()
    sys.exit(1)

# Hyderabad→Darjeeling, 10d, 5 ppl, 0 kids, photography/waterfalls, ₹80k strict
USER_MSG = (
    "Hi, I want to travel from Hyderabad to Darjeeling for 10 days "
    "with 5 people, 0 kids, I am in good health, "
    "I enjoy photography, waterfalls, and viewpoints, "
    "budget is strictly ₹80,000, start date is flexible"
)

print(f"\n=== DIAGNOSTIC: running chat step with message ===")
print(f"  MSG: {USER_MSG!r}")
try:
    sid = "diag-session-001"
    result = wf.process_step("chat", session_id=sid, user_input=USER_MSG)
    print(f"\n--- RESULT keys: {list(result.keys())} ---")
    print(f"  next_step    : {result.get('next_step')}")
    print(f"  missing_fields: {result.get('missing_fields')}")
    state = result.get("state", {})
    ui = state.get("user_info", {})
    print(f"  user_info    : {json.dumps(ui, ensure_ascii=False, indent=2)}")

    # Consume stream if any (to surface streaming errors)
    stream = result.get("stream")
    if stream:
        print("\n  (consuming stream...)")
        for chunk in stream:
            text = chunk.content if hasattr(chunk, 'content') else str(chunk)
            print("  CHUNK: " + repr(text)[:120])
except Exception:
    print("\n!!! EXCEPTION in chat step !!!")
    traceback.print_exc()
