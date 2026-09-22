"""Step 27 workspace store — SQLite (dev/on-prem single node).

Follows the userstore.py precedent: plain parameterized SQL, Postgres-
compatible shape, no SQLite-isms in queries. All scoping (own vs team vs
all) is enforced by callers in work_api.py — this module stores and fetches.
Tables: attendance, tasks, evidence, worksheets, assignments, reports,
feedback. Audit events live in audit_log (metadata only).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

from . import config as cfg

WORK_DB = os.getenv("SOV_WORK_DB", "") or cfg.data_path("work.db")

TASK_STATUSES = ("assigned", "in_progress", "submitted", "needs_correction",
                 "reviewed", "approved", "blocked")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")
ATTENDANCE_STATUSES = ("present", "absent", "leave", "holiday", "late")
ASSIGN_STATUSES = ("assigned", "started", "in_progress", "submitted",
                   "reviewed", "approved", "needs_correction")
REPORT_TYPES = ("employee_work", "team_work", "department", "monthly_ops",
                "performance", "attendance", "financial")


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(WORK_DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return con


# DDL runs once per process per path: running executescript on every store
# call serialized requests on SQLite's write lock (intermittent 500s under
# the parallel requests a browser sends). Still idempotent + re-runs if the
# configured path changes.
_initialized_paths: set[str] = set()


def init_db() -> None:
    if WORK_DB in _initialized_paths:
        return
    con = _con()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS attendance(
      id TEXT PRIMARY KEY, user_id TEXT NOT NULL, date TEXT NOT NULL,
      status TEXT NOT NULL, marked_by TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL,
      UNIQUE(user_id, date));
    CREATE TABLE IF NOT EXISTS tasks(
      id TEXT PRIMARY KEY, title TEXT NOT NULL, instructions TEXT NOT NULL DEFAULT '',
      assignee_id TEXT NOT NULL, created_by TEXT NOT NULL, department TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'assigned', progress INTEGER NOT NULL DEFAULT 0,
      due_date TEXT NOT NULL DEFAULT '', review_remarks TEXT NOT NULL DEFAULT '',
      reviewed_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS evidence(
      id TEXT PRIMARY KEY, task_id TEXT NOT NULL DEFAULT '', owner_id TEXT NOT NULL,
      doc_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS worksheets(
      id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
      columns_json TEXT NOT NULL DEFAULT '[]', source_doc_id TEXT NOT NULL DEFAULT '',
      created_by TEXT NOT NULL, department TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS assignments(
      id TEXT PRIMARY KEY, worksheet_id TEXT NOT NULL, assignee_id TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'assigned', fields_json TEXT NOT NULL DEFAULT '{}',
      evidence_doc_ids TEXT NOT NULL DEFAULT '[]', review_remarks TEXT NOT NULL DEFAULT '',
      reviewed_by TEXT NOT NULL DEFAULT '', submitted_at REAL NOT NULL DEFAULT 0,
      created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS reports(
      id TEXT PRIMARY KEY, title TEXT NOT NULL, report_type TEXT NOT NULL,
      scope_type TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '',
      period_from TEXT NOT NULL DEFAULT '', period_to TEXT NOT NULL DEFAULT '',
      metrics_json TEXT NOT NULL DEFAULT '{}', analysis TEXT NOT NULL DEFAULT '',
      sources_json TEXT NOT NULL DEFAULT '[]', official_remarks TEXT NOT NULL DEFAULT '',
      created_by TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS feedback(
      id TEXT PRIMARY KEY, to_user_id TEXT NOT NULL, from_user_id TEXT NOT NULL,
      task_id TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, rating INTEGER,
      created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS identity(
      user_id TEXT PRIMARY KEY, enrolled INTEGER NOT NULL DEFAULT 0,
      method TEXT NOT NULL DEFAULT '', face_doc_id TEXT NOT NULL DEFAULT '',
      face_sha256 TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS task_extra(
      task_id TEXT PRIMARY KEY REFERENCES tasks(id));
    CREATE TABLE IF NOT EXISTS schedules(
      id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
      shift TEXT NOT NULL DEFAULT 'Morning', start TEXT NOT NULL DEFAULT '09:00',
      end TEXT NOT NULL DEFAULT '18:00', break_start TEXT NOT NULL DEFAULT '13:00',
      break_end TEXT NOT NULL DEFAULT '14:00',
      working_days TEXT NOT NULL DEFAULT '[]', holidays TEXT NOT NULL DEFAULT '[]',
      scope_type TEXT NOT NULL DEFAULT 'user', scope_id TEXT NOT NULL DEFAULT '',
      notes TEXT NOT NULL DEFAULT '', demo INTEGER NOT NULL DEFAULT 0,
      created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
      updated_at REAL NOT NULL);
    """)
    # Additive columns for existing databases (CREATE TABLE IF NOT EXISTS
    # never alters an already-created table). Priority/start-date are shown
    # on the Assign Work form; defaults keep old rows valid.
    existing = {r["name"] for r in con.execute("PRAGMA table_info(tasks)").fetchall()}
    for col, ddl in (
        ("priority", "ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'"),
        ("start_date", "ALTER TABLE tasks ADD COLUMN start_date TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in existing:
            con.execute(ddl)
    con.commit()
    con.close()
    _initialized_paths.add(WORK_DB)


def _now() -> float:
    return time.time()


def _nid(prefix: str) -> str:
    return f"{prefix}-" + uuid.uuid4().hex[:10]


def _row(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def _all(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


# ---------------- attendance ----------------

def mark_attendance(user_id: str, date: str, status: str, marked_by: str, note: str = "") -> dict:
    import re as _re
    if status not in ATTENDANCE_STATUSES:
        raise ValueError(f"unknown attendance status '{status}'")
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date or ""):
        raise ValueError("date must be YYYY-MM-DD")
    init_db()
    con = _con()
    r = _row(con, "SELECT * FROM attendance WHERE user_id=? AND date=?", (user_id, date))
    if r:
        con.execute("UPDATE attendance SET status=?, marked_by=?, note=? WHERE id=?",
                    (status, marked_by, (note or "")[:500], r["id"]))
        r = _row(con, "SELECT * FROM attendance WHERE id=?", (r["id"],))
    else:
        r = {"id": _nid("att"), "user_id": user_id, "date": date, "status": status,
             "marked_by": marked_by, "note": (note or "")[:500], "created_at": _now()}
        con.execute("INSERT INTO attendance(id,user_id,date,status,marked_by,note,created_at)"
                    " VALUES(:id,:user_id,:date,:status,:marked_by,:note,:created_at)", r)
    con.commit()
    con.close()
    return r


def list_attendance(user_ids: list[str], date_from: str = "", date_to: str = "") -> list[dict]:
    init_db()
    if not user_ids:
        return []
    con = _con()
    q = f"SELECT * FROM attendance WHERE user_id IN ({','.join('?' * len(user_ids))})"
    args: list = list(user_ids)
    if date_from:
        q += " AND date>=?"
        args.append(date_from)
    if date_to:
        q += " AND date<=?"
        args.append(date_to)
    out = _all(con, q + " ORDER BY date DESC", args)
    con.close()
    return out


# ---------------- tasks ----------------

def create_task(title: str, instructions: str, assignee_id: str, created_by: str,
                department: str, due_date: str = "", priority: str = "medium",
                start_date: str = "", status: str = "assigned") -> dict:
    if not (title or "").strip():
        raise ValueError("title is required")
    if priority not in TASK_PRIORITIES:
        raise ValueError(f"unknown priority '{priority}'")
    if status not in TASK_STATUSES:
        raise ValueError(f"unknown status '{status}'")
    init_db()
    rec = {"id": _nid("task"), "title": title[:200], "instructions": (instructions or "")[:4000],
           "assignee_id": assignee_id, "created_by": created_by, "department": department or "",
           "status": status, "progress": 0, "due_date": (due_date or "")[:10],
           "priority": priority, "start_date": (start_date or "")[:10],
           "review_remarks": "", "reviewed_by": "", "created_at": _now(), "updated_at": _now()}
    con = _con()
    con.execute("INSERT INTO tasks(id,title,instructions,assignee_id,created_by,department,"
                "status,progress,due_date,priority,start_date,review_remarks,reviewed_by,"
                "created_at,updated_at)"
                " VALUES(:id,:title,:instructions,:assignee_id,:created_by,:department,"
                ":status,:progress,:due_date,:priority,:start_date,:review_remarks,"
                ":reviewed_by,:created_at,:updated_at)", rec)
    con.commit()
    con.close()
    return rec


def get_task(tid: str) -> dict | None:
    init_db()
    con = _con()
    r = _row(con, "SELECT * FROM tasks WHERE id=?", (tid,))
    con.close()
    return r


def list_tasks(assignee_ids: list[str] | None = None, status: str = "",
               date_from: float = 0, date_to: float = 0, limit: int = 200) -> list[dict]:
    init_db()
    con = _con()
    q, args = "SELECT * FROM tasks", []
    clauses = []
    if assignee_ids is not None:
        if not assignee_ids:
            con.close()
            return []
        clauses.append(f"assignee_id IN ({','.join('?' * len(assignee_ids))})")
        args.extend(assignee_ids)
    if status:
        if status not in TASK_STATUSES:
            con.close()
            raise ValueError(f"unknown task status '{status}'")
        clauses.append("status=?")
        args.append(status)
    if date_from:
        clauses.append("created_at>=?")
        args.append(date_from)
    if date_to:
        clauses.append("created_at<=?")
        args.append(date_to)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    out = _all(con, q + " ORDER BY created_at DESC LIMIT ?", (*args, limit))
    con.close()
    return out


def update_task(tid: str, **fields) -> dict | None:
    allowed = {"status", "progress", "review_remarks", "reviewed_by", "instructions",
               "priority", "due_date", "start_date"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "status" in updates and updates["status"] not in TASK_STATUSES:
        raise ValueError(f"unknown task status '{updates['status']}'")
    if "priority" in updates and updates["priority"] not in TASK_PRIORITIES:
        raise ValueError(f"unknown priority '{updates['priority']}'")
    if "progress" in updates:
        updates["progress"] = max(0, min(100, int(updates["progress"])))
    if not updates:
        return get_task(tid)
    init_db()
    con = _con()
    if not _row(con, "SELECT id FROM tasks WHERE id=?", (tid,)):
        con.close()
        return None
    updates["updated_at"] = _now()
    con.execute(f"UPDATE tasks SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                (*updates.values(), tid))
    con.commit()
    r = _row(con, "SELECT * FROM tasks WHERE id=?", (tid,))
    con.close()
    return r


# ---------------- evidence ----------------

def add_evidence(task_id: str, owner_id: str, doc_id: str, note: str) -> dict:
    init_db()
    rec = {"id": _nid("ev"), "task_id": task_id or "", "owner_id": owner_id,
           "doc_id": doc_id or "", "note": (note or "")[:1000], "created_at": _now()}
    con = _con()
    con.execute("INSERT INTO evidence(id,task_id,owner_id,doc_id,note,created_at)"
                " VALUES(:id,:task_id,:owner_id,:doc_id,:note,:created_at)", rec)
    con.commit()
    con.close()
    return rec


def list_evidence(owner_ids: list[str] | None = None, task_id: str = "") -> list[dict]:
    init_db()
    con = _con()
    q, args = "SELECT * FROM evidence", []
    clauses = []
    if owner_ids is not None:
        if not owner_ids:
            con.close()
            return []
        clauses.append(f"owner_id IN ({','.join('?' * len(owner_ids))})")
        args.extend(owner_ids)
    if task_id:
        clauses.append("task_id=?")
        args.append(task_id)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    out = _all(con, q + " ORDER BY created_at DESC LIMIT 500", args)
    con.close()
    return out


# ---------------- worksheets ----------------

def create_worksheet(title: str, description: str, columns: list, source_doc_id: str,
                     created_by: str, department: str) -> dict:
    if not (title or "").strip():
        raise ValueError("title is required")
    cols = [{"name": str(c.get("name", ""))[:80], "type": str(c.get("type", "text"))[:20]}
            for c in (columns or [])[:50] if isinstance(c, dict) and c.get("name")]
    init_db()
    rec = {"id": _nid("ws"), "title": title[:200], "description": (description or "")[:2000],
           "columns_json": json.dumps(cols), "source_doc_id": source_doc_id or "",
           "created_by": created_by, "department": department or "",
           "status": "active", "created_at": _now()}
    con = _con()
    con.execute("INSERT INTO worksheets(id,title,description,columns_json,source_doc_id,"
                "created_by,department,status,created_at) VALUES(:id,:title,:description,"
                ":columns_json,:source_doc_id,:created_by,:department,:status,:created_at)", rec)
    con.commit()
    con.close()
    return rec


def get_worksheet(wid: str) -> dict | None:
    init_db()
    con = _con()
    r = _row(con, "SELECT * FROM worksheets WHERE id=?", (wid,))
    con.close()
    return r


def list_worksheets(departments: list[str] | None = None, status: str = "") -> list[dict]:
    init_db()
    con = _con()
    q, args = "SELECT * FROM worksheets", []
    clauses = []
    if departments is not None:
        if not departments:
            con.close()
            return []
        clauses.append(f"department IN ({','.join('?' * len(departments))})")
        args.extend(departments)
    if status:
        clauses.append("status=?")
        args.append(status)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    out = _all(con, q + " ORDER BY created_at DESC LIMIT 200", args)
    con.close()
    return out


def assign_worksheet(worksheet_id: str, assignee_id: str) -> dict:
    init_db()
    rec = {"id": _nid("asg"), "worksheet_id": worksheet_id, "assignee_id": assignee_id,
           "status": "assigned", "fields_json": "{}", "evidence_doc_ids": "[]",
           "review_remarks": "", "reviewed_by": "", "submitted_at": 0,
           "created_at": _now(), "updated_at": _now()}
    con = _con()
    con.execute("INSERT INTO assignments(id,worksheet_id,assignee_id,status,fields_json,"
                "evidence_doc_ids,review_remarks,reviewed_by,submitted_at,created_at,updated_at)"
                " VALUES(:id,:worksheet_id,:assignee_id,:status,:fields_json,:evidence_doc_ids,"
                ":review_remarks,:reviewed_by,:submitted_at,:created_at,:updated_at)", rec)
    con.commit()
    con.close()
    return rec


def get_assignment(aid: str) -> dict | None:
    init_db()
    con = _con()
    r = _row(con, "SELECT * FROM assignments WHERE id=?", (aid,))
    con.close()
    return r


def list_assignments(assignee_ids: list[str] | None = None, worksheet_id: str = "",
                     status: str = "") -> list[dict]:
    init_db()
    con = _con()
    q, args = "SELECT * FROM assignments", []
    clauses = []
    if assignee_ids is not None:
        if not assignee_ids:
            con.close()
            return []
        clauses.append(f"assignee_id IN ({','.join('?' * len(assignee_ids))})")
        args.extend(assignee_ids)
    if worksheet_id:
        clauses.append("worksheet_id=?")
        args.append(worksheet_id)
    if status:
        if status not in ASSIGN_STATUSES:
            con.close()
            raise ValueError(f"unknown assignment status '{status}'")
        clauses.append("status=?")
        args.append(status)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    out = _all(con, q + " ORDER BY created_at DESC LIMIT 500", args)
    con.close()
    return out


def update_assignment(aid: str, **fields) -> dict | None:
    allowed = {"status", "fields_json", "evidence_doc_ids", "review_remarks", "reviewed_by",
               "submitted_at"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "status" in updates and updates["status"] not in ASSIGN_STATUSES:
        raise ValueError(f"unknown assignment status '{updates['status']}'")
    if not updates:
        return get_assignment(aid)
    init_db()
    con = _con()
    if not _row(con, "SELECT id FROM assignments WHERE id=?", (aid,)):
        con.close()
        return None
    updates["updated_at"] = _now()
    con.execute(f"UPDATE assignments SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                (*updates.values(), aid))
    con.commit()
    r = _row(con, "SELECT * FROM assignments WHERE id=?", (aid,))
    con.close()
    return r


# ---------------- reports ----------------

def create_report(title: str, report_type: str, scope_type: str, scope_id: str,
                  period_from: str, period_to: str, metrics: dict, analysis: str,
                  sources: list, official_remarks: str, created_by: str) -> dict:
    if report_type not in REPORT_TYPES:
        raise ValueError(f"unknown report type '{report_type}'")
    if scope_type not in ("own", "team", "department"):
        raise ValueError(f"unknown scope '{scope_type}'")
    init_db()
    rec = {"id": _nid("rep"), "title": title[:200], "report_type": report_type,
           "scope_type": scope_type, "scope_id": (scope_id or "")[:120],
           "period_from": (period_from or "")[:10], "period_to": (period_to or "")[:10],
           "metrics_json": json.dumps(metrics or {}), "analysis": (analysis or "")[:8000],
           "sources_json": json.dumps(sources or [])[:4000],
           "official_remarks": (official_remarks or "")[:2000],
           "created_by": created_by, "created_at": _now()}
    con = _con()
    con.execute("INSERT INTO reports(id,title,report_type,scope_type,scope_id,period_from,"
                "period_to,metrics_json,analysis,sources_json,official_remarks,created_by,"
                "created_at) VALUES(:id,:title,:report_type,:scope_type,:scope_id,:period_from,"
                ":period_to,:metrics_json,:analysis,:sources_json,:official_remarks,:created_by,"
                ":created_at)", rec)
    con.commit()
    con.close()
    return rec


def get_report(rid: str) -> dict | None:
    init_db()
    con = _con()
    r = _row(con, "SELECT * FROM reports WHERE id=?", (rid,))
    con.close()
    return r


def list_reports(creator_ids: list[str] | None = None, report_type: str = "") -> list[dict]:
    init_db()
    con = _con()
    q, args = "SELECT * FROM reports", []
    clauses = []
    if creator_ids is not None:
        if not creator_ids:
            con.close()
            return []
        clauses.append(f"created_by IN ({','.join('?' * len(creator_ids))})")
        args.extend(creator_ids)
    if report_type:
        clauses.append("report_type=?")
        args.append(report_type)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    out = _all(con, q + " ORDER BY created_at DESC LIMIT 200", args)
    con.close()
    return out


# ---------------- feedback ----------------

def add_feedback(to_user_id: str, from_user_id: str, task_id: str, text: str,
                 rating: int | None) -> dict:
    if not (text or "").strip():
        raise ValueError("feedback text is required")
    if rating is not None and (rating < 1 or rating > 5):
        raise ValueError("rating must be 1..5")
    init_db()
    rec = {"id": _nid("fb"), "to_user_id": to_user_id, "from_user_id": from_user_id,
           "task_id": task_id or "", "text": text[:2000], "rating": rating, "created_at": _now()}
    con = _con()
    con.execute("INSERT INTO feedback(id,to_user_id,from_user_id,task_id,text,rating,created_at)"
                " VALUES(:id,:to_user_id,:from_user_id,:task_id,:text,:rating,:created_at)", rec)
    con.commit()
    con.close()
    return rec


def list_feedback(to_user_ids: list[str] | None = None) -> list[dict]:
    init_db()
    con = _con()
    if to_user_ids is None:
        out = _all(con, "SELECT * FROM feedback ORDER BY created_at DESC LIMIT 500")
    elif not to_user_ids:
        out = []
    else:
        out = _all(con, f"SELECT * FROM feedback WHERE to_user_id IN "
                        f"({','.join('?' * len(to_user_ids))}) ORDER BY created_at DESC LIMIT 500",
                   to_user_ids)
    con.close()
    return out


# ---------------- identity (privacy-conscious verification concept) ----------------

def get_identity(uid: str) -> dict:
    """Enrollment record only: no biometrics stored, just a hash + doc ref."""
    init_db()
    con = _con()
    r = _row(con, "SELECT * FROM identity WHERE user_id=?", (uid,))
    con.close()
    return r or {"user_id": uid, "enrolled": 0, "method": "", "face_doc_id": "",
                 "face_sha256": "", "updated_at": 0}


def enroll_identity(uid: str, method: str, face_doc_id: str, face_sha256: str) -> dict:
    init_db()
    con = _con()
    rec = {"user_id": uid, "enrolled": 1, "method": (method or "")[:40],
           "face_doc_id": (face_doc_id or "")[:64], "face_sha256": (face_sha256 or "")[:128],
           "updated_at": _now()}
    con.execute("INSERT INTO identity(user_id,enrolled,method,face_doc_id,face_sha256,updated_at)"
                " VALUES(:user_id,:enrolled,:method,:face_doc_id,:face_sha256,:updated_at)"
                " ON CONFLICT(user_id) DO UPDATE SET enrolled=1, method=excluded.method,"
                " face_doc_id=excluded.face_doc_id, face_sha256=excluded.face_sha256,"
                " updated_at=excluded.updated_at", rec)
    con.commit()
    con.close()
    return get_identity(uid)


# ---------------- schedules (per-employee / team roster) ----------------
# Additive SIH demo feature: the org-default shift remains the fallback;
# an assigned schedule row overrides it for its scope. `demo` marks seeded
# rows so they are never confused with real production schedules.

SCHEDULE_SCOPES = ("user", "team", "org")


def _sched_out(r: dict) -> dict:
    import json as _json
    out = dict(r)
    for k in ("working_days", "holidays"):
        try:
            v = _json.loads(out.get(k) or "[]")
            out[k] = v if isinstance(v, list) else []
        except Exception:
            out[k] = []
    out["demo"] = bool(out.get("demo", 0))
    return out


def create_schedule(name: str, shift: str, start: str, end: str,
                    break_start: str, break_end: str, working_days: list,
                    holidays: list, scope_type: str, scope_id: str,
                    notes: str, created_by: str, demo: bool = False) -> dict:
    if scope_type not in SCHEDULE_SCOPES:
        raise ValueError("scope_type must be user|team|org")
    import json as _json
    now = _now()
    rec = {"id": _nid("sch"), "name": (name or "")[:120], "shift": (shift or "Morning")[:40],
           "start": (start or "09:00")[:8], "end": (end or "18:00")[:8],
           "break_start": (break_start or "")[:8], "break_end": (break_end or "")[:8],
           "working_days": _json.dumps([str(d)[:12] for d in (working_days or [])]),
           "holidays": _json.dumps([str(d)[:12] for d in (holidays or [])]),
           "scope_type": scope_type, "scope_id": (scope_id or "")[:64],
           "notes": (notes or "")[:500], "demo": 1 if demo else 0,
           "created_by": created_by or "", "created_at": now, "updated_at": now}
    init_db()
    con = _con()
    con.execute("INSERT INTO schedules(id,name,shift,start,end,break_start,break_end,"
                "working_days,holidays,scope_type,scope_id,notes,demo,created_by,"
                "created_at,updated_at) VALUES(:id,:name,:shift,:start,:end,:break_start,"
                ":break_end,:working_days,:holidays,:scope_type,:scope_id,:notes,:demo,"
                ":created_by,:created_at,:updated_at)", rec)
    con.commit()
    con.close()
    return _sched_out(rec)


def get_schedule_row(sid: str) -> dict | None:
    init_db()
    con = _con()
    r = _row(con, "SELECT * FROM schedules WHERE id=?", (sid,))
    con.close()
    return _sched_out(r) if r else None


def list_schedules(scope_types: list[str] | None = None,
                   scope_ids: list[str] | None = None) -> list[dict]:
    """scope_types/scope_ids filter (both None = all). scope_id match also
    includes org rows (scope_id='') so a team/user sees org-wide schedules."""
    init_db()
    con = _con()
    q, args = "SELECT * FROM schedules", []
    clauses = []
    if scope_types is not None:
        clauses.append(f"scope_type IN ({','.join('?' * len(scope_types))})")
        args.extend(scope_types)
    if scope_ids is not None:
        ids = [i for i in scope_ids if i]
        if ids:
            placeholders = ",".join("?" * len(ids))
            clauses.append(f"(scope_id IN ({placeholders}) OR scope_id='')")
            args.extend(ids)
        else:
            clauses.append("scope_id=''")
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    out = _all(con, q + " ORDER BY updated_at DESC", args)
    con.close()
    return [_sched_out(r) for r in out]


def update_schedule(sid: str, **fields) -> dict | None:
    import json as _json
    allowed = {"name", "shift", "start", "end", "break_start", "break_end",
               "working_days", "holidays", "scope_type", "scope_id", "notes"}
    updates = {}
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k in ("working_days", "holidays"):
            updates[k] = _json.dumps([str(d)[:12] for d in (v or [])])
        elif k == "scope_type":
            if v not in SCHEDULE_SCOPES:
                raise ValueError("scope_type must be user|team|org")
            updates[k] = v
        else:
            updates[k] = str(v)[:500]
    if not updates:
        return get_schedule_row(sid)
    init_db()
    con = _con()
    if not _row(con, "SELECT id FROM schedules WHERE id=?", (sid,)):
        con.close()
        return None
    updates["updated_at"] = _now()
    con.execute(f"UPDATE schedules SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                (*updates.values(), sid))
    con.commit()
    r = _row(con, "SELECT * FROM schedules WHERE id=?", (sid,))
    con.close()
    return _sched_out(r) if r else None


def delete_schedule(sid: str) -> bool:
    init_db()
    con = _con()
    cur = con.execute("DELETE FROM schedules WHERE id=?", (sid,))
    con.commit()
    n = cur.rowcount
    con.close()
    return n > 0


def has_schedules() -> bool:
    """True when at least one schedule row exists (idempotent seed guard)."""
    init_db()
    con = _con()
    r = con.execute("SELECT 1 FROM schedules LIMIT 1").fetchone()
    con.close()
    return r is not None
