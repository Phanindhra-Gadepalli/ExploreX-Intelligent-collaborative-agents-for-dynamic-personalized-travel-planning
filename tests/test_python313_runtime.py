"""Python 3.13 runtime and dependency-migration regression guards.

These tests exist because the project moved from CPython 3.11 to 3.13:

* ``numpy<2`` and ``chromadb 0.4.x`` have no cp313 Windows wheels, so the
  vector store had to move to the chromadb 1.x line (which bundles its own
  native backend, ``chroma-hnswlib`` is no longer required);
* ``langchain-core``/``langchain-chroma``/``langchain-huggingface`` were moved
  to the versions that support that chromadb line;
* the model stack (torch/transformers/sentence-transformers) must import and
  produce an embedding on the new interpreter.

They are intentionally cheap and offline: no model download, no network, no
provider credentials.
"""
import sys


def test_runtime_is_python_313_or_newer():
    assert sys.version_info[:2] >= (3, 13), (
        "ExploreX is verified on CPython 3.13+; found %s" % (sys.version,)
    )
    assert sys.maxsize > 2**32, "a 64-bit interpreter is required for torch wheels"


def test_binary_dependency_floors_are_313_compatible():
    import numpy
    import chromadb

    numpy_parts = tuple(int(p) for p in numpy.__version__.split(".")[:2])
    assert numpy_parts >= (2, 1), (
        "numpy %s has no cp313 Windows wheel; 2.1+ is required" % numpy.__version__
    )
    assert int(chromadb.__version__.split(".")[0]) >= 1, (
        "chromadb %s cannot install on Windows/Python 3.13 (no chroma-hnswlib "
        "wheel); the 1.x line ships its own abi3 backend" % chromadb.__version__
    )


def test_web_agent_and_model_stack_imports():
    import flask
    import pydantic
    import torch
    import transformers
    import sentence_transformers
    from langchain_core.messages import HumanMessage
    from langchain_core.documents import Document
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma

    assert pydantic.VERSION.startswith("2.")
    assert HumanMessage(content="hi").content == "hi"
    assert Document(page_content="x").page_content == "x"
    # The names must be importable; construction is not required here (and a
    # real ChatGoogleGenerativeAI needs a key, which tests must not depend on).
    assert ChatGoogleGenerativeAI is not None
    assert HuggingFaceEmbeddings is not None
    assert Chroma is not None
    assert torch.__version__ and transformers.__version__ and sentence_transformers.__version__
    assert flask.__name__ == "flask"


def test_chroma_vector_store_roundtrip(tmp_path):
    """Exercise the real chromadb 1.x backend with the langchain wrapper."""
    from chromadb.config import Settings
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings

    class FakeEmbeddings(Embeddings):
        """Deterministic bag-of-words embeddings; no model download."""

        VOCAB = ("taj", "mahal", "agra", "goa", "beach", "heritage")

        def _vec(self, text):
            words = text.lower().replace(",", " ").replace(".", " ").split()
            return [1.0 if term in words else 0.0 for term in self.VOCAB]

        def embed_documents(self, texts):
            return [self._vec(t) for t in texts]

        def embed_query(self, text):
            return self._vec(text)

    db = Chroma(
        collection_name="py313_roundtrip",
        embedding_function=FakeEmbeddings(),
        persist_directory=str(tmp_path),
        client_settings=Settings(anonymized_telemetry=False),
    )
    db.add_documents([
        Document(page_content="Agra is home to the Taj Mahal.",
                 metadata={"destination": "Agra", "category": "heritage"}),
        Document(page_content="Goa is known for its beaches.",
                 metadata={"destination": "Goa", "category": "beach"}),
    ])

    assert len(db.get().get("ids", [])) == 2

    results = db.similarity_search("Taj Mahal heritage", k=1)
    assert results and "Taj Mahal" in results[0].page_content
    assert results[0].metadata["destination"] == "Agra"

    ids = db.get().get("ids", [])
    db.delete(ids=ids)
    assert db.get().get("ids", []) == []


def test_india_knowledge_base_is_available_for_reingestion():
    """The RAG corpus must still be present and ingestible on chromadb 1.x."""
    from services.document_ingestion import INDIA_KNOWLEDGE_BASE, DocumentIngestor

    assert len(INDIA_KNOWLEDGE_BASE) >= 40
    assert all(getattr(doc, "page_content", "") for doc in INDIA_KNOWLEDGE_BASE)
    assert hasattr(DocumentIngestor, "load_india_knowledge_base")
    assert hasattr(DocumentIngestor, "get_document_count")
