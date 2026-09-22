"""Conversation + message store (Phase 2).

Default: SQLite file (SOV_CHAT_DB, zero-config, on-prem single node).
PostgreSQL: set DATABASE_URL=postgresql://... AND have `psycopg` installed;
the same schema is used (TEXT/DOUBLE columns — pgvector arrives with RAG).
Ownership is enforced by callers (owner_id match) — never trust client ids.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from . import config as cfg

CHAT_DB = os.getenv("SOV_CHAT_DB", "") or cfg.data_path("chat.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
  id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, owner_role TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, convo_id TEXT NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL, content TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  tools_used TEXT NOT NULL DEFAULT '[]', lang TEXT NOT NULL DEFAULT 'en',
  created_at DOUBLE PRECISION NOT NULL);
"""


def _use_pg() -> bool:
    if not DATABASE_URL:
        return False
    try:
        import psycopg  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


# ---- SQLite path (default) ----

def _lite():
    import sqlite3
    con = sqlite3.connect(CHAT_DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return con


# DDL once per process per path (see userstore.init_db) — avoids needless
# write-lock contention that caused intermittent 500s on concurrent requests.
_initialized_paths: set[str] = set()


def init_db() -> None:
    if not _use_pg() and CHAT_DB in _initialized_paths:
        return
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute(SCHEMA)
        return
    con = _lite()
    con.executescript(SCHEMA)
    try:  # Step 19: answer language per message (existing DBs gain the column)
        con.execute("ALTER TABLE messages ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'")
    except Exception:
        pass
    con.commit()
    con.close()
    _initialized_paths.add(CHAT_DB)


def backend() -> str:
    return "postgresql" if _use_pg() else "sqlite"


def create_conversation(owner_id: str, owner_role: str, title: str = "") -> dict:
    init_db()
    rec = {"id": "c-" + uuid.uuid4().hex[:10], "owner_id": owner_id,
           "owner_role": owner_role, "title": title[:120], "created_at": time.time()}
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute("INSERT INTO conversations(id,owner_id,owner_role,title,created_at)"
                        " VALUES(%s,%s,%s,%s,%s)",
                        (rec["id"], owner_id, owner_role, rec["title"], rec["created_at"]))
        return rec
    con = _lite()
    con.execute("INSERT INTO conversations(id,owner_id,owner_role,title,created_at)"
                " VALUES(:id,:owner_id,:owner_role,:title,:created_at)", rec)
    con.commit()
    con.close()
    return rec


def get_conversation(cid: str, owner_id: str) -> dict | None:
    """Ownership-checked fetch. Returns None for missing OR foreign ids (no probing)."""
    init_db()
    ph, query = ("?", "SELECT * FROM conversations WHERE id=? AND owner_id=?")
    if _use_pg():
        import psycopg
        ph, query = ("%s", "SELECT * FROM conversations WHERE id=%s AND owner_id=%s")
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute(query, (cid, owner_id))
                r = cur.fetchone()
                if not r:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, r))
    con = _lite()
    r = con.execute(query, (cid, owner_id)).fetchone()
    con.close()
    return dict(r) if r else None


def list_conversations(owner_id: str, limit: int = 50) -> list[dict]:
    init_db()
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM conversations WHERE owner_id=%s"
                            " ORDER BY created_at DESC LIMIT %s", (owner_id, limit))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    con = _lite()
    rs = con.execute("SELECT * FROM conversations WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                     (owner_id, limit)).fetchall()
    con.close()
    return [dict(r) for r in rs]


def delete_conversation(cid: str, owner_id: str) -> bool:
    init_db()
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute("DELETE FROM messages WHERE convo_id=%s AND EXISTS"
                            " (SELECT 1 FROM conversations WHERE id=%s AND owner_id=%s)",
                            (cid, cid, owner_id))
                cur.execute("DELETE FROM conversations WHERE id=%s AND owner_id=%s", (cid, owner_id))
                return cur.rowcount > 0
    con = _lite()
    if not con.execute("SELECT 1 FROM conversations WHERE id=? AND owner_id=?",
                       (cid, owner_id)).fetchone():
        con.close()
        return False
    con.execute("DELETE FROM messages WHERE convo_id=?", (cid,))
    con.execute("DELETE FROM conversations WHERE id=?", (cid,))
    con.commit()
    con.close()
    return True


def add_message(cid: str, role: str, content: str, provider: str = "",
                model: str = "", tools_used: list | None = None,
                lang: str = "en") -> dict:
    init_db()
    rec = {"id": "m-" + uuid.uuid4().hex[:10], "convo_id": cid, "role": role,
           "content": content, "provider": provider, "model": model,
           "tools_used": json.dumps(tools_used or []), "lang": lang,
           "created_at": time.time()}
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            con.execute("INSERT INTO messages(id,convo_id,role,content,provider,model,tools_used,lang,created_at)"
                        " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (rec["id"], cid, role, content, provider, model,
                         rec["tools_used"], lang, rec["created_at"]))
        rec["tools_used"] = tools_used or []
        return rec
    con = _lite()
    con.execute("INSERT INTO messages(id,convo_id,role,content,provider,model,tools_used,lang,created_at)"
                " VALUES(:id,:convo_id,:role,:content,:provider,:model,:tools_used,:lang,:created_at)", rec)
    con.commit()
    con.close()
    rec["tools_used"] = tools_used or []
    return rec


def get_messages(cid: str, owner_id: str) -> list[dict] | None:
    if get_conversation(cid, owner_id) is None:
        return None
    if _use_pg():
        import psycopg
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM messages WHERE convo_id=%s ORDER BY created_at", (cid,))
                cols = [d[0] for d in cur.description]
                out = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    try:
                        d["tools_used"] = json.loads(d.get("tools_used") or "[]")
                    except Exception:
                        d["tools_used"] = []
                    out.append(d)
                return out
    con = _lite()
    rs = con.execute("SELECT * FROM messages WHERE convo_id=? ORDER BY created_at", (cid,)).fetchall()
    con.close()
    out = []
    for r in rs:
        d = dict(r)
        try:
            d["tools_used"] = json.loads(d.get("tools_used") or "[]")
        except Exception:
            d["tools_used"] = []
        out.append(d)
    return out
