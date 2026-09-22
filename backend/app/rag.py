"""RAG pipeline (Phase 4): extract -> chunk -> embed -> store -> retrieve.

Permission filtering happens INSIDE vectorstore.search() (owner pre-filter
before scoring) and document_ids are verified against ownership BEFORE any
retrieval. Vectors of unauthorized docs are never scored, never injected.
Audit carries metadata only (counts, ids, scores) — never chunk text.
"""
from __future__ import annotations

import os

from . import audit_log
from . import config as cfg
from . import docs_store as store
from .chunking import chunk_segments
from .doc_text import ExtractError, extract, extract_segments
from .embeddings import EmbeddingError, get_embedding_provider
from .vectorstore import VectorError, get_vector_store
from .vision import VisionError, get_vision_provider

TOP_K = int(os.getenv("SOV_RAG_TOP_K", "4"))


def _min_score() -> float:
    try:
        return float(os.getenv("SOV_RAG_MIN_SCORE", "0.15"))
    except Exception:
        return 0.15


def index_document(doc: dict, user: dict) -> dict:
    """Full pipeline for one analyzed document. Returns refreshed record."""
    import json as _json
    store.update(doc["id"], status="PROCESSING", proc_status="processing", proc_error="")
    try:
        path = _stored_path(doc)
        if not os.path.exists(path):
            raise OSError("stored file missing")
        with open(path, "rb") as fh:
            data = fh.read()
        if doc["kind"] == "image":
            from . import private_images
            data = private_images.unprotect(data)  # fail-closed if key missing
        vision_meta: dict | None = None
        if doc["kind"] == "image":
            vp = get_vision_provider()
            info = vp.validate(data)  # raises VisionError on corrupt -> FAILED
            # Privacy: images index METADATA ONLY. OCR text is never extracted
            # here, never chunked, never embedded, never searchable. Text
            # recognition runs exclusively via the explicit "Analyze with
            # Private AI" path (images_api), ephemerally.
            vision_meta = {**info, "ocr": {"status": "not_requested"}}
            text = ""
            segments = []
            proc, err = "metadata_only", ""
        else:
            text, _ = extract(doc["kind"], data)
            segments = extract_segments(doc["kind"], data)
            proc, err = "done", ""
        chunks = chunk_segments(doc["id"], doc["owner_id"], doc["filename"], segments)
        dim = 0
        if chunks:
            emb = get_embedding_provider()
            vecs = emb.embed([c["text"] for c in chunks])
            get_vector_store().upsert(chunks, vecs)
            dim = len(vecs[0])
        if doc["kind"] == "image":
            # Purge any legacy OCR-derived vectors/text: image text must never
            # be searchable. (Chunks is already empty; this clears old rows.)
            drop_doc_vectors(doc["id"])
        store.save_text(doc["id"], text)
        store.update(doc["id"], status="READY", proc_status=proc, proc_error=err,
                     text_len=len(text),
                     **({"vision": _json.dumps(vision_meta)} if vision_meta else {}))
        audit_log.append(user["id"], user["role"], "doc_index", resource=doc["id"],
                         detail=f"chunks={len(chunks)} dim={dim} "
                                f"embed={os.getenv('SOV_EMBED_PROVIDER', 'mock')}")
        rec = store.get(doc["id"])
        rec["chunks"] = len(chunks)
        return rec
    except (ExtractError, EmbeddingError, VectorError, VisionError, OSError) as e:
        store.update(doc["id"], status="FAILED", proc_status="failed",
                     proc_error=str(e)[:200])
        audit_log.append(user["id"], user["role"], "doc_index", resource=doc["id"],
                         decision="deny", detail=f"FAILED: {str(e)[:150]}")
        rec = store.get(doc["id"])
        rec["chunks"] = 0
        return rec


def _stored_path(doc: dict) -> str:
    import re
    base = os.getenv("SOV_UPLOADS_DIR", "") or cfg.data_path("uploads")
    stored = doc.get("stored", "")
    if not re.match(r"^d-[0-9a-f]{12}\.(pdf|docx|txt|csv|xlsx|jpg|jpeg|png)$", stored):
        raise OSError("stored file missing")
    path = os.path.realpath(os.path.join(base, stored))
    if os.path.dirname(path) != os.path.realpath(base):
        raise OSError("stored file missing")
    return path


def verify_doc_access(doc_ids: list[str], user: dict):
    """Fail-closed ownership check for every requested document."""
    from fastapi import HTTPException
    is_admin = user["role"] == "ADMIN"
    for did in doc_ids:
        doc = store.get(did)
        if doc is None or (doc["owner_id"] != user["id"] and not is_admin):
            audit_log.append(user["id"], user["role"], "permission_denied",
                             resource=f"rag_doc:{did}", decision="deny")
            raise HTTPException(403, "access denied to document")


def retrieve(user: dict, query: str, doc_ids: list[str] | None = None,
             top_k: int = TOP_K) -> tuple[list[dict], dict]:
    """Embed query -> permission-prefiltered similarity search."""
    from .embeddings import EmbeddingError as EE
    from .vectorstore import VectorError as VE
    emb = get_embedding_provider()
    try:
        qv = emb.embed([query])[0]
    except EE as e:
        raise e
    vs = get_vector_store()
    try:
        hits = vs.search(qv, user["id"], user["role"] == "ADMIN", doc_ids, top_k)
    except VE as e:
        raise e
    floor = _min_score()
    hits = [h for h in hits if h["score"] >= floor]  # below-threshold = not present
    audit_log.append(user["id"], user["role"], "rag_retrieval", resource="rag",
                     detail=f"hits={len(hits)} docs={','.join(sorted({h['doc_id'] for h in hits})) or '-'} "
                            f"top_scores={','.join(str(h['score']) for h in hits[:3]) or '-'}")
    return hits, {"provider": emb.name, "dim": len(qv), "top_k": top_k}


def drop_doc_vectors(doc_id: str) -> int:
    try:
        return get_vector_store().delete_doc(doc_id)
    except Exception:
        return 0
