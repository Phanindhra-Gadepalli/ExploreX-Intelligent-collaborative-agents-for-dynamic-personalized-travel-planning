"""Offline HTTP-contract smoke check for the stabilized ExploreX backend.

Drives the real Flask routes with Flask's test_client (no port, no live agent
run) and prints the status code + payload shape for every endpoint the SPA
touches, so the error/API contract of Phase 9 is verified end to end.
"""
import io
import json
import sys
import warnings

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import main  # noqa: E402

client = main.app.test_client()


def body(resp):
    return json.loads(resp.get_data(as_text=True))


def show(label, resp, keys=None):
    payload = None
    try:
        payload = body(resp)
    except Exception:
        pass
    detail = ""
    if isinstance(payload, dict):
        picked = keys or list(payload.keys())
        detail = {k: payload.get(k) for k in picked if k in payload}
    print(f"{label:34s} -> {resp.status_code} {str(detail)[:190]}")


show("GET /", client.get("/"))

with client.session_transaction() as s:
    s["session_id"] = "smoke-session"

show("GET /api/state", client.get("/api/state"), ["status", "session_id"])
show("GET /api/assistant", client.get("/api/assistant"), ["status"])
show("GET /api/nearby/bad-coords", client.get("/api/nearby/notacoord"),
     ["status", "error", "request_ref"])
show("GET /api/attraction-details?name=", client.get("/api/attraction-details?name="),
     ["error"])
show("GET /api/state (no session)", client.get("/api/state"), ["status"])
show("POST /api/email (no session)",
     client.post("/api/email", json={"email": "a@b.co"}), ["success", "message"])

# A workflow whose step explodes must not leak internals to the browser.
from unittest.mock import MagicMock  # noqa: E402

boom = MagicMock()
boom.process_step.side_effect = RuntimeError("Gemini 403 for key sk-SMOKETESTSECRET")
main.workflows["smoke-leak"] = boom
with client.session_transaction() as s:
    s["session_id"] = "smoke-leak"
resp = client.post("/api/process", json={"step": "chat", "user_input": "hi"})
text = resp.get_data(as_text=True)
print(f"POST /api/process (boom)         -> {resp.status_code} "
      f"leaked_secret={'sk-SMOKETESTSECRET' in text} ref={body(resp).get('request_ref')}")
main.workflows.pop("smoke-leak", None)

print("[SMOKE] done")
