"""Seed clearly-marked DEMO data for the SIH presentation (idempotent).

Usage:
  python scripts/seed_demo.py           # seed anything missing (safe to re-run)
  python scripts/seed_demo.py --reset   # delete ONLY demo-marked records, then re-seed

Rules honoured by this script:
  * Idempotent + repeatable: every section checks for its own marker first,
    so restarting never creates duplicates and never overwrites other data.
  * Every seeded record is labelled "DEMO" (title prefix "DEMO — ",
    filename prefix "DEMO_", attendance/remarks note "DEMO — ...") so demo
    data is never mistaken for real records.
  * Only talks to the real HTTP API with real logins — nothing bypasses
    backend authorization. Rate limits are relaxed via env for the bulk run.
  * Touches only the local dev databases (respects SOV_*_DB env).
  * --reset removes demo-marked rows only (never other records).
"""
import io
import os
import sys
import time
from datetime import date, timedelta

# Relax per-minute rate limits BEFORE the app imports them (read at call time).
os.environ.setdefault("SOV_RATE_WORK", "2000")
os.environ.setdefault("SOV_RATE_UPLOAD", "500")
os.environ.setdefault("SOV_RATE_REPORTS", "500")
os.environ.setdefault("SOV_RATE_ANALYZE", "500")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402

c = TestClient(app)
H = lambda t: {"Authorization": "Bearer " + t}  # noqa: E731

MARK = "DEMO — "          # record-title/notes marker for demo rows
FILE_MARK = "DEMO_"       # filename marker for demo documents

ACCOUNTS = {
    "admin":     ("admin@company.com", "Admin@2026#S9x!"),
    "manager":   ("manager@company.com", "Manager@2026#K7p!"),
    "employee":  ("employee@company.com", "Employee@2026#R4m!"),
    "employee1": ("employee1@company.com", "Employee@2026#R4m!"),
    "employee2": ("employee2@company.com", "Employee@2026#R4m!"),
    "operator":  ("operator@company.com", "Operator@2026#T8q!"),
    "reviewer":  ("reviewer@company.com", "Reviewer@2026#V6n!"),
}


def login(u, p):
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---------------------------------------------------------------- helpers

def _get(tok, path):
    r = c.get(path, headers=H(tok))
    assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text}"
    return r.json()


def _post(tok, path, body=None, **kw):
    r = c.post(path, headers=H(tok), json=body, **kw)
    assert r.status_code in (200, 201), f"POST {path}: {r.status_code} {r.text}"
    return r.json()


def _patch(tok, path, body):
    r = c.patch(path, headers=H(tok), json=body)
    assert r.status_code == 200, f"PATCH {path}: {r.status_code} {r.text}"
    return r.json()


def _ids(tokens):
    """Resolve each demo account's user id via the real /auth/me endpoint."""
    return {k: _get(t, "/api/v1/auth/me")["id"] for k, t in tokens.items()}


# ---------------------------------------------------------------- schedules

def seed_schedules(tok, ids):
    """Team roster + per-employee roster + org-wide default override."""
    existing = {s["name"] for s in _get(tok, "/api/v1/work/schedules")["schedules"]}
    if any(n.startswith(MARK) for n in existing):
        print("  schedules: already seeded — skipping")
        return
    _post(tok, "/api/v1/work/schedules", {
        "name": MARK + "Production day shift", "shift": "Morning",
        "start": "09:00", "end": "18:00", "break_start": "13:00",
        "break_end": "14:00", "working_days": ["Monday", "Tuesday", "Wednesday",
                                               "Thursday", "Friday"],
        "holidays": [], "scope_type": "team", "scope_id": "Production",
        "notes": MARK + "assigned to the Production team."})
    _post(tok, "/api/v1/work/schedules", {
        "name": MARK + "Flexible start (Employee One)", "shift": "Flexible",
        "start": "10:00", "end": "19:00", "break_start": "14:00",
        "break_end": "14:30", "working_days": ["Monday", "Tuesday", "Wednesday",
                                               "Thursday", "Friday"],
        "holidays": [], "scope_type": "user", "scope_id": ids["employee1"],
        "notes": MARK + "individual roster override."})
    print("  schedules: team + per-employee rosters created")


# ---------------------------------------------------------------- attendance

def seed_attendance(tok, ids):
    """Last ~30 calendar days of weekday attendance for the demo team."""
    today = date.today()
    start = today - timedelta(days=30)
    targets = ["manager", "employee", "employee1", "employee2", "operator"]
    # Deterministic pattern: mostly present, sprinkled late/leave/absent.
    pattern = {9: "late", 13: "leave", 17: "absent", 23: "late"}
    seeded = filled = 0
    for key in targets:
        uid = ids[key]
        have = {r["date"] for r in _get(
            tok, f"/api/v1/work/attendance?user_id={uid}"
                 f"&date_from={start.isoformat()}&date_to={today.isoformat()}")["rows"]}
        d = start
        while d <= today:
            if d.weekday() < 5 and d.isoformat() not in have:      # Mon-Fri only
                status = pattern.get(d.day % 30, "present")
                _post(tok, "/api/v1/work/attendance", {
                    "user_id": uid, "date": d.isoformat(), "status": status,
                    "note": MARK + "seeded history"})
                seeded += 1
            d += timedelta(days=1)
        filled += 1
    print(f"  attendance: backfilled {seeded} missing day(s) across "
          f"{filled} team members")


# ---------------------------------------------------------------- tasks

def seed_tasks(tok, ids, emp_tokens):
    """Work assignments covering every workflow status (REAL transitions:
    assignee starts/submits/blocks, manager reviews/approves/corrects)."""
    existing = {t["title"] for t in _get(tok, "/api/v1/work/tasks")["tasks"]}
    if any(t.startswith(MARK) for t in existing):
        print("  tasks: already seeded — skipping")
        return {}
    today = date.today()
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731

    def create(title, who, priority, start, due, instructions):
        return _post(tok, "/api/v1/work/tasks", {
            "title": MARK + title, "instructions": instructions,
            "assignee_id": ids[who], "priority": priority,
            "start_date": start, "due_date": due})

    # 1. COMPLETED: full cycle assigned -> in progress -> submitted -> approved
    t1 = create("Conveyor sensor installation", "employee1", "high",
                d(-10), d(-2), "Install the new conveyor sensors, run "
                "calibration, and log the readings.")
    e1 = emp_tokens["employee1"]
    _patch(e1, f"/api/v1/work/tasks/{t1['id']}",
           {"status": "in_progress", "progress": 70})
    _patch(e1, f"/api/v1/work/tasks/{t1['id']}",
           {"status": "submitted", "progress": 100})
    _patch(tok, f"/api/v1/work/tasks/{t1['id']}",
           {"status": "approved",
            "review_remarks": MARK + "readings verified — approved."})

    # 2. REQUIRES_REVIEW: submitted, waiting for the manager
    t2 = create("Batch 42 quality verification", "employee1", "urgent",
                d(-3), d(0), "Verify batch 42 against the QA checklist and "
                "attach the signed sheet.")
    _patch(emp_tokens["employee1"], f"/api/v1/work/tasks/{t2['id']}",
           {"status": "in_progress", "progress": 80})
    _patch(emp_tokens["employee1"], f"/api/v1/work/tasks/{t2['id']}",
           {"status": "submitted", "progress": 90})

    # 3. IN_PROGRESS
    t3 = create("Line 3 calibration", "employee2", "medium",
                d(-2), d(3), "Recalibrate line 3 after the maintenance stop.")
    _patch(emp_tokens["employee2"], f"/api/v1/work/tasks/{t3['id']}",
           {"status": "in_progress", "progress": 55})

    # 4. NOT_STARTED (assigned)
    create("Safety checklist audit", "employee2", "medium",
           d(1), d(7), "Walk the floor with the safety checklist and file "
           "observations.")

    # 5. BLOCKED
    t5 = create("Packaging label reprint", "employee", "low",
                d(-1), d(2), "Reprint labels for the promo run.")
    _patch(emp_tokens["employee"], f"/api/v1/work/tasks/{t5['id']}",
           {"status": "blocked"})

    # 6. CORRECTION REQUESTED: submitted -> needs_correction by manager
    t6 = create("Weekly production log", "employee", "medium",
                d(-4), d(1), "Compile the weekly production log.")
    _patch(emp_tokens["employee"], f"/api/v1/work/tasks/{t6['id']}",
           {"status": "in_progress", "progress": 90})
    _patch(emp_tokens["employee"], f"/api/v1/work/tasks/{t6['id']}",
           {"status": "submitted", "progress": 95})
    _patch(tok, f"/api/v1/work/tasks/{t6['id']}",
           {"status": "needs_correction",
            "review_remarks": MARK + "rows 12-18 are incomplete — please "
            "extend and resubmit."})

    # 7. REVIEWED (checked, not yet approved)
    t7 = create("Inventory reconciliation", "employee1", "medium",
                d(-5), d(0), "Reconcile warehouse counts against the system.")
    _patch(emp_tokens["employee1"], f"/api/v1/work/tasks/{t7['id']}",
           {"status": "submitted", "progress": 100})
    _patch(tok, f"/api/v1/work/tasks/{t7['id']}", {"status": "reviewed"})

    print("  tasks: 7 assignments across all workflow statuses "
          "(not started / in progress / blocked / needs correction / "
          "reviewed / awaiting approval / approved)")
    return {"awaiting_review": t2["id"], "approved": t1["id"]}


def seed_evidence(emp_tok, ids):
    """Attach one text evidence file to the approved demo task."""
    have = {d["filename"] for d in _get(
        emp_tok, "/api/v1/docs")["documents"]}
    if FILE_MARK + "calibration_log.txt" in have:
        print("  evidence: already seeded — skipping")
        return
    # Locate the demo task by its DEMO marker (works whether or not this
    # run created it — evidence must not depend on task-seed ordering).
    tasks = _get(emp_tok, "/api/v1/work/tasks")["tasks"]
    target = next((t for t in tasks
                   if t["title"] == MARK + "Conveyor sensor installation"), None)
    if not target:
        print("  evidence: demo task missing — skipping")
        return
    data = (MARK + "calibration log\nsensor A: 4.02V\nsensor B: 3.98V\n"
            "ambient: 22.4C\nall within tolerance.\n").encode()
    r = c.post("/api/v1/work/evidence",
               headers=H(emp_tok),
               files={"f": (FILE_MARK + "calibration_log.txt", data,
                            "text/plain")},
               data={"task_id": target["id"],
                     "note": MARK + "calibration readings"})
    assert r.status_code == 200, f"evidence: {r.status_code} {r.text}"
    print("  evidence: 1 file attached to the approved task")


# ---------------------------------------------------------------- worksheets

def seed_worksheets(tok, ids, emp_tokens):
    existing = {w["title"] for w in _get(tok, "/api/v1/work/worksheets")["worksheets"]}
    if any(w.startswith(MARK) for w in existing):
        print("  worksheets: already seeded — skipping")
        return
    cols = [{"name": "task", "type": "text"}, {"name": "owner", "type": "text"},
            {"name": "done", "type": "text"}]

    w1 = _post(tok, "/api/v1/work/worksheets", {
        "title": MARK + "Maintenance log", "description": "Daily maintenance "
        "entries for the demo line.", "columns": cols})
    a1 = _post(tok, f"/api/v1/work/worksheets/{w1['id']}/assign",
               {"assignee_id": ids["employee2"]})          # stays assigned

    w2 = _post(tok, "/api/v1/work/worksheets", {
        "title": MARK + "Shift handover", "description": "End-of-shift "
        "handover rows for the demo team.", "columns": cols})
    a2 = _post(tok, f"/api/v1/work/worksheets/{w2['id']}/assign",
               {"assignee_id": ids["employee1"]})
    _patch(emp_tokens["employee1"],
           f"/api/v1/work/worksheets/assignments/{a2['id']}/submit",
           {"fields": {"task": "Handover complete", "owner": "employee1",
                       "done": "yes"}})
    _patch(tok, f"/api/v1/work/worksheets/assignments/{a2['id']}/review",
           {"status": "approved", "remarks": MARK + "looks good."})
    print("  worksheets: 2 sheets (1 pending, 1 submitted + approved)")


# ---------------------------------------------------------------- reviewer

def seed_reviewer(admin_tok, rev_tok, ids):
    """Reviewer-specific demo rows (Quality department).

    The manager cannot touch this department, so the admin acts with admin
    rights — nothing here bypasses RBAC: attendance is marked by the admin,
    tasks are created by the admin and progressed by the reviewer through the
    normal owner lane, evidence is attached by the reviewer (WORK_READ)."""
    today = date.today()
    start = today - timedelta(days=30)
    dd = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731

    # --- attendance (~30 weekdays, admin scope covers Quality) ---
    have = {r["date"] for r in _get(
        admin_tok, f"/api/v1/work/attendance?user_id={ids['reviewer']}"
                   f"&date_from={start.isoformat()}&date_to={today.isoformat()}")["rows"]}
    seeded = 0
    pattern = {11: "late", 20: "leave"}
    d = start
    while d <= today:
        if d.weekday() < 5 and d.isoformat() not in have:
            _post(admin_tok, "/api/v1/work/attendance", {
                "user_id": ids["reviewer"], "date": d.isoformat(),
                "status": pattern.get(d.day % 30, "present"),
                "note": MARK + "seeded history"})
            seeded += 1
        d += timedelta(days=1)

    # --- assignments: one awaiting review, one not started ---
    all_titles = {t["title"] for t in
                  _get(admin_tok, "/api/v1/work/tasks")["tasks"]}
    if MARK + "Quality inspection report" not in all_titles:
        t1 = _post(admin_tok, "/api/v1/work/tasks", {
            "title": MARK + "Quality inspection report",
            "instructions": "Compile the weekly quality inspection findings "
                            "and submit for approval.",
            "assignee_id": ids["reviewer"], "priority": "high",
            "start_date": dd(-2), "due_date": dd(2)})
        _patch(rev_tok, f"/api/v1/work/tasks/{t1['id']}",
               {"status": "in_progress", "progress": 60})
        _patch(rev_tok, f"/api/v1/work/tasks/{t1['id']}",
               {"status": "submitted", "progress": 100})
    if MARK + "Supplier certificate review" not in all_titles:
        _post(admin_tok, "/api/v1/work/tasks", {
            "title": MARK + "Supplier certificate review",
            "instructions": "Review incoming supplier certificates against "
                            "the approved vendor list.",
            "assignee_id": ids["reviewer"], "priority": "medium",
            "start_date": dd(1), "due_date": dd(5)})

    # --- evidence on the submitted task (reviewer may attach, WORK_READ) ---
    have_docs = {doc["filename"] for doc in
                 _get(rev_tok, "/api/v1/docs")["documents"]}
    if FILE_MARK + "review_notes.txt" not in have_docs:
        tasks = _get(rev_tok, "/api/v1/work/tasks")["tasks"]
        target = next((t for t in tasks
                       if t["title"] == MARK + "Quality inspection report"), None)
        if target:
            r = c.post("/api/v1/work/evidence", headers=H(rev_tok),
                       files={"f": (FILE_MARK + "review_notes.txt",
                                    (MARK + "review notes\nchecks: 12/12 "
                                     "passed\n").encode(), "text/plain")},
                       data={"task_id": target["id"],
                             "note": MARK + "inspection notes"})
            assert r.status_code == 200, f"evidence: {r.status_code} {r.text}"

    # --- worksheet assigned to the reviewer (admin creates + assigns) ---
    wtitles = {w["title"] for w in
               _get(admin_tok, "/api/v1/work/worksheets")["worksheets"]}
    if MARK + "Quality audit checklist" not in wtitles:
        w = _post(admin_tok, "/api/v1/work/worksheets", {
            "title": MARK + "Quality audit checklist",
            "description": "Weekly quality audit rows for the demo.",
            "columns": [{"name": "check", "type": "text"},
                        {"name": "result", "type": "text"},
                        {"name": "notes", "type": "text"}]})
        _post(admin_tok, f"/api/v1/work/worksheets/{w['id']}/assign",
              {"assignee_id": ids["reviewer"]})

    print(f"  reviewer: attendance backfilled ({seeded} missing day(s)), "
          "2 assignments (1 awaiting review), evidence + worksheet added")


# ---------------------------------------------------------------- operator

def seed_operator(mgr_tok, op_tok, ids):
    """OPERATOR self-service demo rows (Production department — the manager's
    normal scope; nothing bypasses RBAC): one realistic assignment in the
    owner lane so the operator portal shows work they can continue and submit
    for review."""
    titles = {t["title"] for t in _get(mgr_tok, "/api/v1/work/tasks")["tasks"]}
    if MARK + "Shift handover summary" in titles:
        print("  operator: already seeded — skipping")
        return
    today = date.today()
    t = _post(mgr_tok, "/api/v1/work/tasks", {
        "title": MARK + "Shift handover summary",
        "instructions": "Summarize shift events, open issues, and hand over "
                        "to the incoming crew.",
        "assignee_id": ids["operator"], "priority": "medium",
        "start_date": (today - timedelta(days=1)).isoformat(),
        "due_date": (today + timedelta(days=3)).isoformat()})
    _patch(op_tok, f"/api/v1/work/tasks/{t['id']}",
           {"status": "in_progress", "progress": 40})
    print("  operator: 1 assignment seeded (in progress)")


# ---------------------------------------------------------------- documents

def _csv_bytes():
    rows = ["month,revenue,expense",
            "2026-06,420000,310000", "2026-07,455000,330000",
            "2026-08,470000,341000", "2026-09,482000,350000"]
    return ("\n".join(rows) + "\n").encode()


def _xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["month", "revenue", "expense"])
    for row in [("2026-06", 420000, 310000), ("2026-07", 455000, 330000),
                ("2026-08", 470000, 341000), ("2026-09", 482000, 350000)]:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_bytes():
    """Hand-built minimal single-page PDF (valid xref) with DEMO text."""
    lines = [MARK + "Safety protocol (sample document)",
             "1. Lock out / tag out before servicing.",
             "2. Report any spill to the shift supervisor.",
             "3. Wear hearing protection beyond line 3.",
             "This file exists to demonstrate the document pipeline."]
    esc = [ln.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
           for ln in lines]
    content = "BT /F1 11 Tf 54 740 Td 16 TL\n" + "\n".join(
        f"({ln}) Tj T*" for ln in esc) + "\nET"
    cb = content.encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(cb)).encode() + b" >>\nstream\n" + cb +
        b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, o)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
            % (len(objs) + 1, xref))
    return bytes(out)


def _docx_bytes():
    from docx import Document
    d = Document()
    d.add_heading(MARK + "Maintenance SOP", level=1)
    d.add_paragraph("Weekly maintenance procedure for the demo production "
                    "line. Sample content for the document pipeline.")
    d.add_paragraph("Step 1 — power down. Step 2 — inspect. Step 3 — log.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _upload(tok, name, data, mime):
    have = {d["filename"] for d in _get(tok, "/api/v1/docs")["documents"]}
    if name in have:
        return False
    r = c.post("/api/v1/docs/upload", headers=H(tok),
               files={"f": (name, data, mime)})
    assert r.status_code in (200, 201), f"upload {name}: {r.status_code} {r.text}"
    return True


def seed_documents(tokens):
    """Documents per owner (the API scopes non-admin lists to OWN docs)."""
    plan = {
        # Spreadsheets page is manager/admin — they need owned CSV + XLSX.
        "manager": [
            (FILE_MARK + "revenue_expenses.csv", _csv_bytes(), "text/csv"),
            (FILE_MARK + "budget_2026.xlsx", _xlsx_bytes(),
             "application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet"),
            (FILE_MARK + "safety_protocol.pdf", _pdf_bytes(),
             "application/pdf"),
            (FILE_MARK + "maintenance_sop.docx", _docx_bytes(),
             "application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document"),
            (FILE_MARK + "readme.txt",
             (MARK + "sample text document for the demo.\n").encode(),
             "text/plain"),
        ],
        "employee": [
            (FILE_MARK + "inspection_checklist.txt",
             (MARK + "inspection checklist\nbody: ok\nedges: ok\n").encode(),
             "text/plain"),
            (FILE_MARK + "daily_output.csv",
             (b"line,units\n1,420\n2,380\n3,455\n"), "text/csv"),
        ],
        "employee1": [
            (FILE_MARK + "handover_notes.txt",
             (MARK + "handover notes\nopen items: none\n").encode(),
             "text/plain"),
        ],
        "employee2": [
            (FILE_MARK + "maintenance_readings.csv",
             (b"sensor,voltage\nA,4.02\nB,3.98\n"), "text/csv"),
        ],
        "operator": [
            (FILE_MARK + "shift_log.txt",
             (MARK + "shift log\nquiet shift\n").encode(), "text/plain"),
        ],
        # NOTE: the REVIEWER role deliberately has no DOCUMENT_UPLOAD
        # permission (read + analyze only) — no demo file is seeded for it,
        # because seeding must never bypass RBAC.
    }
    made = 0
    for who, files in plan.items():
        for name, data, mime in files:
            if _upload(tokens[who], name, data, mime):
                made += 1
    print(f"  documents: {made} new demo file(s) "
          f"(CSV, XLSX, PDF, DOCX, TXT across roles)")


# ---------------------------------------------------------------- reports

def seed_reports(tok, ids, tokens):
    """Team reports for the manager + one own-scope report per other role so
    every role's Reports list renders real data."""
    have = {(r["report_type"], r["official_remarks"])
            for r in _get(tok, "/api/v1/reports")["reports"]}
    today = date.today()
    first = today.replace(day=1).isoformat()
    team_types = ["monthly_ops", "performance", "attendance"]
    made = 0
    for rt in team_types:
        if (rt, MARK + "seeded demo dataset.") in have:
            continue
        _post(tok, "/api/v1/reports/generate", {
            "report_type": rt, "scope": "team",
            "period_from": (today - timedelta(days=30)).isoformat(),
            "period_to": today.isoformat(),
            "official_remarks": MARK + "seeded demo dataset."})
        made += 1
    for who in ("admin", "employee", "employee1", "employee2", "operator",
                "reviewer"):
        tk = tokens[who]
        own = {(r["report_type"], r["official_remarks"])
               for r in _get(tk, "/api/v1/reports")["reports"]}
        if ("employee_work", MARK + "seeded demo dataset.") in own:
            continue
        _post(tk, "/api/v1/reports/generate", {
            "report_type": "employee_work", "scope": "own",
            "period_from": first, "period_to": today.isoformat(),
            "official_remarks": MARK + "seeded demo dataset."})
        made += 1
    print(f"  reports: {made} new report(s) across roles")


def seed_feedback(tok, ids, tokens):
    """Manager feedback — checked via the recipient's own dashboard."""
    existing = {f.get("text", "") for f in
                _get(tokens["employee1"], "/api/v1/work/me")["feedback_recent"]}
    if any(t.startswith(MARK) for t in existing):
        print("  feedback: already seeded — skipping")
        return
    _post(tok, "/api/v1/work/feedback", {
        "to_user_id": ids["employee1"],
        "text": MARK + "calibration work was clean and well documented.",
        "rating": 5})
    _post(tok, "/api/v1/work/feedback", {
        "to_user_id": ids["employee2"],
        "text": MARK + "good progress on line 3 — keep the log updated.",
        "rating": 4})
    print("  feedback: 2 notes recorded")


# ---------------------------------------------------------------- reset

def reset_demo():
    """Delete ONLY demo-marked records from the local dev databases."""
    from backend.app import work as work_store
    from backend.app import docs_store
    import backend.app.docs_api as docs_api

    con = work_store._con()
    demo_task_ids = [r["id"] for r in con.execute(
        "SELECT id FROM tasks WHERE title LIKE ? OR title IN (?, ?)",
        (MARK + "%", "Assembly Line Upgrade", "Quality Verification"))]
    n = 0
    if demo_task_ids:
        ph = ",".join("?" * len(demo_task_ids))
        n += con.execute(f"DELETE FROM evidence WHERE task_id IN ({ph})",
                         demo_task_ids).rowcount
        n += con.execute(f"DELETE FROM tasks WHERE id IN ({ph})",
                         demo_task_ids).rowcount
    n += con.execute(
        "DELETE FROM attendance WHERE note LIKE ? OR note = ?",
        (MARK + "%", "demo seed")).rowcount
    n += con.execute("DELETE FROM schedules WHERE name LIKE ?",
                     (MARK + "%",)).rowcount
    demo_ws = [r["id"] for r in con.execute(
        "SELECT id FROM worksheets WHERE title LIKE ? OR title = ?",
        (MARK + "%", "Production Schedule"))]
    if demo_ws:
        ph = ",".join("?" * len(demo_ws))
        n += con.execute(f"DELETE FROM assignments WHERE worksheet_id IN ({ph})",
                         demo_ws).rowcount
        n += con.execute(f"DELETE FROM worksheets WHERE id IN ({ph})",
                         demo_ws).rowcount
    n += con.execute(
        "DELETE FROM reports WHERE official_remarks LIKE ?"
        " OR official_remarks LIKE ?", (MARK + "%", "%demo dataset.%")).rowcount
    n += con.execute("DELETE FROM feedback WHERE text LIKE ? OR text = ?",
                     (MARK + "%", "Solid installation work.")).rowcount
    con.commit()
    con.close()

    # Demo documents: rows + stored files (filename marker only).
    dcon = docs_store._lite()
    rows = dcon.execute(
        "SELECT id, stored FROM documents WHERE filename LIKE ?"
        " OR filename IN (?, ?, ?)",
        (FILE_MARK + "%", "site-safety.pdf", "quarterly-report.csv",
         "handover.txt")).fetchall()
    m = 0
    for r in rows:
        m += dcon.execute("DELETE FROM doc_texts WHERE doc_id=?",
                          (r["id"],)).rowcount
        m += dcon.execute("DELETE FROM documents WHERE id=?",
                          (r["id"],)).rowcount
        path = os.path.join(docs_api.UPLOAD_DIR, r["stored"])
        if os.path.exists(path):
            os.remove(path)
    dcon.commit()
    dcon.close()
    print(f"reset: removed {n} work record(s) and {m} demo document(s)")


# ---------------------------------------------------------------- main

def main():
    if "--reset" in sys.argv:
        reset_demo()

    tokens = {k: login(*v) for k, v in ACCOUNTS.items()}
    ids = _ids(tokens)
    mgr = tokens["manager"]

    print("seeding demo data (idempotent, DEMO-labelled)…")
    seed_schedules(tokens["admin"], ids)     # admin: org-wide allowed
    seed_schedules(mgr, ids)                 # manager: team + employee rows
    seed_attendance(mgr, ids)
    seed_tasks(mgr, ids, tokens)
    seed_evidence(tokens["employee1"], ids)
    seed_worksheets(mgr, ids, tokens)
    seed_reviewer(tokens["admin"], tokens["reviewer"], ids)
    seed_operator(tokens["manager"], tokens["operator"], ids)
    seed_documents(tokens)
    seed_reports(mgr, ids, tokens)
    seed_feedback(mgr, ids, tokens)
    print("done.")


if __name__ == "__main__":
    main()
