"""Step 27 workspace API: employee self-service + manager team scope.

Routers: /api/v1/work/* (records), /api/v1/analytics/* (aggregates),
/api/v1/reports/* (generated reports). Every endpoint resolves identity,
role, permissions, and department server-side; ownership/team checks happen
BEFORE any data leaves. Audit is metadata-only (ids, counts, statuses).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, field_validator

from . import audit_log
from . import userstore
from . import work as store
from .auth_api import require_perm
from .rbac import has_permission

router = APIRouter(tags=["step27-work"])

MANAGER_ROLES = {"ADMIN", "MANAGER", "approving_manager"}
REVIEW_ROLES = {"ADMIN", "MANAGER", "REVIEWER", "approving_manager"}

PERF_WEIGHTS = {"attendance": 0.20, "completion": 0.25, "timeliness": 0.20,
                "team_contribution": 0.15, "quality": 0.10, "issue_resolution": 0.10}


# ---------------- scope helpers (server-side) ----------------

def _dept_of(user: dict) -> str:
    rec = userstore.find_by_id(user["id"])
    return (rec.get("department") or "") if rec else ""


def _team_ids(user: dict) -> list[str] | None:
    """None = global (ADMIN). Manager roles = same-department ids.
    Everyone else = [self]. security_admin/auditor get [] (no work data)."""
    if user["role"] == "ADMIN":
        return None
    if user["role"] in ("MANAGER", "approving_manager"):
        dept = _dept_of(user)
        ids = [u["id"] for u in userstore.list_users()
               if (u.get("department") or "") == dept and u["active"]]
        return ids or [user["id"]]
    if has_permission(user["role"], "WORK_READ"):
        return [user["id"]]
    return []


def _require_scope(user: dict) -> list[str] | None:
    ids = _team_ids(user)
    if ids == []:
        raise HTTPException(403, "this role has no workspace access")
    return ids


def _can_manage(user: dict) -> bool:
    return user["role"] in MANAGER_ROLES and has_permission(user["role"], "WORK_MANAGE")


def _can_review(user: dict) -> bool:
    return user["role"] in REVIEW_ROLES and has_permission(user["role"], "WORK_MANAGE")


def _in_scope_ids(scope: list[str] | None, uid: str) -> bool:
    return scope is None or uid in scope


def _audit(user: dict, action: str, resource: str = "", detail: str = "") -> None:
    audit_log.append(user["id"], user["role"], action, resource=resource, detail=detail)


# ---------------- models ----------------

class AttendanceIn(BaseModel):
    user_id: str | None = None
    date: str = ""
    status: str = "present"
    note: str = ""


class TaskIn(BaseModel):
    title: str
    instructions: str = ""
    assignee_id: str = ""
    due_date: str = ""
    start_date: str = ""
    priority: str = "medium"


class TaskPatch(BaseModel):
    status: str | None = None
    progress: int | None = None
    review_remarks: str | None = None


class EvidenceIn(BaseModel):
    task_id: str = ""
    note: str = ""


class WorksheetIn(BaseModel):
    title: str
    description: str = ""
    columns: list | None = None
    department: str = ""


class AssignIn(BaseModel):
    assignee_id: str


class SubmitIn(BaseModel):
    fields: dict | None = None
    evidence_doc_ids: list | None = None


class ReviewIn(BaseModel):
    status: str
    remarks: str = ""


class SheetAnalyzeIn(BaseModel):
    doc_id: str
    revenue_cols: list[str] = []
    expense_cols: list[str] = []
    group_by: str = ""


class ReportGenIn(BaseModel):
    report_type: str
    scope: str = "own"  # own | team | department
    scope_id: str = ""
    period_from: str = ""
    period_to: str = ""
    official_remarks: str = ""
    lang: str = "en"


class FeedbackIn(BaseModel):
    to_user_id: str
    task_id: str = ""
    text: str = ""
    rating: int | None = None

    @field_validator("rating")
    @classmethod
    def _rating(cls, v):
        if v is not None and (v < 1 or v > 5):
            raise ValueError("rating must be 1..5")
        return v


# ---------------- task display names ----------------

def _task_names(rows: list[dict]) -> list[dict]:
    """Attach assignee/creator display names server-side so the UI can show
    'assigned by' without exposing raw ids or requiring USER_READ. Only the
    display name leaves the server; no other profile fields are included."""
    cache: dict[str, str] = {}

    def name_of(uid: str) -> str:
        if not uid:
            return ""
        if uid not in cache:
            prof = userstore.get_profile(uid)
            if prof:
                cache[uid] = userstore.full_name_of(prof)
            else:
                rec = userstore.find_by_id(uid)
                cache[uid] = (rec.get("username") or "") if rec else ""
        return cache[uid]

    out: list[dict] = []
    for r in rows:
        rec = dict(r)
        rec["assignee_name"] = name_of(rec.get("assignee_id") or "")
        rec["created_by_name"] = name_of(rec.get("created_by") or "")
        out.append(rec)
    return out


# ---------------- employee: me/dashboard ----------------

@router.get("/api/v1/work/me")
def my_dashboard(user: dict = Depends(require_perm("WORK_READ"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    today = time.strftime("%Y-%m-%d")
    tasks = _task_names(store.list_tasks(assignee_ids=[user["id"]]))
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    att = store.list_attendance([user["id"]], date_from=today, date_to=today)
    ev = store.list_evidence(owner_ids=[user["id"]])
    fb = store.list_feedback(to_user_ids=[user["id"]])[:5]
    asg = store.list_assignments(assignee_ids=[user["id"]])
    reps = store.list_reports(creator_ids=[user["id"]])[:5]
    done = sum(1 for t in tasks if t["status"] in ("approved", "reviewed"))
    perf = _perf_for(user["id"], None)
    open_tasks = [t for t in tasks if t["status"] not in ("approved", "reviewed")]
    recent = sorted(tasks, key=lambda t: t.get("updated_at", 0), reverse=True)[:5]
    month_ago = time.time() - 30 * 86400
    done_30d = sum(1 for t in tasks if t["status"] in ("approved", "reviewed")
                   and (t.get("updated_at") or 0) >= month_ago)
    team_pct, team_name = None, ""
    dept = _dept_of(user)
    team_name = dept
    if dept:
        tids = [u["id"] for u in userstore.list_users() if u["active"]
                and (u.get("department") or "") == dept]
        tt = store.list_tasks(assignee_ids=tids or [user["id"]])
        td = sum(1 for t in tt if t["status"] in ("approved", "reviewed"))
        team_pct = round(100 * td / len(tt), 1) if tt else None
    lite = lambda t: {"id": t["id"], "title": t["title"], "status": t["status"],
                      "progress": t["progress"],
                      "priority": t.get("priority", "medium"),
                      "created_by_name": t.get("created_by_name", "")}
    return {"attendance_today": att[0] if att else None,
            "tasks_total": len(tasks), "tasks_by_status": by_status,
            "completion_pct": round(100 * done / len(tasks), 1) if tasks else None,
            "tasks_open": [lite(t) for t in open_tasks[:10]],
            "tasks_recent": [lite(t) for t in recent],
            "tasks_done_30d": done_30d,
            "team_completion_pct": team_pct, "team_name": team_name,
            "evidence_count": len(ev), "feedback_recent": fb,
            "worksheets_assigned": len(asg),
            "worksheets_pending": sum(1 for a in asg if a["status"] not in ("approved", "reviewed")),
            "reports_recent": [{"id": r["id"], "title": r["title"],
                                "report_type": r["report_type"]} for r in reps],
            "performance": perf}


# ---------------- attendance ----------------

@router.post("/api/v1/work/attendance")
def mark_attendance(b: AttendanceIn, user: dict = Depends(require_perm("ATTENDANCE_READ"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    target = b.user_id or user["id"]
    if target != user["id"]:
        if not (has_permission(user["role"], "ATTENDANCE_MANAGE")
                and user["role"] in (MANAGER_ROLES | {"ADMIN"})):
            raise HTTPException(403, "cannot mark attendance for others")
        scope = _require_scope(user)
        if not _in_scope_ids(scope, target):
            raise HTTPException(404, "employee not found")
    if not b.date:
        b.date = time.strftime("%Y-%m-%d")
    try:
        rec = store.mark_attendance(target, b.date, b.status, user["id"], b.note or "")
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(user, "ws_attendance", rec["id"], f"user={target} date={b.date} status={b.status}")
    return rec


@router.get("/api/v1/work/attendance")
def get_attendance(user_id: str = "", date_from: str = "", date_to: str = "",
                   user: dict = Depends(require_perm("ATTENDANCE_READ"))):
    scope = _require_scope(user)
    if user_id:
        if not _in_scope_ids(scope, user_id):
            raise HTTPException(404, "employee not found")
        ids = [user_id]
    else:
        ids = scope  # None (ADMIN) -> store returns [] ; expand below
        if ids is None:
            ids = [u["id"] for u in userstore.list_users() if u["active"]]
    rows = store.list_attendance(ids, date_from, date_to)
    _audit(user, "ws_analytics", "attendance", f"rows={len(rows)}")
    return {"rows": rows}


class MyWorkIn(BaseModel):
    title: str
    description: str = ""
    due_date: str = ""


@router.post("/api/v1/work/my-work")
def start_my_work(b: MyWorkIn, user: dict = Depends(require_perm("WORK_READ"))):
    """Employee self-service Start Work: creates a task owned by the caller
    (team-first model preserved — manager assignment is unchanged)."""
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    title = (b.title or "").strip()
    if not title:
        raise HTTPException(422, "work title is required")
    dept = _dept_of(user)
    try:
        rec = store.create_task(title[:200], (b.description or "")[:2000],
                                user["id"], user["id"], dept, (b.due_date or "")[:10])
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(user, "ws_task_create", rec["id"], f"self title={title[:80]}")
    return rec


# ---------------- schedule (org default + assigned roster) ----------------

def _schedule_payload() -> dict:
    import datetime as _dt
    from . import config as cfg
    days = cfg.SHIFT_WORKDAYS or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    weekly = [{"day": d, "start": cfg.SHIFT_START, "end": cfg.SHIFT_END} for d in days]
    today = _dt.date.today()
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
    nxt = today
    for _ in range(8):
        if names[nxt.weekday()] in days:
            break
        nxt += _dt.timedelta(days=1)
    return {"shift": cfg.SHIFT_NAME, "start": cfg.SHIFT_START, "end": cfg.SHIFT_END,
            "break": cfg.SHIFT_BREAK, "working_days": days, "weekly": weekly,
            "next_working_day": nxt.isoformat(), "today": today.isoformat(),
            "source": "org_default",
            "note": "Organization default schedule; no per-employee roster is stored."}


def _assigned_for(user: dict) -> dict | None:
    """Most specific assigned schedule for this user: user > team > org.
    Returns None when nothing is assigned (caller falls back to org default)."""
    dept = _dept_of(user)
    rows = store.list_schedules(scope_types=["user", "team", "org"],
                                scope_ids=[user["id"], dept])
    # Exact user match first, then team (department), then org-wide.
    for stype in ("user", "team", "org"):
        for r in rows:
            if r["scope_type"] != stype:
                continue
            if stype == "user" and r["scope_id"] == user["id"]:
                return r
            if stype == "team" and r["scope_id"] == dept and dept:
                return r
            if stype == "org":
                return r
    return None


def _sched_to_payload(s: dict) -> dict:
    """Shape an assigned schedule like the org-default payload so existing
    clients render it unchanged, plus roster fields + source labeling."""
    import datetime as _dt
    days = s.get("working_days") or []
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
    today = _dt.date.today()
    nxt = today
    for _ in range(8):
        if not days or names[nxt.weekday()] in days:
            break
        nxt += _dt.timedelta(days=1)
    weekly = [{"day": d,
               "start": s.get("start", ""),
               "end": s.get("end", "")} for d in days]
    brk = ""
    if s.get("break_start") or s.get("break_end"):
        brk = f"{s.get('break_start', '')}–{s.get('break_end', '')}"
    return {"shift": s.get("shift", ""), "start": s.get("start", ""),
            "end": s.get("end", ""), "break": brk, "working_days": days,
            "weekly": weekly, "next_working_day": nxt.isoformat(),
            "today": today.isoformat(), "source": "assigned",
            "scope": s.get("scope_type", ""), "name": s.get("name", ""),
            "notes": s.get("notes", ""), "holidays": s.get("holidays") or [],
            "demo": s.get("demo", False),
            "note": ("Demo schedule — seeded for the SIH prototype."
                     if s.get("demo") else "Assigned schedule.")}


@router.get("/api/v1/work/schedule")
def get_schedule(user: dict = Depends(require_perm("WORK_READ"))):
    assigned = _assigned_for(user)
    if assigned:
        return _sched_to_payload(assigned)
    return _schedule_payload()


# -------- schedule management (authorized Manager/Admin only) --------

class ScheduleIn(BaseModel):
    name: str = ""
    shift: str = "Morning"
    start: str = "09:00"
    end: str = "18:00"
    break_start: str = "13:00"
    break_end: str = "14:00"
    working_days: list[str] = []
    holidays: list[str] = []
    scope_type: str = "user"
    scope_id: str = ""
    notes: str = ""


def _require_schedule_manager(user: dict) -> None:
    if not (user["role"] in MANAGER_ROLES and has_permission(user["role"], "WORK_MANAGE")):
        raise HTTPException(403, "schedule management requires a manager/admin role")


def _check_scope_target(user: dict, scope_type: str, scope_id: str) -> None:
    """Server-side scope guard: manager may assign within their department;
    ADMIN may assign anywhere. team scope_id = department name."""
    if user["role"] == "ADMIN":
        return
    if scope_type == "org":
        raise HTTPException(403, "only admins may set an organization-wide schedule")
    if scope_type == "team":
        if scope_id != _dept_of(user):
            raise HTTPException(403, "team outside your department")
        return
    target = userstore.find_by_id(scope_id)
    if not target or not target["active"]:
        raise HTTPException(404, "employee not found")
    if (target.get("department") or "") != _dept_of(user):
        raise HTTPException(403, "employee outside your department")


@router.get("/api/v1/work/schedules")
def list_schedules_api(user: dict = Depends(require_perm("WORK_READ"))):
    """Assigned schedules visible to the caller (scoped server-side).
    Employees see their own; managers their department + org; admin all."""
    if user["role"] == "ADMIN":
        rows = store.list_schedules()
    elif user["role"] in MANAGER_ROLES:
        dept = _dept_of(user)
        # Every employee in the manager's department: per-employee rows they
        # created (scope_id = employee id) stay visible, alongside team rows
        # (scope_id = department) and the org-wide default (scope_id = '').
        member_ids = [u["id"] for u in userstore.list_users()
                      if (u.get("department") or "") == dept]
        rows = store.list_schedules(scope_ids=[user["id"], dept, *member_ids])
    else:
        # Employees: their own roster + team rows for their department +
        # the org-wide default.
        rows = store.list_schedules(scope_types=["user", "team", "org"],
                                    scope_ids=[user["id"], _dept_of(user)])
    _audit(user, "ws_analytics", "schedules", f"rows={len(rows)}")
    return {"schedules": rows}


@router.post("/api/v1/work/schedules")
def create_schedule_api(b: ScheduleIn, user: dict = Depends(require_perm("WORK_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    _require_schedule_manager(user)
    if b.scope_type not in ("user", "team", "org"):
        raise HTTPException(422, "scope_type must be user|team|org")
    if not b.scope_id and b.scope_type != "org":
        raise HTTPException(422, "scope_id is required for user/team scope")
    _check_scope_target(user, b.scope_type, b.scope_id)
    try:
        rec = store.create_schedule(b.name, b.shift, b.start, b.end, b.break_start,
                                    b.break_end, b.working_days, b.holidays,
                                    b.scope_type, b.scope_id, b.notes, user["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(user, "ws_schedule_create", rec["id"],
           f"scope={b.scope_type} id={b.scope_id[:12]} shift={b.shift}")
    return rec


@router.patch("/api/v1/work/schedules/{sid}")
def patch_schedule_api(sid: str, b: ScheduleIn,
                       user: dict = Depends(require_perm("WORK_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    _require_schedule_manager(user)
    cur = store.get_schedule_row(sid)
    if not cur:
        raise HTTPException(404, "schedule not found")
    if user["role"] != "ADMIN":
        # Manager may only edit schedules inside their own scope.
        dept = _dept_of(user)
        if cur["scope_type"] == "org":
            raise HTTPException(403, "only admins may edit an organization-wide schedule")
        if cur["scope_type"] == "team" and cur["scope_id"] != dept:
            raise HTTPException(403, "schedule outside your department")
        if cur["scope_type"] == "user":
            t = userstore.find_by_id(cur["scope_id"]) or {}
            if (t.get("department") or "") != dept:
                raise HTTPException(403, "schedule outside your department")
        if b.scope_type != cur["scope_type"] or b.scope_id != cur["scope_id"]:
            raise HTTPException(403, "moving a schedule to another scope requires an admin")
        _check_scope_target(user, b.scope_type, b.scope_id)
    try:
        rec = store.update_schedule(sid, **b.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not rec:
        raise HTTPException(404, "schedule not found")
    _audit(user, "ws_schedule_update", sid, f"shift={rec.get('shift', '')}")
    return rec


@router.delete("/api/v1/work/schedules/{sid}")
def delete_schedule_api(sid: str, user: dict = Depends(require_perm("WORK_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    _require_schedule_manager(user)
    cur = store.get_schedule_row(sid)
    if not cur:
        raise HTTPException(404, "schedule not found")
    if user["role"] != "ADMIN":
        dept = _dept_of(user)
        if cur["scope_type"] == "org":
            raise HTTPException(403, "only admins may delete an organization-wide schedule")
        if cur["scope_type"] == "team" and cur["scope_id"] != dept:
            raise HTTPException(403, "schedule outside your department")
        if cur["scope_type"] == "user":
            t = userstore.find_by_id(cur["scope_id"]) or {}
            if (t.get("department") or "") != dept:
                raise HTTPException(403, "schedule outside your department")
    store.delete_schedule(sid)
    _audit(user, "ws_schedule_delete", sid, f"scope={cur['scope_type']}")
    return {"ok": True}


# ---------------- attendance summary (recorded data only) ----------------

def _month_bounds(which: str) -> tuple[str, str, str]:
    import datetime as _dt
    import re
    today = _dt.date.today()
    if which in ("", "current"):
        y, m = today.year, today.month
    elif which == "previous":
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    elif re.fullmatch(r"\d{4}-\d{2}", which or ""):
        y, m = int(which[:4]), int(which[5:7])
    else:
        from fastapi import HTTPException as _H
        raise _H(422, "month must be current|previous|YYYY-MM")
    first = _dt.date(y, m, 1)
    last = first
    while last.month == m:
        last += _dt.timedelta(days=1)
    last -= _dt.timedelta(days=1)
    end = min(last, today) if (y, m) == (today.year, today.month) else last
    return first.isoformat(), end.isoformat(), f"{y:04d}-{m:02d}"


def _summarize(uid: str, month: str) -> dict:
    import datetime as _dt
    first, end, label = _month_bounds(month)
    rows = store.list_attendance([uid], first, end)
    by_date = {r["date"]: r["status"] for r in rows}
    d = _dt.date.fromisoformat(first)
    last = _dt.date.fromisoformat(end)
    working, present, absent, late = 0, 0, 0, 0
    while d <= last:
        if d.weekday() < 5:
            working += 1
            st = by_date.get(d.isoformat(), "")
            if st == "present":
                present += 1
            elif st == "absent":
                absent += 1
            elif st == "late":
                late += 1
        d += _dt.timedelta(days=1)
    today = _dt.date.today().isoformat()
    pct = round(100 * present / working, 1) if working else None
    return {"month": label, "present": present, "absent": absent, "late": late,
            "working_days": working, "attendance_pct": pct,
            "today_status": by_date.get(today),
            "has_records": bool(rows),
            "note": "Working days count Monday–Friday; unmarked days are shown as unmarked, never assumed."}


@router.get("/api/v1/work/attendance/summary")
def attendance_summary(month: str = "current", user_id: str = "",
                       user: dict = Depends(require_perm("ATTENDANCE_READ"))):
    scope = _require_scope(user)
    target = user_id or user["id"]
    if not _in_scope_ids(scope, target):
        raise HTTPException(404, "employee not found")
    out = {"current": _summarize(target, month if month not in ("current", "previous") else month)}
    if month in ("current", "previous", ""):
        out["previous"] = _summarize(target, "previous")
    _audit(user, "ws_analytics", "attendance_summary", f"user={target} month={month}")
    return out


# ---------------- recent activity (own scope only) ----------------

@router.get("/api/v1/work/activity")
def recent_activity(user_id: str = "",
                    user: dict = Depends(require_perm("WORK_READ"))):
    from . import conversations as convos
    scope = _require_scope(user)
    target = user_id or user["id"]
    if not _in_scope_ids(scope, target):
        raise HTTPException(404, "employee not found")
    items: list[dict] = []
    for t in sorted(store.list_tasks(assignee_ids=[target]),
                    key=lambda x: x.get("updated_at", 0), reverse=True)[:5]:
        items.append({"type": "work", "text": f"Work '{t['title']}' is {t['status']}",
                      "ts": t.get("updated_at", 0)})
    for e in store.list_evidence(owner_ids=[target])[:5]:
        items.append({"type": "evidence", "text": f"Evidence added{((' for task ' + e['task_id'][:8]) if e.get('task_id') else '')}",
                      "ts": e.get("created_at", 0)})
    for a in store.list_attendance([target])[:5]:
        items.append({"type": "attendance",
                      "text": f"Attendance {a['status']} on {a['date']}", "ts": a.get("created_at", 0)})
    for r in store.list_reports(creator_ids=[target])[:5]:
        items.append({"type": "report", "text": f"Report '{r['title']}' generated",
                      "ts": r.get("created_at", 0)})
    for f in store.list_feedback(to_user_ids=[target])[:3]:
        items.append({"type": "review", "text": "Manager review received",
                      "ts": f.get("created_at", 0)})
    if target == user["id"]:
        for c in convos.list_conversations(target, limit=5):
            items.append({"type": "ai", "text": f"AI question: {(c.get('title') or '')[:60]}",
                          "ts": c.get("created_at", 0)})
    items.sort(key=lambda x: x.get("ts", 0) or 0, reverse=True)
    _audit(user, "ws_analytics", "activity", f"user={target} items={len(items)}")
    return {"items": items[:20]}


# ---------------- tasks ----------------

@router.post("/api/v1/work/tasks")
def create_task(b: TaskIn, user: dict = Depends(require_perm("WORK_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    if user["role"] not in MANAGER_ROLES:
        raise HTTPException(403, "only managers/admins assign work")
    target = userstore.find_by_id(b.assignee_id)
    if not target or not target["active"]:
        raise HTTPException(404, "assignee not found")
    scope = _require_scope(user)
    if not _in_scope_ids(scope, b.assignee_id):
        raise HTTPException(403, "assignee outside your department")
    dept = (target.get("department") or "")
    try:
        rec = store.create_task(b.title, b.instructions, b.assignee_id, user["id"], dept,
                                b.due_date or "", b.priority or "medium",
                                b.start_date or "")
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(user, "ws_task_create", rec["id"], f"assignee={b.assignee_id}")
    return rec


@router.get("/api/v1/work/tasks")
def list_tasks(status: str = "", user_id: str = "",
               user: dict = Depends(require_perm("WORK_READ"))):
    scope = _require_scope(user)
    if user_id:
        if not _in_scope_ids(scope, user_id):
            raise HTTPException(404, "employee not found")
        ids: list[str] | None = [user_id]
    else:
        ids = scope
        if ids is None:
            ids = None  # ADMIN global
    try:
        rows = store.list_tasks(assignee_ids=ids, status=status)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"tasks": _task_names(rows)}


@router.patch("/api/v1/work/tasks/{tid}")
def patch_task(tid: str, b: TaskPatch, user: dict = Depends(require_perm("WORK_READ"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    t = store.get_task(tid)
    if not t:
        raise HTTPException(404, "task not found")
    scope = _require_scope(user)
    if not _in_scope_ids(scope, t["assignee_id"]) and t["created_by"] != user["id"]:
        raise HTTPException(404, "task not found")
    updates: dict = {}
    if t["assignee_id"] == user["id"]:
        # Owner lane: progress + worker statuses only.
        if b.status is not None:
            if b.status not in ("in_progress", "submitted", "blocked"):
                raise HTTPException(403, "status transition not permitted for assignee")
            updates["status"] = b.status
        if b.progress is not None:
            updates["progress"] = b.progress
        if b.review_remarks is not None:
            raise HTTPException(403, "only reviewers add remarks")
    elif _can_review(user):
        if b.status is not None:
            if b.status not in ("needs_correction", "reviewed", "approved", "blocked"):
                raise HTTPException(403, "status transition not permitted for reviewer")
            updates["status"] = b.status
            updates["reviewed_by"] = user["id"]
        if b.review_remarks is not None:
            updates["review_remarks"] = (b.review_remarks or "")[:2000]
            updates.setdefault("reviewed_by", user["id"])
        if b.progress is not None:
            raise HTTPException(403, "only the assignee updates progress")
    else:
        raise HTTPException(403, "not your task")
    try:
        rec = store.update_task(tid, **updates)
    except ValueError as e:
        raise HTTPException(422, str(e))
    action = "ws_task_review" if updates.get("reviewed_by") else "ws_task_update"
    _audit(user, action, tid, f"status={rec['status'] if rec else '?'}")
    return rec


# ---------------- evidence (reuses secure docs pipeline) ----------------

@router.post("/api/v1/work/evidence")
async def add_evidence(f: UploadFile | None = None, task_id: str = "",
                       note: str = "", user: dict = Depends(require_perm("WORK_READ"))):
    from . import ratelimit
    from . import docs_api
    from . import docs_store
    ratelimit.check("upload", user["id"], user)
    if task_id:
        t = store.get_task(task_id)
        if not t:
            raise HTTPException(404, "task not found")
        scope = _require_scope(user)
        if t["assignee_id"] != user["id"] and not _in_scope_ids(scope, t["assignee_id"]):
            raise HTTPException(404, "task not found")
    doc_id = ""
    if f is not None:
        data = await docs_api._read_capped(f)
        kind, ext = docs_api._classify(f.filename or "evidence", data)
        import os as _os
        import uuid as _uuid
        _os.makedirs(docs_api.UPLOAD_DIR, exist_ok=True)
        stored = f"d-{_uuid.uuid4().hex[:12]}{ext}"
        with open(_os.path.join(docs_api.UPLOAD_DIR, stored), "wb") as fh:
            fh.write(data)
        doc = docs_store.create(user["id"], user["username"],
                                docs_api._safe_display_name(f.filename or "evidence"),
                                stored, docs_api.MIME[kind] if kind != "image"
                                else ("image/png" if ext == ".png" else "image/jpeg"),
                                ext, kind, len(data))
        doc_id = doc["id"]
    rec = store.add_evidence(task_id or "", user["id"], doc_id, note or "")
    _audit(user, "ws_evidence", rec["id"], f"task={task_id or '-'} doc={doc_id or '-'}")
    return {**rec, "doc_id": doc_id}


@router.get("/api/v1/work/evidence")
def list_evidence(task_id: str = "", user_id: str = "",
                  user: dict = Depends(require_perm("WORK_READ"))):
    scope = _require_scope(user)
    if user_id and not _in_scope_ids(scope, user_id):
        raise HTTPException(404, "employee not found")
    ids = [user_id] if user_id else scope
    if ids is None:
        ids = None
    rows = store.list_evidence(owner_ids=ids, task_id=task_id)
    if task_id:
        t = store.get_task(task_id)
        if t and not _in_scope_ids(scope, t["assignee_id"]) and t["assignee_id"] != user["id"] \
                and t["created_by"] != user["id"]:
            raise HTTPException(404, "task not found")
    return {"evidence": rows}


# ---------------- worksheets ----------------

@router.post("/api/v1/work/worksheets")
def create_worksheet(b: WorksheetIn, user: dict = Depends(require_perm("WORKSHEET_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    if user["role"] not in MANAGER_ROLES:
        raise HTTPException(403, "only managers/admins create worksheets")
    dept = b.department or _dept_of(user)
    if user["role"] != "ADMIN" and dept != _dept_of(user):
        raise HTTPException(403, "worksheet department outside your scope")
    try:
        rec = store.create_worksheet(b.title, b.description, b.columns or [], "",
                                     user["id"], dept)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(user, "ws_worksheet_create", rec["id"], f"dept={dept}")
    return {**rec, "columns": _cols(rec)}


def _cols(rec: dict) -> list:
    import json as _json
    try:
        return _json.loads(rec.get("columns_json") or "[]")
    except Exception:
        return []


@router.post("/api/v1/work/worksheets/upload")
async def upload_worksheet(f: UploadFile, title: str = "", description: str = "",
                           department: str = "",
                           user: dict = Depends(require_perm("WORKSHEET_MANAGE"))):
    """CSV/XLSX -> inspected structure -> worksheet shell (same secure upload path)."""
    from . import ratelimit
    from . import docs_api
    from . import docs_store
    from . import spreadsheet as sheet
    if user["role"] not in MANAGER_ROLES:
        raise HTTPException(403, "only managers/admins create worksheets")
    ratelimit.check("upload", user["id"], user)
    data = await docs_api._read_capped(f)
    kind, ext = docs_api._classify(f.filename or "sheet", data)
    if kind not in ("csv", "xlsx"):
        raise HTTPException(415, "worksheet upload must be CSV or XLSX")
    try:
        info = sheet.inspect(kind, data)
    except sheet.SheetError as e:
        raise HTTPException(422, f"spreadsheet unreadable: {e}")
    first = info["sheets"][0]
    cols = [{"name": c["name"], "type": c["type"]} for c in first["columns"]]
    import os as _os
    import uuid as _uuid
    _os.makedirs(docs_api.UPLOAD_DIR, exist_ok=True)
    stored = f"d-{_uuid.uuid4().hex[:12]}{ext}"
    with open(_os.path.join(docs_api.UPLOAD_DIR, stored), "wb") as fh:
        fh.write(data)
    doc = docs_store.create(user["id"], user["username"],
                            docs_api._safe_display_name(f.filename or "sheet"),
                            stored, docs_api.MIME[kind], ext, kind, len(data))
    dept = department or _dept_of(user)
    if user["role"] != "ADMIN" and dept != _dept_of(user):
        raise HTTPException(403, "worksheet department outside your scope")
    rec = store.create_worksheet(title or doc["filename"], description, cols, doc["id"],
                                 user["id"], dept)
    _audit(user, "ws_worksheet_create", rec["id"],
           f"from={doc['id']} rows={first['rows']} cols={len(cols)}")
    return {**rec, "columns": cols, "source_doc_id": doc["id"],
            "structure": {k: v for k, v in first.items()}}


@router.get("/api/v1/work/worksheets")
def list_worksheets(user: dict = Depends(require_perm("WORKSHEET_READ"))):
    scope = _require_scope(user)
    if scope is None:  # ADMIN global
        rows = store.list_worksheets()
    elif user["role"] in MANAGER_ROLES:
        depts = {(_dept_of(user))}
        rows = store.list_worksheets(departments=sorted(d for d in depts if d is not None))
        # managers also see worksheets they created outside dept? No — dept scoped.
    else:
        mine = [a["worksheet_id"] for a in store.list_assignments(assignee_ids=[user["id"]])]
        rows = [w for w in store.list_worksheets() if w["id"] in mine
                or w["department"] == _dept_of(user)]
    return {"worksheets": [{**w, "columns": _cols(w)} for w in rows]}


@router.post("/api/v1/work/worksheets/{wid}/assign")
def assign_worksheet(wid: str, b: AssignIn, user: dict = Depends(require_perm("WORKSHEET_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    if user["role"] not in MANAGER_ROLES:
        raise HTTPException(403, "only managers/admins assign worksheets")
    w = store.get_worksheet(wid)
    if not w:
        raise HTTPException(404, "worksheet not found")
    if user["role"] != "ADMIN" and w["department"] != _dept_of(user):
        raise HTTPException(404, "worksheet not found")
    target = userstore.find_by_id(b.assignee_id)
    if not target or not target["active"]:
        raise HTTPException(404, "assignee not found")
    if user["role"] != "ADMIN" and (target.get("department") or "") != _dept_of(user):
        raise HTTPException(403, "assignee outside your department")
    rec = store.assign_worksheet(wid, b.assignee_id)
    _audit(user, "ws_worksheet_assign", rec["id"], f"ws={wid} to={b.assignee_id}")
    return rec


@router.get("/api/v1/work/worksheets/assigned")
def my_assignments(user: dict = Depends(require_perm("WORKSHEET_READ"))):
    rows = store.list_assignments(assignee_ids=[user["id"]])
    out = []
    for a in rows:
        w = store.get_worksheet(a["worksheet_id"]) or {}
        out.append({**a, "worksheet_title": w.get("title", ""),
                    "worksheet_columns": _cols(w) if w else []})
    return {"assignments": out}


@router.patch("/api/v1/work/worksheets/assignments/{aid}/submit")
def submit_assignment(aid: str, b: SubmitIn, user: dict = Depends(require_perm("WORKSHEET_READ"))):
    from . import ratelimit
    import json as _json
    ratelimit.check("work", user["id"], user)
    a = store.get_assignment(aid)
    if not a or a["assignee_id"] != user["id"]:
        raise HTTPException(404, "assignment not found")
    if a["status"] in ("approved", "reviewed"):
        raise HTTPException(409, "already reviewed; contact your manager")
    w = store.get_worksheet(a["worksheet_id"]) or {}
    allowed = {c["name"] for c in _cols(w)}
    fields = b.fields or {}
    if not isinstance(fields, dict):
        raise HTTPException(422, "fields must be an object")
    unknown = [k for k in fields if k not in allowed] if allowed else []
    if unknown:
        raise HTTPException(422, f"fields outside worksheet schema: {unknown[:5]}")
    doc_ids = b.evidence_doc_ids or []
    if not isinstance(doc_ids, list) or len(doc_ids) > 20:
        raise HTTPException(422, "evidence_doc_ids must be a list of <=20 ids")
    from . import docs_store
    for did in doc_ids:
        d = docs_store.get(str(did))
        if not d or d["owner_id"] != user["id"]:
            raise HTTPException(404, "evidence document not found")
    rec = store.update_assignment(aid, status="submitted",
                                  fields_json=_json.dumps({k: str(v)[:500] for k, v in fields.items()}),
                                  evidence_doc_ids=_json.dumps([str(d) for d in doc_ids]),
                                  submitted_at=time.time())
    _audit(user, "ws_worksheet_submit", aid, f"fields={len(fields)} evidence={len(doc_ids)}")
    return rec


@router.patch("/api/v1/work/worksheets/assignments/{aid}/review")
def review_assignment(aid: str, b: ReviewIn, user: dict = Depends(require_perm("WORK_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    if not _can_review(user):
        raise HTTPException(403, "review requires a reviewer/manager role")
    a = store.get_assignment(aid)
    if not a:
        raise HTTPException(404, "assignment not found")
    scope = _require_scope(user)
    if not _in_scope_ids(scope, a["assignee_id"]):
        raise HTTPException(404, "assignment not found")
    if b.status not in ("reviewed", "approved", "needs_correction"):
        raise HTTPException(422, "invalid review status")
    rec = store.update_assignment(aid, status=b.status,
                                  review_remarks=(b.remarks or "")[:2000],
                                  reviewed_by=user["id"])
    _audit(user, "ws_worksheet_review", aid, f"status={b.status}")
    return rec


# ---------------- spreadsheet analysis ----------------

@router.post("/api/v1/work/spreadsheets/analyze")
def analyze_sheet(b: SheetAnalyzeIn, user: dict = Depends(require_perm("DOCUMENT_READ"))):
    """Computed metrics over an OWNED spreadsheet doc. Ownership verified via
    the docs layer (owner or ADMIN); managers cannot reach into another
    user's private documents — team insight comes from work records."""
    from . import ratelimit
    from . import docs_api
    from . import spreadsheet as sheet
    ratelimit.check("analytics", user["id"], user)
    doc = docs_api._get_owned(b.doc_id, user)
    if doc["kind"] not in ("csv", "xlsx"):
        raise HTTPException(422, "document is not a spreadsheet")
    with open(docs_api._stored_path(doc), "rb") as fh:
        data = fh.read()
    try:
        info = sheet.inspect(doc["kind"], data)
        headers, rows = sheet.raw_rows(doc["kind"], data)
        agg = sheet.aggregate_rows(headers, rows,
                                   [c[:80] for c in (b.revenue_cols or [])[:20]],
                                   [c[:80] for c in (b.expense_cols or [])[:20]],
                                   (b.group_by or "")[:80])
    except sheet.SheetError as e:
        raise HTTPException(422, f"spreadsheet unreadable: {e}")
    has_numbers = agg["totals"]["rows_seen"] > 0 and (
        agg["totals"]["revenue"] != 0 or agg["totals"]["expenses"] != 0
        or not agg["missing_columns"])
    out = {"doc_id": doc["id"], "filename": doc["filename"],
           "structure": info["sheets"][0], "truncated": info["truncated"],
           "metrics": agg["totals"], "groups": agg["groups"],
           "missing_columns": agg["missing_columns"],
           "skipped_cells": agg["totals"]["skipped_cells"],
           "verdict": ("calculated from data" if has_numbers and not agg["missing_columns"]
                       else "insufficient data to calculate this metric")}
    _audit(user, "ws_spreadsheet", doc["id"],
           f"rows={info['sheets'][0]['rows']} missing={len(agg['missing_columns'])}")
    return out


# ---------------- analytics ----------------

def _perf_for(uid: str, weights: dict | None) -> dict:
    """Transparent deterministic scoring. Missing inputs -> null + flags
    (never guessed). Weights served verbatim at /analytics/criteria.
    Observable work data only — never personality, health, or protected traits."""
    w = weights or PERF_WEIGHTS
    att = store.list_attendance([uid])
    present = sum(1 for a in att if a["status"] == "present")
    attendance = round(present / len(att), 3) if att else None
    tasks = store.list_tasks(assignee_ids=[uid])
    done = [t for t in tasks if t["status"] in ("approved", "reviewed")]
    completion = round(len(done) / len(tasks), 3) if tasks else None
    due = [t for t in tasks if t.get("due_date")]
    on_time = [t for t in due if t["status"] in ("approved", "reviewed")]
    timeliness = round(len(on_time) / len(due), 3) if due else None
    asg = store.list_assignments(assignee_ids=[uid])
    asg_done = [a for a in asg if a["status"] in ("approved", "reviewed")]
    team_contribution = (round(len(asg_done) / len(asg), 3) if asg else None)
    reviewed = [t for t in tasks if t["status"] in ("reviewed", "approved", "needs_correction")]
    quality = (round(sum(1 for t in reviewed if t["status"] in ("reviewed", "approved"))
                     / len(reviewed), 3) if reviewed else None)
    flagged = [t for t in tasks if t["status"] in ("blocked", "needs_correction")]
    issue_resolution = (round(1 - len(flagged) / len(tasks), 3) if tasks else None)
    parts = {"attendance": attendance, "completion": completion, "timeliness": timeliness,
             "team_contribution": team_contribution, "quality": quality,
             "issue_resolution": issue_resolution}
    avail = {k: v for k, v in parts.items() if v is not None}
    denom = sum(w[k] for k in avail)
    overall = round(sum(v * w[k] for k, v in avail.items()) / denom, 3) if denom else None
    missing = [k for k, v in parts.items() if v is None]
    return {"parts": parts, "overall": overall,
            "basis": "AI-assisted analysis; assessment from configured criteria",
            "missing": missing,
            "note": ("insufficient data: " + ",".join(missing)) if missing else "complete inputs"}


@router.get("/api/v1/analytics/criteria")
def perf_criteria(user: dict = Depends(require_perm("WORK_READ"))):
    return {"weights": PERF_WEIGHTS,
            "parts": {
                "attendance": "share of attendance days marked present",
                "completion": "share of assigned tasks approved/reviewed",
                "timeliness": "share of due-dated tasks completed (approved/reviewed)",
                "team_contribution": "share of worksheet assignments approved/reviewed",
                "quality": "share of reviewed items approved (vs needs_correction)",
                "issue_resolution": "1 minus share of tasks currently blocked/needs_correction",
            },
            "basis": "AI-assisted analysis; performance assessment based on configured criteria. "
                     "Managers remain responsible for reviewing underlying evidence; "
                     "the system never makes employment decisions."}


def _financial_summary(user: dict) -> dict | None:
    """DEMO financial tiles for the overview: sum revenue/expense columns
    from DEMO_-marked owned spreadsheets using the exact column names the
    demo schema ships (month,revenue,expense). Only demo-labelled files are
    surfaced so unlabeled operational data is never presented as financial
    truth; any permission/parse problem returns None (the UI then shows
    "insufficient data" — never guessed numbers)."""
    from . import docs_api
    from . import docs_store
    from . import spreadsheet as sheet
    try:
        scope = None if docs_api._is_admin(user) else user["id"]
        # Cap by spreadsheet kind BEFORE any slice: an admin's list includes
        # every user's DEMO files (txt/docx first), and slicing first would
        # starve the scan. Stop after 3 usable datasets.
        candidates = [d for d in docs_store.list_for(scope)
                      if str(d.get("filename", "")).startswith("DEMO_")
                      and d.get("kind") in ("csv", "xlsx")][:20]
        rev = exp = 0.0
        used = []
        seen = set()  # mirrored seed files (CSV + XLSX of the same rows)
        for d in candidates:
            if len(used) >= 3:
                break
            full = docs_store.get(d["id"]) or {}
            if full.get("kind") not in ("csv", "xlsx"):
                continue
            with open(docs_api._stored_path(full), "rb") as fh:
                data = fh.read()
            try:
                headers, rows = sheet.raw_rows(full["kind"], data)
            except sheet.SheetError:
                continue
            if "revenue" not in headers or "expense" not in headers:
                continue
            agg = sheet.aggregate_rows(headers, rows, ["revenue"], ["expense"])
            if agg["missing_columns"] or not agg["totals"]["rows_seen"]:
                continue
            key = (agg["totals"]["revenue"], agg["totals"]["expenses"],
                   agg["totals"]["rows_seen"])
            if key in seen:
                continue  # same dataset shipped twice -> counted once
            seen.add(key)
            rev += agg["totals"]["revenue"]
            exp += agg["totals"]["expenses"]
            used.append(full["filename"])
        if not used:
            return None
        return {"revenue": round(rev, 2), "expenses": round(exp, 2),
                "profit": round(rev - exp, 2), "demo": True, "docs": used,
                "note": "DEMO dataset — sample figures for demonstration only."}
    except Exception:
        return None  # fail-closed: a tile must never break the overview


@router.get("/api/v1/analytics/overview")
def team_overview(date_from: str = "", date_to: str = "",
                  user: dict = Depends(require_perm("ANALYTICS_READ"))):
    from . import ratelimit
    ratelimit.check("analytics", user["id"], user)
    scope = _require_scope(user)
    uids = scope if scope is not None else [u["id"] for u in userstore.list_users() if u["active"]]
    att = store.list_attendance(uids, date_from, date_to)
    present = sum(1 for a in att if a["status"] == "present")
    tasks = store.list_tasks(assignee_ids=uids)
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    done = sum(1 for t in tasks if t["status"] in ("approved", "reviewed"))
    ev = store.list_evidence(owner_ids=uids)
    per_user = []
    for uid in uids:
        ut = [t for t in tasks if t["assignee_id"] == uid]
        ud = sum(1 for t in ut if t["status"] in ("approved", "reviewed"))
        ua = [a for a in att if a["user_id"] == uid]
        up = sum(1 for a in ua if a["status"] == "present")
        per_user.append({"user_id": uid, "tasks": len(ut), "completed": ud,
                         "completion_pct": round(100 * ud / len(ut), 1) if ut else None,
                         "attendance_days": len(ua), "present_days": up,
                         "evidence": sum(1 for e in ev if e["owner_id"] == uid)})
    out = {"employees": len(uids),
           "attendance_rate": round(present / len(att), 3) if att else None,
           "tasks_total": len(tasks), "tasks_by_status": by_status,
           "completion_pct": round(100 * done / len(tasks), 1) if tasks else None,
           "evidence_total": len(ev), "per_user": per_user,
           "incomplete": [] if att and tasks else ["attendance" if not att else "",
                                                   "tasks" if not tasks else ""]}
    out["incomplete"] = [x for x in out["incomplete"] if x]
    today = time.strftime("%Y-%m-%d")
    today_rows = [a for a in att if a["date"] == today]
    present_ids = {a["user_id"] for a in today_rows if a["status"] == "present"}
    late_ids = {a["user_id"] for a in today_rows if a["status"] == "late"}
    out["present_today"] = len(present_ids)
    out["late_today"] = len(late_ids)
    out["absent_today"] = len([u for u in uids if u not in present_ids | late_ids])
    out["active_teams"] = len({(userstore.find_by_id(u) or {}).get("department", "") or "—"
                               for u in uids})
    out["work_in_progress"] = sum(1 for t in tasks if t["status"] in ("in_progress", "submitted"))
    out["completed_work"] = done
    out["pending_review"] = sum(1 for t in tasks if t["status"] in ("submitted", "needs_correction"))
    out["blocked_count"] = by_status.get("blocked", 0)
    out["avg_performance"] = _avg_perf(uids)
    out["pending_items"] = [{"id": t["id"], "title": t["title"], "status": t["status"],
                             "assignee": (userstore.find_by_id(t["assignee_id"]) or {})
                             .get("username", t["assignee_id"])} for t in tasks
                            if t["status"] in ("submitted", "needs_correction")][:20]
    fin = _financial_summary(user)
    out["financial"] = fin  # DEMO-labelled dataset only; None -> "insufficient data"
    _audit(user, "ws_analytics", "overview",
           f"employees={len(uids)} tasks={len(tasks)}"
           + (f" financial_docs={len(fin['docs'])}" if fin else ""))
    return out


@router.get("/api/v1/analytics/employee/{uid}")
def employee_detail(uid: str, date_from: str = "", date_to: str = "",
                    user: dict = Depends(require_perm("WORK_READ"))):
    from . import ratelimit
    ratelimit.check("analytics", user["id"], user)
    scope = _require_scope(user)
    if not _in_scope_ids(scope, uid):
        raise HTTPException(404, "employee not found")
    if uid != user["id"] and not (has_permission(user["role"], "ANALYTICS_READ")
                                  or user["role"] == "ADMIN"):
        raise HTTPException(403, "manager analytics required")
    att = store.list_attendance([uid], date_from, date_to)
    tasks = store.list_tasks(assignee_ids=[uid])
    ev = store.list_evidence(owner_ids=[uid])
    fb = store.list_feedback(to_user_ids=[uid])[:20]
    asg = store.list_assignments(assignee_ids=[uid])
    perf = _perf_for(uid, None)
    blocked = [t for t in tasks if t["status"] == "blocked"]
    out = {"user_id": uid, "attendance": att, "tasks": tasks,
           "evidence": ev, "feedback": fb, "assignments": asg,
           "performance": perf, "blockers": blocked}
    _audit(user, "ws_analytics", f"employee:{uid}",
           f"tasks={len(tasks)} att={len(att)} ev={len(ev)}")
    return out


@router.get("/api/v1/analytics/trends")
def trends(date_from: str = "", date_to: str = "", bucket: str = "week",
           user: dict = Depends(require_perm("ANALYTICS_READ"))):
    from . import ratelimit
    import datetime as _dt
    ratelimit.check("analytics", user["id"], user)
    if bucket not in ("day", "week", "month"):
        raise HTTPException(422, "bucket must be day|week|month")
    scope = _require_scope(user)
    uids = scope if scope is not None else [u["id"] for u in userstore.list_users() if u["active"]]
    tasks = store.list_tasks(assignee_ids=uids)
    att = store.list_attendance(uids, date_from, date_to)

    def _key(ts: float) -> str:
        d = _dt.datetime.utcfromtimestamp(ts)
        if bucket == "day":
            return d.strftime("%Y-%m-%d")
        if bucket == "week":
            return d.strftime("%Y-W%V")
        return d.strftime("%Y-%m")

    series: dict[str, dict] = {}
    for t in tasks:
        k = _key(t["created_at"])
        s = series.setdefault(k, {"tasks": 0, "completed": 0, "present": 0, "attendance": 0})
        s["tasks"] += 1
        if t["status"] in ("approved", "reviewed"):
            s["completed"] += 1
    for a in att:
        k = a["date"][:7] if bucket == "month" else (a["date"] if bucket == "day"
             else _dt.datetime.strptime(a["date"], "%Y-%m-%d").strftime("%Y-W%V"))
        s = series.setdefault(k, {"tasks": 0, "completed": 0, "present": 0, "attendance": 0})
        s["attendance"] += 1
        if a["status"] == "present":
            s["present"] += 1
    pts = [{"bucket": k, **v} for k, v in sorted(series.items())]
    _audit(user, "ws_analytics", "trends", f"buckets={len(pts)} bucket={bucket}")
    return {"bucket": bucket, "points": pts,
            "coverage_note": "buckets with no records are omitted (labeled by absence, never interpolated)"}


@router.get("/api/v1/analytics/financial")
def financial(doc_ids: str = "", revenue_cols: str = "", expense_cols: str = "",
              group_by: str = "", period: str = "monthly",
              user: dict = Depends(require_perm("ANALYTICS_READ"))):
    """Sum revenue/expense columns across OWNED spreadsheet docs only.
    Every doc passes docs_api._get_owned (owner or ADMIN) first; unknown
    columns are reported, never guessed; empty input -> insufficient-data."""
    from . import ratelimit
    from . import docs_api
    from . import docs_store
    from . import spreadsheet as sheet
    import os as _os
    ratelimit.check("analytics", user["id"], user)
    if period not in ("daily", "weekly", "monthly", "quarterly", "custom"):
        raise HTTPException(422, "invalid period")
    ids = [d.strip() for d in (doc_ids or "").split(",") if d.strip()][:10]
    if not ids:
        return {"totals": None, "groups": {}, "docs": [],
                "verdict": "insufficient data to calculate this metric"}
    rev = [c.strip()[:80] for c in revenue_cols.split(",") if c.strip()][:20]
    exp = [c.strip()[:80] for c in expense_cols.split(",") if c.strip()][:20]
    totals = {"revenue": 0.0, "expenses": 0.0, "profit": 0.0}
    groups: dict[str, dict] = {}
    docs_out, missing, skipped = [], set(), 0
    for did in ids:
        doc = docs_api._get_owned(did, user)  # 404 for foreign ids
        if doc["kind"] not in ("csv", "xlsx"):
            continue
        with open(docs_api._stored_path(doc), "rb") as fh:
            data = fh.read()
        try:
            headers, rows = sheet.raw_rows(doc["kind"], data)
            agg = sheet.aggregate_rows(headers, rows, rev, exp, (group_by or "")[:80])
        except sheet.SheetError:
            continue
        docs_out.append({"doc_id": did, "filename": doc["filename"], "rows": len(rows)})
        totals["revenue"] = round(totals["revenue"] + agg["totals"]["revenue"], 2)
        totals["expenses"] = round(totals["expenses"] + agg["totals"]["expenses"], 2)
        skipped += agg["totals"]["skipped_cells"]
        missing.update(agg["missing_columns"])
        for k, g in agg["groups"].items():
            d = groups.setdefault(k, {"revenue": 0.0, "expenses": 0.0, "rows": 0})
            d["revenue"] = round(d["revenue"] + g["revenue"], 2)
            d["expenses"] = round(d["expenses"] + g["expenses"], 2)
            d["rows"] += g["rows"]
    totals["profit"] = round(totals["revenue"] - totals["expenses"], 2)
    for g in groups.values():
        g["profit"] = round(g["revenue"] - g["expenses"], 2)
    verdict = ("calculated from data" if docs_out and not missing
               else "insufficient data to calculate this metric")
    _audit(user, "ws_analytics", "financial",
           f"docs={len(docs_out)} revenue={totals['revenue']} expenses={totals['expenses']}")
    return {"totals": totals, "groups": groups, "docs": docs_out,
            "missing_columns": sorted(missing), "skipped_cells": skipped,
            "period": period, "verdict": verdict,
            "kinds": ["calculated from data" if verdict.startswith("calculated")
                      else "user-provided information (columns chosen by requester)"]}


# ---------------- reports ----------------

def _report_metrics_scope(user: dict, scope: str, scope_id: str,
                          period_from: str, period_to: str) -> tuple[list[str], str, list]:
    """Resolve which user ids feed the report + source labels. Fail-closed."""
    if scope == "own":
        return [user["id"]], "self", [f"records of {user['username']}"]
    if user["role"] not in MANAGER_ROLES:
        raise HTTPException(403, "team/department reports require a manager role")
    if scope == "team":
        ids = _require_scope(user)
        ids = ids if ids is not None else [u["id"] for u in userstore.list_users() if u["active"]]
        return ids, _dept_of(user) or "team", [f"team records ({_dept_of(user) or 'all'})"]
    if scope == "department":
        dept = scope_id or _dept_of(user)
        if user["role"] != "ADMIN":
            scope_now = _require_scope(user)
            if scope_now is not None and dept != _dept_of(user):
                raise HTTPException(403, "department outside your scope")
        ids = [u["id"] for u in userstore.list_users()
               if u["active"] and (u.get("department") or "") == dept]
        return ids, dept, [f"department records ({dept})"]
    raise HTTPException(422, "scope must be own|team|department")


@router.post("/api/v1/reports/generate")
def generate_report(b: ReportGenIn, user: dict = Depends(require_perm("REPORT_CREATE"))):
    from . import ratelimit
    ratelimit.check("reports", user["id"], user)
    if b.report_type not in store.REPORT_TYPES:
        raise HTTPException(422, f"unknown report type '{b.report_type}'")
    from .i18n import SUPPORTED
    lang = b.lang if b.lang in SUPPORTED else "en"
    uids, scope_label, sources = _report_metrics_scope(
        user, b.scope, b.scope_id or "", b.period_from or "", b.period_to or "")
    att = store.list_attendance(uids, b.period_from or "", b.period_to or "")
    tasks = store.list_tasks(assignee_ids=uids)
    ev = store.list_evidence(owner_ids=uids)
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    done = sum(1 for t in tasks if t["status"] in ("approved", "reviewed"))
    present = sum(1 for a in att if a["status"] == "present")
    blocked = [t for t in tasks if t["status"] == "blocked"]
    metrics = {"period": {"from": b.period_from or "", "to": b.period_to or ""},
               "employees": len(uids), "tasks_total": len(tasks),
               "tasks_by_status": by_status,
               "completion_pct": round(100 * done / len(tasks), 1) if tasks else None,
               "attendance_rate": round(present / len(att), 3) if att else None,
               "evidence_total": len(ev),
               "blocked_count": len(blocked),
               "performance_avg": _avg_perf(uids)}
    srcs = sources + [f"attendance records: {b.period_from or '?'}–{b.period_to or '?'}",
                      f"work submissions: {b.period_from or '?'}–{b.period_to or '?'}"]
    analysis = _grounded_summary(user, b.report_type, metrics, blocked, lang)
    title = f"{b.report_type.replace('_', ' ').title()} — {scope_label}"
    rec = store.create_report(title, b.report_type, b.scope, scope_label,
                              b.period_from or "", b.period_to or "", metrics, analysis,
                              srcs, b.official_remarks or "", user["id"])
    _audit(user, "ws_report", rec["id"],
           f"type={b.report_type} scope={b.scope} tasks={len(tasks)}")
    return {**rec, "metrics": metrics, "sources": srcs}


def _avg_perf(uids: list[str]) -> float | None:
    vals = [p["overall"] for p in (_perf_for(u, None) for u in uids) if p["overall"] is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _grounded_summary(user: dict, rtype: str, metrics: dict, blocked: list, lang: str) -> str:
    """Data-first summary: fixed template of recorded figures, then an
    AI-assisted interpretation clearly labeled as such (template fallback
    when the provider fails). Never invents figures."""
    m = metrics
    lines = [
        f"Tasks: {m['tasks_total']} total, "
        f"{m['completion_pct'] if m['completion_pct'] is not None else 'n/a'}% completed.",
        f"Attendance rate: {m['attendance_rate'] if m['attendance_rate'] is not None else 'n/a'}.",
        f"Evidence submissions: {m['evidence_total']}. Blocked items: {m['blocked_count']}.",
    ]
    if m["performance_avg"] is not None:
        lines.append(f"Average performance score: {m['performance_avg']} (configured criteria).")
    factual = " ".join(lines)
    interp = ""
    try:
        from .ai import get_service
        interp = get_service().provider.generate(
            [{"role": "user", "content": f"Given these recorded figures, write 2 sentences of "
                                        f"AI-assisted analysis (no new numbers): {factual}"}],
            system="You interpret recorded figures only. Never invent data.", role=user["role"])
        interp = (interp or "")[:1000]
    except Exception:
        interp = ""
    if lang != "en":
        try:
            from .i18n import get_translation_provider, translate_answer
            factual, _ = translate_answer(factual, lang, get_translation_provider())
            if interp:
                interp, _ = translate_answer(interp, lang, get_translation_provider())
        except Exception:
            pass
    out = f"Recorded data: {factual}"
    if interp.strip():
        out += f"\n\nAI-assisted analysis: {interp.strip()}"
    return out[:4000]


@router.get("/api/v1/reports")
def list_reports(report_type: str = "", user: dict = Depends(require_perm("REPORT_READ"))):
    rows = store.list_reports(report_type=report_type or "")
    out = [r for r in rows if _can_see_report(user, r)]
    return {"reports": [{**r, "metrics": _jm(r, "metrics_json"),
                         "sources": _jl(r, "sources_json")} for r in out]}


def _jm(r: dict, k: str) -> dict:
    import json as _json
    try:
        return _json.loads(r.get(k) or "{}")
    except Exception:
        return {}


def _jl(r: dict, k: str) -> list:
    import json as _json
    try:
        v = _json.loads(r.get(k) or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _can_see_report(user: dict, r: dict) -> bool:
    if user["role"] == "ADMIN":
        return True
    if r["created_by"] == user["id"]:
        return True
    if user["role"] in MANAGER_ROLES and r["scope_type"] in ("team", "department"):
        dept = _dept_of(user)
        return r["scope_id"] in (dept, "") or r["scope_id"] == "team"
    return False


@router.get("/api/v1/reports/{rid}")
def get_report(rid: str, user: dict = Depends(require_perm("REPORT_READ"))):
    r = store.get_report(rid)
    if not r or not _can_see_report(user, r):
        raise HTTPException(404, "report not found")
    _audit(user, "ws_analytics", f"report:{rid}", f"type={r['report_type']}")
    return {**r, "metrics": _jm(r, "metrics_json"), "sources": _jl(r, "sources_json")}


# ---------------- feedback ----------------

@router.post("/api/v1/work/feedback")
def give_feedback(b: FeedbackIn, user: dict = Depends(require_perm("WORK_MANAGE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    if user["role"] not in (MANAGER_ROLES | {"REVIEWER"}):
        raise HTTPException(403, "feedback requires a manager/reviewer role")
    target = userstore.find_by_id(b.to_user_id)
    if not target or not target["active"]:
        raise HTTPException(404, "employee not found")
    scope = _require_scope(user)
    if not _in_scope_ids(scope, b.to_user_id):
        raise HTTPException(404, "employee not found")
    if b.task_id:
        t = store.get_task(b.task_id)
        if not t or (t["assignee_id"] != b.to_user_id and t["created_by"] != user["id"]):
            raise HTTPException(404, "task not found")
    try:
        rec = store.add_feedback(b.to_user_id, user["id"], b.task_id or "", b.text or "",
                                 b.rating)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _audit(user, "ws_task_review", rec["id"], f"feedback to={b.to_user_id}")
    return rec


# ---------------- identity (privacy-conscious verification concept) ----------------

@router.get("/api/v1/identity/status")
def identity_status(user_id: str = "", user: dict = Depends(require_perm("WORK_READ"))):
    """Verification status: account active + face enrolled on file + device
    check-in today. Honest wording only — never claims recognition accuracy,
    never biometric-only auth."""
    scope = _require_scope(user)
    target = user_id or user["id"]
    if not _in_scope_ids(scope, target):
        raise HTTPException(404, "employee not found")
    rec = userstore.find_by_id(target)
    if not rec:
        raise HTTPException(404, "employee not found")
    ident = store.get_identity(target)
    today = time.strftime("%Y-%m-%d")
    checkins = [a for a in store.list_attendance([target], today, today)
                if a["status"] == "present"]
    enrolled = bool(ident.get("enrolled"))
    verified = bool(rec["active"]) and enrolled
    return {"user_id": target, "username": rec["username"],
            "account_active": bool(rec["active"]), "enrolled": enrolled,
            "enroll_method": ident.get("method") or "",
            "device_checkin_today": len(checkins) > 0,
            "verified": verified,
            "status": "Identity Verified" if verified else "Verification Required"}


@router.post("/api/v1/identity/enroll")
async def identity_enroll(f: UploadFile,
                          user: dict = Depends(require_perm("WORK_READ"))):
    """Optional face enrollment: image stored through the private docs
    pipeline (owner=self, never public, never sent externally). Only a
    sha256 + doc ref are recorded — no biometric templates leave the host."""
    import hashlib
    from . import ratelimit
    from . import docs_api
    from . import docs_store
    ratelimit.check("upload", user["id"], user)
    data = await docs_api._read_capped(f)
    kind, ext = docs_api._classify(f.filename or "face", data)
    if kind != "image":
        raise HTTPException(415, "enrollment requires a JPG/PNG image")
    import os as _os
    import uuid as _uuid
    _os.makedirs(docs_api.UPLOAD_DIR, exist_ok=True)
    stored = f"d-{_uuid.uuid4().hex[:12]}{ext}"
    with open(_os.path.join(docs_api.UPLOAD_DIR, stored), "wb") as fh:
        fh.write(data)
    doc = docs_store.create(user["id"], user["username"],
                            docs_api._safe_display_name("identity-enrollment"),
                            stored, "image/png" if ext == ".png" else "image/jpeg",
                            ext, kind, len(data))
    digest = hashlib.sha256(data).hexdigest()
    rec = store.enroll_identity(user["id"], "face", doc["id"], digest)
    _audit(user, "ws_identity_enroll", doc["id"], "face enrollment stored privately")
    return {"enrolled": True, "method": "face", "doc_id": doc["id"],
            "status": "Identity Verified"}
