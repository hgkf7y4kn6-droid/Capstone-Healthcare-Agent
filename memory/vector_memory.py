"""
Long-term patient memory: a FAISS vector store of patient summaries so the
agent can retrieve relevant context ("this patient has CKD, avoid NSAIDs,
last visit was...") without re-reading full history every turn.

Uses local HuggingFace sentence-transformers embeddings (free, no extra API
key needed) rather than a hosted embeddings API — this keeps the memory
store working regardless of which chat LLM (local or hosted) is
configured, and avoids an extra billable API call on every memory write.
"""

import os
import sys
import threading
import datetime as dt

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VECTOR_STORE_DIR

from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

_EMBEDDINGS = None
_EMBEDDINGS_LOCK = threading.Lock()
_INDEX_CACHE = None
_INDEX_LOCK = threading.Lock()
_INDEX_PATH = os.path.join(VECTOR_STORE_DIR, "patient_memory")


def _get_embeddings():
    global _EMBEDDINGS
    if _EMBEDDINGS is not None:
        return _EMBEDDINGS
    with _EMBEDDINGS_LOCK:
        if _EMBEDDINGS is not None:
            return _EMBEDDINGS
        _EMBEDDINGS = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        return _EMBEDDINGS


def _load_or_create_index() -> FAISS:
    """Cached in-process: FAISS.load_local() deserializes the whole index
    from disk, which is wasted work if we already have it in memory from
    an earlier call in this process (same reasoning as _get_embeddings()
    caching the embeddings model). Safe for this single-process app; if
    this ever runs as multiple worker processes sharing one index file,
    this cache would need external invalidation. Double-checked locking
    for the same reason as agents/llm.py's pipeline cache: concurrent
    requests on separate threads could otherwise both pay the full
    load-from-disk cost at once."""
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    with _INDEX_LOCK:
        if _INDEX_CACHE is not None:
            return _INDEX_CACHE
        embeddings = _get_embeddings()
        if os.path.exists(_INDEX_PATH):
            _INDEX_CACHE = FAISS.load_local(_INDEX_PATH, embeddings, allow_dangerous_deserialization=True)
        else:
            # bootstrap an empty index with one throwaway doc, then clear it
            _INDEX_CACHE = FAISS.from_documents(
                [Document(page_content="bootstrap", metadata={"bootstrap": True})], embeddings
            )
        return _INDEX_CACHE


def add_patient_summary(patient_id: int, patient_name: str, summary_text: str, tag: str = "summary") -> None:
    """Store/update a patient summary snippet in long-term memory."""
    store = _load_or_create_index()
    doc = Document(
        page_content=summary_text,
        metadata={
            "patient_id": patient_id,
            "patient_name": patient_name,
            "tag": tag,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    )
    store.add_documents([doc])
    store.save_local(_INDEX_PATH)


def retrieve_relevant_context(query: str, patient_id: int = None, k: int = 4) -> list[dict]:
    """Retrieve the k most relevant memory snippets for a query, optionally
    filtered to a specific patient_id."""
    if not os.path.exists(_INDEX_PATH):
        return []
    store = _load_or_create_index()
    results = store.similarity_search(query, k=k * 3 if patient_id else k)
    out = []
    for doc in results:
        if doc.metadata.get("bootstrap"):
            continue
        if patient_id is not None and doc.metadata.get("patient_id") != patient_id:
            continue
        out.append({"content": doc.page_content, "metadata": doc.metadata})
        if len(out) >= k:
            break
    return out
