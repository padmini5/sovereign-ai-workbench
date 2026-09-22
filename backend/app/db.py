"""Append-only audit log (SQLite)."""
import sqlite3, time, json
from . import config as cfg
DB = cfg.AUDIT_DB

def init():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user_id TEXT, role TEXT,
      action TEXT, doc_id TEXT, signature TEXT)""")
    con.commit(); con.close()

def append(user_id, role, action, doc_id, signature: dict):
    init()
    con = sqlite3.connect(DB)
    con.execute("INSERT INTO audit(ts,user_id,role,action,doc_id,signature) VALUES(?,?,?,?,?,?)",
        (time.time(), user_id, role, action, doc_id, json.dumps(signature)))
    con.commit(); con.close()

def rows(limit=200):
    init()
    con = sqlite3.connect(DB)
    r = con.execute("SELECT ts,user_id,role,action,doc_id FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [{"ts": t, "user_id": u, "role": r_, "action": a, "doc_id": d} for t, u, r_, a, d in r]
