"""SIH flagship workflow: inspection approval-note records.

One record per inspection_approval agent run, stored in its own SQLite file
(SOV_APPROVALS_DB / data/approvals.db). It carries the extracted source text,
the retrieved authorized procedures, the grounded analysis, the assembled
note, the generated DOCX location, and the human decision.

Access rules live in approvals_api.py (owner / WORK_MANAGE / ADMIN). The
`stored` filesystem name never leaves this module: public() strips it, and
document_path() re-validates confinement on every read.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

from . import config as cfg

APPROVALS_DB = os.getenv("SOV_APPROVALS_DB", "") or cfg.data_path("approvals.db")
APPROVALS_DIR = os.getenv("SOV_APPROVALS_DIR", "") or cfg.data_path("approvals")

SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_notes(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
  owner_id TEXT NOT NULL, owner_role TEXT NOT NULL, requester TEXT NOT NULL,
  doc_id TEXT NOT NULL, doc_filename TEXT NOT NULL, doc_created_at REAL NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PREPARING', stage TEXT NOT NULL DEFAULT 'preparing',
  extracted_text TEXT NOT NULL DEFAULT '', extract_meta TEXT NOT NULL DEFAULT '{}',
  procedures TEXT NOT NULL DEFAULT '[]', analysis TEXT NOT NULL DEFAULT '{}',
  note TEXT NOT NULL DEFAULT '{}', analysis_mode TEXT NOT NULL DEFAULT '',
  stored TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL DEFAULT '', decided_by TEXT NOT NULL DEFAULT '',
  decided_role TEXT NOT NULL DEFAULT '', decided_at REAL,
  comment TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
"""

STATUS_LABELS = {
    "PREPARING": "Preparing",
    "DRAFT": "Draft — awaiting document",
    "AWAITING_HUMAN_APPROVAL": "DRAFT — AWAITING HUMAN APPROVAL",
    "APPROVED": "Approved",
    "REJECTED": "Rejected",
    "CORRECTION_REQUESTED": "Correction requested",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
}

STAGE_LABELS = {
    "preparing": "Preparing",
    "reading_report": "Reading report",
    "finding_procedures": "Finding relevant procedures",
    "analyzing_findings": "Analyzing findings",
    "preparing_approval_note": "Preparing approval note",
    "awaiting_approval": "Awaiting human approval",
    "approved": "Approved",
    "rejected": "Rejected",
    "correction_requested": "Correction requested",
    "failed": "Stopped",
    "cancelled": "Cancelled",
}

JSON_FIELDS = ("extract_meta", "procedures", "analysis", "note")


def fmt_date(ts: float) -> str:
    """User-facing date, e.g. '24 Sep 2026' (server-local, stored as epoch)."""
    try:
        return time.strftime("%d %b %Y", time.localtime(ts))
    except Exception:
        return "Not available in source material."


def _con():
    import sqlite3
    con = sqlite3.connect(APPROVALS_DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return con


_initialized_paths: set[str] = set()


def init_db() -> None:
    if APPROVALS_DB in _initialized_paths:
        return
    con = _con()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    _initialized_paths.add(APPROVALS_DB)


def create_for_run(run_id: str, user: dict, doc_id: str, goal: str) -> dict:
    init_db()
    from . import docs_store
    doc = docs_store.get(doc_id) or {}
    now = time.time()
    rec = {"id": "n-" + uuid.uuid4().hex[:10], "run_id": run_id,
           "owner_id": user["id"], "owner_role": user["role"],
           "requester": str(user.get("username") or user["id"]),
           "doc_id": doc_id,
           "doc_filename": str(doc.get("filename") or doc_id)[:255],
           "doc_created_at": float(doc.get("created_at") or now),
           "goal": (goal or "")[:2000], "status": "PREPARING", "stage": "preparing",
           "extracted_text": "", "extract_meta": "{}", "procedures": "[]",
           "analysis": "{}", "note": "{}", "analysis_mode": "",
           "stored": "", "filename": "", "decision": "", "decided_by": "",
           "decided_role": "", "decided_at": None, "comment": "",
           "created_at": now, "updated_at": now}
    con = _con()
    con.execute(
        "INSERT INTO approval_notes(id,run_id,owner_id,owner_role,requester,doc_id,"
        "doc_filename,doc_created_at,goal,status,stage,extracted_text,extract_meta,"
        "procedures,analysis,note,analysis_mode,stored,filename,decision,decided_by,"
        "decided_role,decided_at,comment,created_at,updated_at) "
        "VALUES(:id,:run_id,:owner_id,:owner_role,:requester,:doc_id,:doc_filename,"
        ":doc_created_at,:goal,:status,:stage,:extracted_text,:extract_meta,:procedures,"
        ":analysis,:note,:analysis_mode,:stored,:filename,:decision,:decided_by,"
        ":decided_role,:decided_at,:comment,:created_at,:updated_at)", rec)
    con.commit()
    con.close()
    return _pub(rec)


def _pub(r: dict) -> dict:
    out = dict(r)
    for k in JSON_FIELDS:
        try:
            out[k] = json.loads(out.get(k) or ("[]" if k == "procedures" else "{}"))
        except Exception:
            out[k] = [] if k == "procedures" else {}
    return out


def get(rec_id: str) -> dict | None:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM approval_notes WHERE id=?", (rec_id,)).fetchone()
    con.close()
    return _pub(dict(r)) if r else None


def get_by_run(run_id: str) -> dict | None:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM approval_notes WHERE run_id=?", (run_id,)).fetchone()
    con.close()
    return _pub(dict(r)) if r else None


def update(rec_id: str, **fields) -> None:
    init_db()
    for k in JSON_FIELDS:
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    fields["updated_at"] = time.time()
    con = _con()
    con.execute(f"UPDATE approval_notes SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                (*fields.values(), rec_id))
    con.commit()
    con.close()


def mark_terminal(run_id: str, run_state: str) -> None:
    """Agent run ended without reaching human approval — reflect it here.
    Never overwrites a decision or an awaiting-approval note."""
    if run_state == "COMPLETED":
        return
    rec = get_by_run(run_id)
    if rec is None or rec["status"] not in ("PREPARING", "DRAFT"):
        return
    status = "CANCELLED" if run_state == "CANCELLED" else "FAILED"
    update(rec["id"], status=status, stage="cancelled" if status == "CANCELLED" else "failed")


def list_for(owner_id: str | None, limit: int = 50) -> list[dict]:
    init_db()
    con = _con()
    if owner_id is None:
        rs = con.execute("SELECT * FROM approval_notes ORDER BY created_at DESC LIMIT ?",
                         (limit,)).fetchall()
    else:
        rs = con.execute("SELECT * FROM approval_notes WHERE owner_id=? "
                         "ORDER BY created_at DESC LIMIT ?", (owner_id, limit)).fetchall()
    con.close()
    return [_pub(dict(r)) for r in rs]


_STORED_RE = re.compile(r"^an-[0-9a-f]{10}\.docx$")


def document_path(rec: dict) -> str:
    """Confined resolution of the generated DOCX (defense in depth)."""
    stored = rec.get("stored", "")
    if not _STORED_RE.match(stored):
        raise FileNotFoundError(stored)
    path = os.path.realpath(os.path.join(APPROVALS_DIR, stored))
    if os.path.dirname(path) != os.path.realpath(APPROVALS_DIR) or not os.path.isfile(path):
        raise FileNotFoundError(stored)
    return path


def public(rec: dict, detail: bool = False) -> dict:
    """API shape — never exposes `stored`, DB paths, or the full raw text
    (detail adds a capped source excerpt for reviewers)."""
    out = {"id": rec["id"], "run_id": rec["run_id"], "status": rec["status"],
           "status_label": STATUS_LABELS.get(rec["status"], rec["status"]),
           "stage": rec["stage"],
           "stage_label": STAGE_LABELS.get(rec["stage"], rec["stage"]),
           "requester": rec["requester"], "doc_id": rec["doc_id"],
           "doc_filename": rec["doc_filename"],
           "doc_uploaded": fmt_date(rec["doc_created_at"]),
           "goal": rec["goal"], "note": rec.get("note") or {},
           "analysis_mode": rec.get("analysis_mode", ""),
           "extract": rec.get("extract_meta") or {},
           "procedures": rec.get("procedures") or [],
           "filename": rec["filename"], "has_document": bool(rec["stored"]),
           "decision": rec["decision"], "decided_by": rec["decided_by"],
           "decided_role": rec["decided_role"], "decided_at": rec["decided_at"],
           "comment": rec["comment"],
           "created_at": rec["created_at"], "updated_at": rec["updated_at"]}
    if detail:
        out["source_excerpt"] = rec.get("extracted_text", "")[:4000]
        out["analysis"] = rec.get("analysis") or {}
    return out
