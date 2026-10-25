"""
Phase 2 mandated ChromaDB validation test.
=========================================
Proves the pinned ChromaDB + langchain-chroma + embedding-model stack works
end-to-end *without* touching the real `data/vector_db` knowledge base. We
always point Chroma at a per-test temporary folder (pytest `tmp_path`) so the
test is repeatable and non-destructive, exactly as required:

  "Add a repeatable validation test that initialises ChromaDB, creates or opens
   the intended collection, adds a test document, queries it, and verifies
   retrieval. Use a temporary database for destructive test operations."

This also reproduces/guards against the schema-mismatch class of failure
("no such column: collections.topic") because the DB here is created fresh by
the *installed* ChromaDB version, so it is guaranteed self-compatible.
"""
import os

import pytest

from agents.retrieval_agent import RetrievalAgent


COLLECTION = "travel_knowledge_validation"


@pytest.fixture(scope="module")
def embeddings():
    """Reuse the module-level embedding singleton (all-MiniLM-L6-v2)."""
    return RetrievalAgent._get_embeddings()


def test_embedding_model_produces_expected_dimensionality(embeddings):
    """The knowledge base was built with 384-dim vectors; verify the model."""
    vec = embeddings.embed_query("Backwaters of Kerala houseboat Munnar tea")
    assert isinstance(vec, list)
    assert len(vec) == 384, f"Expected 384 dims, got {len(vec)}"


def test_chromadb_roundtrip_in_temporary_database(embeddings, tmp_path):
    """Init -> create collection -> add -> query -> verify retrieval."""
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from chromadb.config import Settings

    persist_dir = str(tmp_path / "vector_db_tmp")

    db = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=persist_dir,
        client_settings=Settings(anonymized_telemetry=False),
    )

    docs = [
        Document(
            page_content="Kerala backwaters are a network of brackish lagoons and lakes "
            "parallel to the Arabian Sea coast, famous for houseboat cruises in Alleppey.",
            metadata={"source": "kerala", "region": "south_india"},
        ),
        Document(
            page_content="Munnar is a hill station in Kerala known for rolling tea "
            "plantations, cool climate and viewpoints such as Top Station.",
            metadata={"source": "kerala", "region": "south_india"},
        ),
        Document(
            page_content="The Eiffel Tower is a wrought-iron lattice tower in Paris, France.",
            metadata={"source": "paris", "region": "europe"},
        ),
    ]
    ids = db.add_documents(docs)
    assert ids and len(ids) == 3, "Expected three document ids to be created"

    # Persist and re-open to confirm on-disk durability with the pinned version.
    del db
    reopened = Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=persist_dir,
        client_settings=Settings(anonymized_telemetry=False),
    )

    results = reopened.similarity_search("Kerala houseboat backwater cruise", k=2)
    assert len(results) >= 1, "Similarity search returned no documents"
    joined = " ".join(r.page_content.lower() for r in results)
    assert "kerala" in joined or "backwater" in joined, (
        "Expected Kerala/backwater document to be retrieved, got: " + joined
    )


def test_retrieval_agent_against_real_knowledge_base_is_not_required():
    """
    Sanity guard: the real DB must exist and be schema-compatible for the app
    to run, but this test only asserts the *directory* is present so a missing
    knowledge base is caught early. Full agent init is exercised in test_startup.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_dir = os.path.join(root, "data", "vector_db")
    # data/vector_db is created by the ingestion rebuild step.
    assert os.path.isdir(db_dir), f"Missing knowledge base directory: {db_dir}"
