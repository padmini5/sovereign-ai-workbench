"""Document records + extracted text (Phase 3).

SQLite default (SOV_DOCS_DB), PostgreSQL when DATABASE_URL + psycopg —
same schema. Files themselves live on disk (uploads dir), never in the DB.
"""
from __future__ import annotations

import os
import time
import uuid

from . import config as cfg

DOCS_DB = os.getenv("SOV_DOCS_DB", "") or cfg.data_path("docs.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, uploader TEXT NOT NULL,
  filename TEXT NOT NULL, stored TEXT NOT NULL,
  mime TEXT NOT NULL, ext TEXT NOT NULL, kind TEXT NOT NULL,
  size INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'uploaded',
  proc_status TEXT NOT NULL DEFAULT 'pending', proc_error TEXT NOT NULL DEFAULT '',
  text_len INTEGER NOT NULL DEFAULT 0, vision TEXT NOT NULL DEFAULT '{}',
  created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS doc_texts(
  doc_id TEXT PRIMARY KEY REFERENCES documents(id), text TEXT NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL);
"""


def _use_pg() -> bool:
    if not DATABASE_URL:
        return False
    try:
        import psycopg  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def _lite():
    import sqlite3
    con = sqlite3.connect(DOCS_DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return con


def backend() -> str:
    return "postgresql" if _use_pg() else "sqlite"


# DDL once per process per path (see userstore.init_db): running
# executescript on every store call serialized requests on SQLite's write
# lock, producing intermittent 500s under concurrent browser requests.
_initialized_paths: set[str] = set()


def init_db() -> None:
    if not _use_pg() and DOCS_DB in _initialized_paths:
        return
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute(SCHEMA)
        return
    con = _lite()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    _initialized_paths.add(DOCS_DB)


def _to_dict(r) -> dict:
    return dict(r)


def create(owner_id: str, uploader: str, filename: str, stored: str,
           mime: str, ext: str, kind: str, size: int) -> dict:
    init_db()
    rec = {"id": "d-" + uuid.uuid4().hex[:10], "owner_id": owner_id, "uploader": uploader,
           "filename": filename[:255], "stored": stored, "mime": mime, "ext": ext,
           "kind": kind, "size": size, "status": "UPLOADED", "proc_status": "pending",
           "proc_error": "", "text_len": 0, "vision": "{}", "created_at": time.time()}
    cols = ",".join(rec)
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute(f"INSERT INTO documents({cols}) VALUES(" + ",".join(["%s"] * len(rec)) + ")",
                        tuple(rec.values()))
        return rec
    con = _lite()
    con.execute(f"INSERT INTO documents({cols}) VALUES(:" + ",:".join(rec) + ")", rec)
    con.commit()
    con.close()
    return rec


def get(doc_id: str) -> dict | None:
    init_db()
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM documents WHERE id=%s", (doc_id,))
                r = cur.fetchone()
                if not r:
                    return None
                return dict(zip([d[0] for d in cur.description], r))
    con = _lite()
    r = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    con.close()
    return _to_dict(r) if r else None


def list_for(owner_id: str | None, q: str = "", kind: str = "", status: str = "",
             limit: int = 100, owner: str | None = None,
             created_from: float = 0, created_to: float = 0) -> list[dict]:
    init_db()
    where, args = [], []
    if owner_id is not None:
        where.append("owner_id=?"); args.append(owner_id)
    if owner:
        where.append("owner_id=?"); args.append(owner)
    if q:
        where.append("filename LIKE ?"); args.append(f"%{q[:60]}%")
    if kind:
        where.append("kind=?"); args.append(kind)
    if status:
        where.append("status=?"); args.append(status)
    if created_from:
        where.append("created_at>=?"); args.append(created_from)
    if created_to:
        where.append("created_at<=?"); args.append(created_to)
    sql = "SELECT * FROM documents" + (" WHERE " + " AND ".join(where) if where else "") \
        + " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    if _use_pg():
        import psycopg
        sql = sql.replace("?", "%s").replace("LIKE %s", "LIKE %s")
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute(sql, tuple(args))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    con = _lite()
    rs = con.execute(sql, tuple(args)).fetchall()
    con.close()
    return [_to_dict(r) for r in rs]


def update(doc_id: str, **fields) -> None:
    init_db()
    assert fields, "nothing to update"
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute(f"UPDATE documents SET {','.join(f'{k}=%s' for k in fields)} WHERE id=%s",
                        (*fields.values(), doc_id))
        return
    con = _lite()
    con.execute(f"UPDATE documents SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                (*fields.values(), doc_id))
    con.commit()
    con.close()


def delete(doc_id: str) -> None:
    init_db()
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute("DELETE FROM doc_texts WHERE doc_id=%s", (doc_id,))
            con.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
        return
    con = _lite()
    con.execute("DELETE FROM doc_texts WHERE doc_id=?", (doc_id,))
    con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    con.commit()
    con.close()


def save_text(doc_id: str, text: str) -> None:
    init_db()
    now = time.time()
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute("INSERT INTO doc_texts(doc_id,text,updated_at) VALUES(%s,%s,%s)"
                        " ON CONFLICT (doc_id) DO UPDATE SET text=%s, updated_at=%s",
                        (doc_id, text, now, text, now))
        return
    con = _lite()
    con.execute("INSERT OR REPLACE INTO doc_texts(doc_id,text,updated_at) VALUES(?,?,?)",
                (doc_id, text, now))
    con.commit()
    con.close()


def get_text(doc_id: str) -> str:
    init_db()
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute("SELECT text FROM doc_texts WHERE doc_id=%s", (doc_id,))
                r = cur.fetchone()
                return r[0] if r else ""
    con = _lite()
    r = con.execute("SELECT text FROM doc_texts WHERE doc_id=?", (doc_id,)).fetchone()
    con.close()
    return r["text"] if r else ""
