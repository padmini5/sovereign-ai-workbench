"""Agent execution store (Step 21). SQLite (SOV_AGENTS_DB); runs table only —
documents, messages, and audit keep living in their own stores.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from . import config as cfg

AGENTS_DB = os.getenv("SOV_AGENTS_DB", "") or cfg.data_path("agents.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs(
  id TEXT PRIMARY KEY, agent TEXT NOT NULL, owner_id TEXT NOT NULL,
  owner_role TEXT NOT NULL, goal TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'en',
  state TEXT NOT NULL DEFAULT 'CREATED', steps TEXT NOT NULL DEFAULT '[]',
  pending TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
"""


def _con():
    import sqlite3
    con = sqlite3.connect(AGENTS_DB, timeout=30)
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
    if AGENTS_DB in _initialized_paths:
        return
    con = _con()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    _initialized_paths.add(AGENTS_DB)


def create(agent: str, owner_id: str, owner_role: str, goal: str, lang: str) -> dict:
    init_db()
    now = time.time()
    rec = {"id": "a-" + uuid.uuid4().hex[:10], "agent": agent, "owner_id": owner_id,
           "owner_role": owner_role, "goal": goal[:2000], "lang": lang,
           "state": "CREATED", "steps": "[]", "pending": "", "result": "",
           "created_at": now, "updated_at": now}
    con = _con()
    con.execute("INSERT INTO agent_runs(id,agent,owner_id,owner_role,goal,lang,state,"
                "steps,pending,result,created_at,updated_at) VALUES(:id,:agent,:owner_id,"
                ":owner_role,:goal,:lang,:state,:steps,:pending,:result,:created_at,:updated_at)", rec)
    con.commit()
    con.close()
    return _pub(rec)


def _pub(r: dict) -> dict:
    out = dict(r)
    for k in ("steps",):
        try:
            out[k] = json.loads(out.get(k) or "[]")
        except Exception:
            out[k] = []
    return out


def get(run_id: str, owner_id: str, is_admin: bool) -> dict | None:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    con.close()
    if not r:
        return None
    d = _pub(dict(r))
    if d["owner_id"] != owner_id and not is_admin:
        return None
    return d


def save(run_id: str, **fields) -> None:
    init_db()
    fields["updated_at"] = time.time()
    if "steps" in fields and not isinstance(fields["steps"], str):
        fields["steps"] = json.dumps(fields["steps"])
    con = _con()
    con.execute(f"UPDATE agent_runs SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                (*fields.values(), run_id))
    con.commit()
    con.close()


def list_for(owner_id: str | None, limit: int = 50) -> list[dict]:
    init_db()
    con = _con()
    if owner_id is None:
        rs = con.execute("SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?",
                         (limit,)).fetchall()
    else:
        rs = con.execute("SELECT * FROM agent_runs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                         (owner_id, limit)).fetchall()
    con.close()
    return [_pub(dict(r)) for r in rs]
