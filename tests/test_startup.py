"""
Phase 10 (A/B) startup & integration-boundary validation.
=========================================================
Verifies the three things the FINAL SUCCESS CRITERION depends on at the
framework level, without requiring any external network call:

  1. The Flask application object imports cleanly (no crash-at-import).
  2. GET '/' (the landing page) returns HTTP 200 with real HTML.
  3. The RetrievalAgent can initialise against the rebuilt ChromaDB knowledge
     base (the exact call path that previously crashed with
     "no such column: collections.topic").

Each test that touches the live knowledge base is skipped — never faked — when
the dependency is genuinely unavailable, so the deterministic suite stays green
in environments without the rebuilt DB, while still failing loudly if the DB
directory exists but is schema-incompatible.
"""
import os

import pytest


@pytest.fixture(scope="module")
def flask_app():
    from main import app  # module-level Flask app
    app.config.update({"TESTING": True})
    return app


def test_flask_app_imports_and_has_routes(flask_app):
    """Importing main must not raise; the core routes must be registered."""
    rules = {r.rule for r in flask_app.url_map.iter_rules()}
    assert "/" in rules, "Landing route '/' is not registered"
    assert "/api/process" in rules, "Workflow endpoint '/api/process' is not registered"


def test_landing_page_returns_200(flask_app):
    """GET '/' must return 200 with a titled HTML document."""
    client = flask_app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = resp.get_data(as_text=True)
    assert "<title" in body.lower(), "Landing page HTML is missing a <title> element"


def test_retrieval_agent_initialises_against_real_db():
    """
    The critical Phase-2 regression path. Constructs RetrievalAgent with the
    real persist directory; this fails if the DB schema is incompatible.
    Skipped only when the DB has not been built in this environment.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_dir = os.path.join(root, "data", "vector_db")
    if not os.path.isdir(db_dir):
        pytest.skip("Knowledge base not built (data/vector_db missing); run ingestion rebuild")

    from agents.retrieval_agent import RetrievalAgent

    agent = RetrievalAgent(persist_directory=db_dir)
    assert agent.vector_db is not None
    # A real query must return a context string (data present) — this exercises
    # the SQLite schema, embeddings dimension and collection binding together.
    ctx = agent.retrieve_context({"hobbies": "viewpoints photography"}, "Kerala", k=3)
    assert isinstance(ctx, str) and ctx, "retrieve_context returned empty for Kerala"
