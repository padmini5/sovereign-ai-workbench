"""Phase 1 audit trail for auth + permission-sensitive ops.

Separate hash-chained SQLite table (auth_audit) so Phase 1 events are
queryable independently of the legacy task audit log in db.py.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time

from . import config as cfg

AUDIT_DB = os.getenv("SOV_AUTH_AUDIT_DB", "") or cfg.data_path("auth_audit.db")


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(AUDIT_DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return con


# init() is a DDL write; append() calls it on every audit event. Running it
# once per process (per path) removes needless write-lock contention that
# caused intermittent 500s when many requests were audited concurrently.
_initialized_paths: set[str] = set()


def init() -> None:
    if AUDIT_DB in _initialized_paths:
        return
    con = _con()
    con.execute(
        """CREATE TABLE IF NOT EXISTS auth_audit(
             id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
             actor_id TEXT, actor_role TEXT, action TEXT NOT NULL,
             resource TEXT, decision TEXT NOT NULL, detail TEXT,
             prev_hash TEXT, hash TEXT)"""
    )
    con.commit()
    con.close()
    _initialized_paths.add(AUDIT_DB)


def _last_hash() -> str:
    con = _con()
    r = con.execute("SELECT hash FROM auth_audit ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return r["hash"] if r else "GENESIS"


def append(actor_id: str | None, actor_role: str | None, action: str,
           resource: str = "", decision: str = "allow", detail: str = "") -> None:
    init()
    ts = time.time()
    prev = _last_hash()
    h = hashlib.sha256(f"{ts}|{actor_id}|{action}|{resource}|{decision}|{prev}".encode()).hexdigest()
    con = _con()
    con.execute(
        "INSERT INTO auth_audit(ts,actor_id,actor_role,action,resource,decision,detail,prev_hash,hash)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (ts, actor_id, actor_role, action, resource, decision, detail, prev, h),
    )
    con.commit()
    con.close()


def rows(limit: int = 200) -> list[dict]:
    init()
    con = _con()
    rs = con.execute(
        "SELECT ts,actor_id,actor_role,action,resource,decision,detail"
        " FROM auth_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rs]
