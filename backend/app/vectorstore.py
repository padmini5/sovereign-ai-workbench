"""Vector store abstraction (Phase 4).

Backends: local SQLite (default, brute-force cosine over owner-prefiltered
rows) or PostgreSQL + pgvector when DATABASE_URL + psycopg are present
(cosine operator search; falls back to local if the `vector` extension is
missing). SOV_VECTOR_BACKEND=fail forces errors for failure-path tests.

Permission filtering ALWAYS happens inside search(): callers pass the
user's scope (owner_id, is_admin) and only allowed rows are scored —
unreachable vectors never reach the LLM.
"""
from __future__ import annotations

import json
import math
import os
from abc import ABC, abstractmethod

from . import config as cfg

VECTORS_DB = os.getenv("SOV_VECTORS_DB", "") or cfg.data_path("vectors.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS doc_chunks(
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, owner_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL, page INTEGER, filename TEXT NOT NULL,
  text TEXT NOT NULL, embedding TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_owner ON doc_chunks(owner_id);
"""


class VectorError(Exception):
    pass


def _cosine(a: list[float], b: list[float]) -> float:
    n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if not n:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / n


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> int: ...
    @abstractmethod
    def delete_doc(self, doc_id: str) -> int: ...
    @abstractmethod
    def count_for(self, doc_id: str) -> int: ...
    @abstractmethod
    def search(self, query_vec: list[float], owner_id: str, is_admin: bool,
               doc_ids: list[str] | None, top_k: int) -> list[dict]: ...
    def health(self) -> dict:
        return {"backend": "base", "ok": True}


class LocalVectorStore(VectorStore):
    def __init__(self, path: str = ""):
        import sqlite3
        self.path = path or VECTORS_DB
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA_SQLITE)
        con.commit()
        con.close()

    def _con(self):
        import sqlite3
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> int:
        con = self._con()
        for ch, vec in zip(chunks, vectors):
            con.execute("INSERT OR REPLACE INTO doc_chunks(id,doc_id,owner_id,chunk_index,"
                        "page,filename,text,embedding,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (ch["id"], ch["doc_id"], ch["owner_id"], ch["chunk_index"],
                         ch["page"], ch["filename"], ch["text"],
                         json.dumps(vec), ch["created_at"]))
        con.commit()
        con.close()
        return len(chunks)

    def delete_doc(self, doc_id: str) -> int:
        con = self._con()
        cur = con.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
        con.commit()
        n = cur.rowcount
        con.close()
        return n

    def count_for(self, doc_id: str) -> int:
        con = self._con()
        r = con.execute("SELECT COUNT(*) c FROM doc_chunks WHERE doc_id=?", (doc_id,)).fetchone()
        con.close()
        return r["c"]

    def search(self, query_vec: list[float], owner_id: str, is_admin: bool,
               doc_ids: list[str] | None, top_k: int) -> list[dict]:
        con = self._con()
        if is_admin:
            rs = con.execute("SELECT * FROM doc_chunks").fetchall()
        else:
            rs = con.execute("SELECT * FROM doc_chunks WHERE owner_id=?", (owner_id,)).fetchall()
        con.close()
        allowed = {d for d in (doc_ids or [])}
        scored = []
        for r in rs:
            d = dict(r)
            if allowed and d["doc_id"] not in allowed:
                continue
            try:
                vec = json.loads(d["embedding"])
            except Exception:
                continue
            if len(vec) != len(query_vec):
                continue  # dim mismatch (provider switched) — skip, don't crash
            scored.append((float(_cosine(query_vec, vec)), d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"id": d["id"], "doc_id": d["doc_id"], "chunk_index": d["chunk_index"],
                 "page": d["page"], "filename": d["filename"], "text": d["text"],
                 "score": round(s, 4)} for s, d in scored[:top_k]]

    def health(self) -> dict:
        return {"backend": "sqlite-local", "ok": True}


class PgVectorStore(VectorStore):
    """PostgreSQL + pgvector. Requires the `vector` extension; raises
    VectorError with guidance if it is absent (fail-loud, not silent)."""

    def __init__(self, url: str, dim: int = 16):
        import psycopg
        self.url, self.dim = url, dim
        with psycopg.connect(url, autocommit=True) as con:
            try:  # works on images shipping the extension (e.g. pgvector/pgvector)
                con.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception:
                pass
            ext = con.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone()
            if not ext:
                raise VectorError("pgvector extension missing: run CREATE EXTENSION vector")
            con.execute("""CREATE TABLE IF NOT EXISTS doc_chunks(
              id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              chunk_index INTEGER NOT NULL, page INTEGER, filename TEXT NOT NULL,
              text TEXT NOT NULL, embedding vector, created_at DOUBLE PRECISION NOT NULL);
              CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks(doc_id);
              CREATE INDEX IF NOT EXISTS idx_chunks_owner ON doc_chunks(owner_id);""")

    def upsert(self, chunks: list[dict], vectors: list[list[float]]) -> int:
        import psycopg
        with psycopg.connect(self.url) as con:
            for ch, vec in zip(chunks, vectors):
                con.execute("""INSERT INTO doc_chunks(id,doc_id,owner_id,chunk_index,page,
                  filename,text,embedding,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)
                  ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, embedding=EXCLUDED.embedding""",
                            (ch["id"], ch["doc_id"], ch["owner_id"], ch["chunk_index"],
                             ch["page"], ch["filename"], ch["text"], vec, ch["created_at"]))
        return len(chunks)

    def delete_doc(self, doc_id: str) -> int:
        import psycopg
        with psycopg.connect(self.url) as con:
            with con.cursor() as cur:
                cur.execute("DELETE FROM doc_chunks WHERE doc_id=%s", (doc_id,))
                return cur.rowcount

    def count_for(self, doc_id: str) -> int:
        import psycopg
        with psycopg.connect(self.url) as con:
            with con.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM doc_chunks WHERE doc_id=%s", (doc_id,))
                return cur.fetchone()[0]

    def search(self, query_vec: list[float], owner_id: str, is_admin: bool,
               doc_ids: list[str] | None, top_k: int) -> list[dict]:
        import psycopg
        where: list = []
        args: list = [query_vec]
        if not is_admin:
            where.append("owner_id=%s")
            args.append(owner_id)
        if doc_ids:
            where.append("doc_id = ANY(%s)")
            args.append(doc_ids)
        sql = ("SELECT id,doc_id,chunk_index,page,filename,text,"
               " 1 - (embedding <=> %s::vector) AS score FROM doc_chunks"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY embedding <=> %s::vector LIMIT %s")
        args += [query_vec, top_k]
        with psycopg.connect(self.url) as con:
            with con.cursor() as cur:
                cur.execute(sql, tuple(args))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]

    def health(self) -> dict:
        return {"backend": "pgvector", "ok": True}


class FailVectorStore(VectorStore):
    def _boom(self):
        raise VectorError("vector backend forced failure")

    def upsert(self, chunks, vectors): return self._boom()
    def delete_doc(self, doc_id): return self._boom()
    def count_for(self, doc_id): return self._boom()
    def search(self, q, o, a, d, k): return self._boom()
    def health(self): return {"backend": "fail", "ok": False}


def get_vector_store(dim: int = 16) -> VectorStore:
    if (os.getenv("SOV_VECTOR_BACKEND") or "").lower() == "fail":
        return FailVectorStore()
    if DATABASE_URL:
        try:
            import psycopg  # type: ignore  # noqa: F401
            try:
                return PgVectorStore(DATABASE_URL, dim=dim)
            except VectorError:
                pass  # extension missing -> honest local fallback
        except Exception:
            pass
    return LocalVectorStore()
