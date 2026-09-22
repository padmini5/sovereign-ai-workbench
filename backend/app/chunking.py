"""Chunking service (Phase 4).

Splits extracted text into overlapping chunks; every chunk retains
document_id, owner_id, page (where available), source filename,
chunk index, and created_at. Word-boundary splits, per-doc caps.
"""
from __future__ import annotations

import time

CHUNK_CHARS = 1200
OVERLAP_CHARS = 150
MAX_CHUNKS_PER_DOC = 500


def chunk_segments(doc_id: str, owner_id: str, filename: str,
                   segments: list[tuple[int | None, str]],
                   size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[dict]:
    chunks: list[dict] = []
    now = time.time()
    for page, text in segments:
        words, cur = text.split(), ""
        for w in words:
            if len(cur) + len(w) + 1 > size and cur:
                chunks.append(_make(doc_id, owner_id, filename, page, len(chunks), cur, now))
                tail = cur.split()[-max(1, overlap // 6):]
                cur = " ".join(tail)
                if len(chunks) >= MAX_CHUNKS_PER_DOC:
                    break
            cur = (cur + " " + w).strip()
        if cur and len(chunks) < MAX_CHUNKS_PER_DOC:
            chunks.append(_make(doc_id, owner_id, filename, page, len(chunks), cur, now))
        if len(chunks) >= MAX_CHUNKS_PER_DOC:
            break
    return chunks


def _make(doc_id: str, owner_id: str, filename: str, page: int | None,
          idx: int, text: str, now: float) -> dict:
    return {"id": f"{doc_id}#c{idx}", "doc_id": doc_id, "owner_id": owner_id,
            "chunk_index": idx, "page": page, "filename": filename,
            "text": text, "created_at": now}
