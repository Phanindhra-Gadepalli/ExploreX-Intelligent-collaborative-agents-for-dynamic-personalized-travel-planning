"""
Pytest configuration for the ExploreX test suite.

Ensures the repository root is importable (agents/, services/, workflows/,
main.py live at the top level) regardless of where pytest is invoked, and that
the local `.env` credentials are loaded so any test touching a Gemini/API client
constructs cleanly. Tests that need live network are marked and skipped when the
external dependency is unavailable, so the deterministic suite stays green.
"""
import os
import sys

# Make the project root importable.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass

# Keep startup logs readable and silence the cosmetic langchain-google-genai
# array/title converter warnings during tests.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# The suite must stay fully offline and deterministic. Attraction-image
# enrichment is exercised explicitly by tests that mock MediaWiki and enable
# lookup locally; everywhere else it stays disabled instead of hitting the
# network for every fixture POI.
os.environ.setdefault("ATTRACTION_IMAGE_LOOKUP", "0")
# Pexels is a live fallback.  Tests that exercise it set this to "1" and mock
# the HTTP session; the rest of the suite must never make stock-photo calls.
os.environ.setdefault("ATTRACTION_IMAGE_PEXELS", "0")
