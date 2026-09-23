"""Focused demo-workflow tests (SIH presentation hardening).

Covers the demo contract end to end against the real HTTP API:
  1. manager assign -> employee portal (display names, priority, dates,
     full status cycle incl. correction -> resume -> approve)
  2. per-employee schedule visibility + server-side scope guards
  3. profile self-edit allow-list (role/department/status never change)
  4. RBAC boundaries (reviewer analytics stays 403, operator cannot create
     tasks, employee IDOR, reviewer's allowed data sources)
  5. demo seed idempotency (two real seed runs, unchanged counts)
  6. UI route integrity + required empty-state / i18n texts

Run: python tests/test_demo_workflow.py. Isolated temp DBs, mock providers.
"""
import os
import re
import subprocess
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_demo_")
for k in ["SOV_AUTH_DB", "SOV_AUTH_AUDIT_DB", "SOV_CHAT_DB", "SOV_DOCS_DB",
          "SOV_VECTORS_DB", "SOV_UPLOADS_DIR", "SOV_AGENTS_DB", "SOV_WORK_DB",
          "SOV_SYSCONFIG_PATH"]:
    os.environ[k] = os.path.join(_tmp, k.replace("SOV_", "").lower())
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_RATE_WORK"] = "5000"
os.environ["SOV_RATE_UPLOAD"] = "2000"
os.environ["SOV_RATE_REPORTS"] = "2000"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import work  # noqa: E402

c = TestClient(app)
H = lambda t: {"Authorization": "Bearer " + t}  # noqa: E731

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def login(u, p):
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


T_ADM = login("admin@company.com", "Admin@2026#S9x!")
T_MGR = login("manager@company.com", "Manager@2026#K7p!")
T_E1 = login("employee@company.com", "Employee@2026#R4m!")
T_E2 = login("employee1@company.com", "Employee@2026#R4m!")
T_OP = login("operator@company.com", "Operator@2026#T8q!")
T_REV = login("reviewer@company.com", "Reviewer@2026#V6n!")

emps = {r["username"]: r for r in
        c.get("/api/v1/employees", headers=H(T_ADM)).json()["employees"]}
id_e1 = emps["employee@company.com"]["id"]
id_e2 = emps["employee1@company.com"]["id"]

DAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


# ---------------------------------------------------------------- 1. assign -> portal

print("== 1. manager assign -> employee portal ==")
TITLE = "TEST — workflow task (portal)"
r = c.post("/api/v1/work/tasks", headers=H(T_MGR), json={
    "title": TITLE, "instructions": "Do the thing carefully.",
    "assignee_id": id_e1, "priority": "high",
    "start_date": "2026-09-20", "due_date": "2026-09-30"})
check("manager creates task 200", r.status_code == 200, r.text)
t1 = r.json() if r.status_code == 200 else {}
check("task echoes priority + dates",
      t1.get("priority") == "high" and t1.get("start_date") == "2026-09-20"
      and t1.get("due_date") == "2026-09-30", str(t1))

mine = c.get("/api/v1/work/tasks", headers=H(T_E1)).json()["tasks"]
row = next((t for t in mine if t["title"] == TITLE), None)
check("employee sees the assigned task", row is not None)
if row:
    an = str(row.get("assignee_name") or "")
    cn = str(row.get("created_by_name") or "")
    check("task carries assignee_name (display name, not raw id)",
          bool(an) and not an.startswith("u-"), an)
    check("task carries created_by_name (assigned-by in portal)",
          bool(cn) and not cn.startswith("u-"), cn)
    check("priority/dates visible to employee",
          row.get("priority") == "high" and row.get("start_date") == "2026-09-20"
          and row.get("due_date") == "2026-09-30", str(row))

dash = c.get("/api/v1/work/me", headers=H(T_E1)).json()
lite = next((t for t in dash.get("tasks_open", [])
             if t.get("title") == TITLE), None)
check("dashboard lite task carries priority + assigned-by",
      lite is not None and lite.get("priority") == "high"
      and bool(lite.get("created_by_name")), str(lite))

tid = (t1 or {}).get("id")
if tid:
    def patch(tok, body):
        return c.patch(f"/api/v1/work/tasks/{tid}", headers=H(tok), json=body)

    check("assignee starts work 200",
          patch(T_E1, {"status": "in_progress", "progress": 40}).status_code == 200)
    check("assignee submits 200",
          patch(T_E1, {"status": "submitted", "progress": 100}).status_code == 200)
    check("assignee cannot self-approve 403",
          patch(T_E1, {"status": "approved"}).status_code == 403)
    r = patch(T_MGR, {"status": "needs_correction",
                      "review_remarks": "TEST — add readings"})
    check("manager requests correction 200", r.status_code == 200, r.text)
    back = next((t for t in c.get("/api/v1/work/tasks",
                                  headers=H(T_E1)).json()["tasks"]
                 if t["id"] == tid), None)
    check("correction + remarks visible to employee",
          back is not None and back["status"] == "needs_correction"
          and "readings" in (back.get("review_remarks") or ""), str(back))
    check("assignee resumes after correction 200",
          patch(T_E1, {"status": "in_progress"}).status_code == 200)
    check("assignee resubmits 200",
          patch(T_E1, {"status": "submitted", "progress": 100}).status_code == 200)
    r = patch(T_MGR, {"status": "approved",
                      "review_remarks": "TEST — verified"})
    check("manager approves 200", r.status_code == 200, r.text)
    done = next((t for t in c.get("/api/v1/work/tasks",
                                  headers=H(T_E1)).json()["tasks"]
                 if t["id"] == tid), None)
    check("approved task visible to employee as completed",
          done is not None and done["status"] == "approved", str(done))
    check("manager sees task in own scope with assignee name",
          any(t["id"] == tid and t.get("assignee_name")
              for t in c.get("/api/v1/work/tasks",
                             headers=H(T_MGR)).json()["tasks"]))


# ---------------------------------------------------------------- 2. schedules

print("== 2. schedule visibility + scope guards ==")
body_e1 = {"name": "TEST schedule e1", "shift": "General",
           "start": "08:30", "end": "17:00",
           "break_start": "12:30", "break_end": "13:00",
           "working_days": DAY, "holidays": ["2026-10-02"],
           "scope_type": "user", "scope_id": id_e1,
           "notes": "TEST per-employee roster"}
r = c.post("/api/v1/work/schedules", headers=H(T_ADM), json=body_e1)
check("admin creates per-employee schedule 200", r.status_code == 200, r.text)
sch_e1 = r.json() if r.status_code == 200 else {}

body_org = {"name": "TEST org schedule", "shift": "General",
            "start": "09:00", "end": "18:00",
            "working_days": DAY, "scope_type": "org", "scope_id": ""}
r = c.post("/api/v1/work/schedules", headers=H(T_ADM), json=body_org)
check("admin creates org-wide schedule 200", r.status_code == 200, r.text)
sch_org = r.json() if r.status_code == 200 else {}

mgr_rows = c.get("/api/v1/work/schedules", headers=H(T_MGR)).json()["schedules"]
check("manager sees own-dept member's personal schedule",
      any(s["id"] == sch_e1.get("id") for s in mgr_rows))
check("manager sees org-wide schedule row",
      any(s["id"] == sch_org.get("id") for s in mgr_rows))

e1_rows = c.get("/api/v1/work/schedules", headers=H(T_E1)).json()["schedules"]
e2_rows = c.get("/api/v1/work/schedules", headers=H(T_E2)).json()["schedules"]
check("employee sees own personal schedule row",
      any(s["id"] == sch_e1.get("id") for s in e1_rows))
check("other employee does NOT see that personal row",
      not any(s["id"] == sch_e1.get("id") for s in e2_rows))
check("employee sees org-wide row",
      any(s["id"] == sch_org.get("id") for s in e2_rows))

r = c.post("/api/v1/work/schedules", headers=H(T_E1),
           json={"name": "TEST self sched", "scope_type": "user",
                 "scope_id": id_e1})
check("employee cannot create schedule 403", r.status_code == 403,
      str(r.status_code))
r = c.post("/api/v1/work/schedules", headers=H(T_MGR),
           json={"name": "TEST org (deny)", "scope_type": "org",
                 "scope_id": ""})
check("manager cannot create org-wide schedule 403", r.status_code == 403,
      str(r.status_code))
r = c.post("/api/v1/work/schedules", headers=H(T_MGR),
           json={"name": "TEST team (deny)", "scope_type": "team",
                 "scope_id": "Quality"})
check("manager cannot target another department 403", r.status_code == 403,
      str(r.status_code))
r = c.post("/api/v1/work/schedules", headers=H(T_MGR),
           json={"name": "TEST team schedule Production", "shift": "General",
                 "start": "09:00", "end": "18:00", "working_days": DAY,
                 "scope_type": "team", "scope_id": "Production"})
check("manager creates own-department team schedule 200",
      r.status_code == 200, r.text)
team_name = (r.json() or {}).get("name", "")

r = c.patch(f"/api/v1/work/schedules/{sch_org.get('id')}", headers=H(T_MGR),
            json=body_org)
check(" manager cannot edit org-wide schedule 403", r.status_code == 403,
      str(r.status_code))
r = c.patch(f"/api/v1/work/schedules/{sch_e1.get('id')}", headers=H(T_MGR),
            json={**body_e1, "shift": "Late"})
check("manager edits own-dept personal schedule 200", r.status_code == 200,
      r.text)

s1 = c.get("/api/v1/work/schedule", headers=H(T_E1)).json()
check("E1 resolves assigned personal schedule",
      s1.get("source") == "assigned" and s1.get("name") == "TEST schedule e1",
      str((s1.get("source"), s1.get("name"))))
s2 = c.get("/api/v1/work/schedule", headers=H(T_E2)).json()
check("E2 resolves own team schedule (not E1's personal row)",
      s2.get("source") == "assigned" and s2.get("name") == team_name,
      str((s2.get("source"), s2.get("name"))))


# ---------------------------------------------------------------- 3. profile allow-list

print("== 3. profile self-edit allow-list ==")
before = c.get("/api/v1/employees/me", headers=H(T_E1)).json()
self_url = f"/api/v1/employees/{before['id']}"
r = c.patch(self_url, headers=H(T_E1), json={"fields": {"phone": "+91-9555000111"}})
check("employee updates own phone 200", r.status_code == 200, r.text)
r = c.patch(self_url, headers=H(T_E1), json={"fields": {"role": "ADMIN"}})
check("self role change rejected 403", r.status_code == 403, str(r.status_code))
c.patch(self_url, headers=H(T_E1), json={"fields": {"department": "Quality"}})
c.patch(self_url, headers=H(T_E1), json={"fields": {"emp_status": "SUSPENDED"}})
r = c.patch(self_url, headers=H(T_E1), json={"fields": {"employee_id": "EMP999"}})
check("self employee-id change rejected 403", r.status_code == 403,
      str(r.status_code))
after = c.get("/api/v1/employees/me", headers=H(T_E1)).json()
check("department unchanged after self edit",
      after.get("department") == before.get("department"),
      f"{before.get('department')} -> {after.get('department')}")
check("account status unchanged after self edit",
      after.get("emp_status") == before.get("emp_status"),
      f"{before.get('emp_status')} -> {after.get('emp_status')}")
check("employee id unchanged after self edit",
      after.get("employee_id") == before.get("employee_id"))
check("role unchanged after self edit",
      after.get("role") == before.get("role"))
check("own phone updated", after.get("phone") == "+91-9555000111",
      str(after.get("phone")))


# ---------------------------------------------------------------- 4. RBAC boundaries

print("== 4. RBAC boundaries ==")
check("reviewer analytics overview 403 (unchanged)",
      c.get("/api/v1/analytics/overview", headers=H(T_REV)).status_code == 403)
check("reviewer analytics criteria 200 (ReviewerView data source)",
      c.get("/api/v1/analytics/criteria", headers=H(T_REV)).status_code == 200)
check("reviewer /work/me 200 (ReviewerView data source)",
      c.get("/api/v1/work/me", headers=H(T_REV)).status_code == 200)
check("operator cannot create task 403",
      c.post("/api/v1/work/tasks", headers=H(T_OP),
             json={"title": "TEST — op", "assignee_id": id_e1}).status_code == 403)
check("employee cannot list employees 403",
      c.get("/api/v1/employees", headers=H(T_E1)).status_code == 403)
r = c.post("/api/v1/work/tasks", headers=H(T_MGR),
           json={"title": "TEST — e2 task", "assignee_id": id_e2})
t2 = r.json() if r.status_code == 200 else {}
check("employee cannot read another's tasks",
      c.get(f"/api/v1/work/tasks?user_id={id_e2}",
            headers=H(T_E1)).status_code in (403, 404))
check("employee cannot patch another's task 404",
      c.patch(f"/api/v1/work/tasks/{t2.get('id')}", headers=H(T_E1),
              json={"status": "in_progress"}).status_code == 404)
check("employee cannot read another's attendance",
      c.get(f"/api/v1/work/attendance?user_id={id_e2}",
            headers=H(T_E1)).status_code in (403, 404))


# ---------------------------------------------------------------- 5. seed idempotency

print("== 5. demo seed idempotency (two real runs) ==")

def snap():
    con = work._con()
    try:
        return {
            "tasks": con.execute(
                "SELECT COUNT(*) FROM tasks WHERE title LIKE 'DEMO%'"
            ).fetchone()[0],
            "schedules": con.execute(
                "SELECT COUNT(*) FROM schedules WHERE name LIKE 'DEMO%'"
            ).fetchone()[0],
            "attendance": con.execute(
                "SELECT COUNT(*) FROM attendance").fetchone()[0],
            "worksheets": con.execute(
                "SELECT COUNT(*) FROM worksheets WHERE title LIKE 'DEMO%'"
            ).fetchone()[0],
            "reports": con.execute(
                "SELECT COUNT(*) FROM reports").fetchone()[0],
        }
    finally:
        con.close()


def run_seed():
    p = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "seed_demo.py")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


rc1, out1 = run_seed()
check("seed run #1 exit 0", rc1 == 0, out1[-800:])
s2 = snap()
check("seed produced demo data (tasks + worksheets + attendance)",
      s2["tasks"] > 0 and s2["worksheets"] > 0 and s2["attendance"] > 0,
      str(s2))
rc2, out2 = run_seed()
check("seed run #2 exit 0", rc2 == 0, out2[-800:])
s3 = snap()
check("second run leaves all counts unchanged (idempotent)",
      s2 == s3, f"{s2} vs {s3}")


# ---------------------------------------------------------------- 6. UI routes + texts

print("== 6. UI route integrity + required texts ==")
def read_ui(rel):
    return open(os.path.join(ROOT, "frontend", "src", rel),
                encoding="utf-8").read()


nav_src = read_ui("nav.js")
main_src = read_ui("main.jsx")
ws_src = read_ui(os.path.join("pages", "Workspace.jsx"))
sw_src = read_ui(os.path.join("pages", "StartWork.jsx"))
i18n_src = read_ui("i18n.js")

nav_ids = set(re.findall(r"id:\s*['\"]([^'\"]+)['\"]", nav_src))
case_ids = set(re.findall(r"case\s+['\"]([^'\"]+)['\"]\s*:", main_src))
check("every NAV id has a route (no dead links)",
      not sorted(nav_ids - case_ids), str(sorted(nav_ids - case_ids)))
check("every route has a NAV entry (no orphan routes)",
      not sorted(case_ids - nav_ids), str(sorted(case_ids - nav_ids)))
print(f"    ({len(nav_ids)} NAV ids, {len(case_ids)} route cases)")

seg = ws_src.split("function ReviewerView", 1)[-1].split("export default", 1)[0]
check("ReviewerView exists and is not a ManagerView alias",
      "function ReviewerView" in ws_src and "return <ManagerView" not in seg)
check("ReviewerView makes no /analytics/overview call",
      "/analytics/overview" not in seg)
check("manager workspace renders Assign Work",
      "<AssignWork" in ws_src)
check("manager workspace has review actions",
      "requestCorrection" in ws_src and "'approved'" in ws_src)
check("StartWork status map covers correction + approved",
      "needs_correction: T(lang, 'correctionRequested')" in sw_src
      and "approved: T(lang, 'completed')" in sw_src)

ss_src = read_ui(os.path.join("pages", "Spreadsheets.jsx"))
rp_src = read_ui(os.path.join("pages", "Reports.jsx"))
check("spreadsheets empty-state hint exact text",
      "Upload a CSV or XLSX file to begin." in ss_src)
check("reports shows insufficient-data metric text",
      "insufficientData" in rp_src)

en_block = i18n_src.split("const EXTRA")[0]
new_keys = [
    "assignWork", "assignedBy", "assignedTo", "priority", "instructions",
    "startDate", "viewDetails", "hideDetails", "attachFile",
    "correctionRequested", "requestCorrection", "reviewRemarks",
    "noCompleted", "noOpenWork", "addSchedule", "editSchedule",
    "deleteSchedule", "scheduleName", "scope", "scopeUser", "scopeTeam",
    "scopeOrg", "assignedSchedule", "holidayList", "notes",
    "searchEmployees", "allDepartments", "workItems", "demoBadge",
    "demoDataNote", "auditLog", "insufficientData",
]
missing = [k for k in new_keys if not re.search(rf"\b{k}:", en_block)]
check("all demo i18n keys defined in `en`", not missing, str(missing))


print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
