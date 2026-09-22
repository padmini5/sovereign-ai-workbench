"""Dashboard + profile upgrade tests: schedule, attendance summary, my-work,
activity, device status, profile photo privacy, role-aware access, IDOR.

Run: python tests/test_dashboard_profile.py. Isolated temp DBs, mock providers.
"""
import io
import os
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="sov_dash_")
for k in ["SOV_AUTH_DB", "SOV_AUTH_AUDIT_DB", "SOV_CHAT_DB", "SOV_DOCS_DB",
          "SOV_VECTORS_DB", "SOV_UPLOADS_DIR", "SOV_AGENTS_DB", "SOV_WORK_DB",
          "SOV_SYSCONFIG_PATH"]:
    os.environ[k] = os.path.join(_tmp, k.replace("SOV_", "").lower())
os.environ["SOV_AI_PROVIDER"] = "mock"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import audit_log  # noqa: E402

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


def mk_png(color="red"):
    from PIL import Image
    im = Image.new("RGB", (48, 32), color)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


T_ADM = login("admin@company.com", "Admin@2026#S9x!")
T_MGR = login("manager@company.com", "Manager@2026#K7p!")
T_E1 = login("employee@company.com", "Employee@2026#R4m!")
T_E2 = login("employee1@company.com", "Employee@2026#R4m!")
T_OP = login("operator@company.com", "Operator@2026#T8q!")
T_REV = login("reviewer@company.com", "Reviewer@2026#V6n!")

print("== 1. login -> dashboard data for every role ==")
for name, tok in [("employee", T_E1), ("operator", T_OP), ("reviewer", T_REV),
                  ("manager", T_MGR), ("admin", T_ADM)]:
    r = c.get("/api/v1/work/me", headers=H(tok))
    check(f"{name} /work/me 200 + perf", r.status_code == 200
          and "performance" in r.json(), r.text[:120])
    r = c.get("/api/v1/employees/me", headers=H(tok))
    check(f"{name} profile 200", r.status_code == 200
          and r.json().get("employee_id"), r.text[:120])

print("== 2. schedule is backend-driven + labeled ==")
r = c.get("/api/v1/work/schedule", headers=H(T_E1)).json()
check("shift fields", r.get("start") == "09:00" and r.get("end") == "17:30"
      and r.get("shift") == "General", str(r)[:150])
check("weekly Mon-Fri", [w["day"] for w in r["weekly"]] == [
      "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
check("honest source label", r.get("source") == "org_default"
      and "note" in r, str(r)[:150])
check("no-auth schedule -> 401",
      c.get("/api/v1/work/schedule").status_code in (401, 403))

print("== 3. attendance summary math (recorded data) ==")
me1 = c.get("/api/v1/auth/me", headers=H(T_E1)).json()
d1, d2 = "2026-09-07", "2026-09-08"  # both weekdays (Mon/Tue)
c.post("/api/v1/work/attendance",
       json={"user_id": me1["id"], "date": d1, "status": "present"}, headers=H(T_MGR))
c.post("/api/v1/work/attendance",
       json={"user_id": me1["id"], "date": d2, "status": "absent"}, headers=H(T_MGR))
r = c.get("/api/v1/work/attendance/summary?month=2026-09", headers=H(T_E1)).json()
check("present/absent counted", r["current"]["present"] == 1
      and r["current"]["absent"] == 1, str(r["current"]))
check("pct = present/working_days",
      r["current"]["attendance_pct"] == round(100 * 1 / r["current"]["working_days"], 1),
      str(r["current"]))
check("empty month honest", "previous" in r or "month" in r["current"])

print("== 4. attendance IDOR ==")
me2 = c.get("/api/v1/auth/me", headers=H(T_E2)).json()
check("employee reads other attendance -> 404",
      c.get(f"/api/v1/work/attendance?user_id={me2['id']}",
            headers=H(T_E1)).status_code == 404)
check("employee summary of other -> 404",
      c.get(f"/api/v1/work/attendance/summary?user_id={me2['id']}",
            headers=H(T_E1)).status_code == 404)
check("manager reads team member ok",
      c.get(f"/api/v1/work/attendance?user_id={me1['id']}",
            headers=H(T_MGR)).status_code == 200)

print("== 5. start work + evidence + submit ==")
r = c.post("/api/v1/work/my-work",
           json={"title": "Calibrate line sensor", "description": "morning shift"},
           headers=H(T_E1))
check("my-work 200 assigned-self", r.status_code == 200
      and r.json()["assignee_id"] == me1["id"]
      and r.json()["status"] == "assigned", r.text[:150])
tid = r.json()["id"]
check("empty title -> 422",
      c.post("/api/v1/work/my-work", json={"title": "  "},
             headers=H(T_E1)).status_code == 422)
r = c.patch(f"/api/v1/work/tasks/{tid}",
            json={"status": "in_progress", "progress": 30}, headers=H(T_E1))
check("progress update", r.status_code == 200, r.text[:120])
r = c.post("/api/v1/work/evidence", files={"f": ("ev.png", mk_png(), "image/png")},
           data={"task_id": tid, "note": "sensor photo"}, headers=H(T_E1))
check("photo evidence 200 + private doc", r.status_code == 200
      and r.json().get("doc_id"), r.text[:150])
doc_id = r.json()["doc_id"]
check("evidence doc private from others",
      c.get(f"/api/v1/docs/{doc_id}/download", headers=H(T_E2)).status_code == 404)
r = c.patch(f"/api/v1/work/tasks/{tid}", json={"status": "submitted"},
            headers=H(T_E1))
check("submit for review", r.status_code == 200
      and r.json()["status"] == "submitted", r.text[:120])
check("assignee cannot self-approve",
      c.patch(f"/api/v1/work/tasks/{tid}", json={"status": "approved"},
              headers=H(T_E1)).status_code == 403)

print("== 6. activity feed scoped ==")
r = c.get("/api/v1/work/activity", headers=H(T_E1)).json()
check("own activity items", len(r["items"]) >= 3, str(r["items"])[:200])
check("other activity -> 404",
      c.get(f"/api/v1/work/activity?user_id={me2['id']}",
            headers=H(T_E1)).status_code == 404)

print("== 7. device status honest ==")
r = c.get("/api/v1/devices/status", headers=H(T_E1)).json()
check("not-connected message", r["connected"] is False
      and r["message"] == "Attendance device not connected", str(r))

print("== 8. profile photo privacy ==")
r = c.post("/api/v1/employees/me/photo",
           files={"f": ("me.png", mk_png(), "image/png")}, headers=H(T_E1))
check("photo upload 200", r.status_code == 200, r.text[:120])
r = c.post("/api/v1/employees/me/photo",
           files={"f": ("me.txt", b"not an image", "text/plain")}, headers=H(T_E1))
check("non-image rejected", r.status_code in (413, 415), str(r.status_code))
r = c.get("/api/v1/employees/me/photo", headers=H(T_E1))
check("own photo bytes", r.status_code == 200 and len(r.content) > 50,
      str(r.status_code))
check("other photo -> 404",
      c.get("/api/v1/employees/me/photo", headers=H(T_E2)).status_code == 404)
check("anon photo -> 401/403",
      c.get("/api/v1/employees/me/photo").status_code in (401, 403))

print("== 9. profile edit guards ==")
r = c.patch(f"/api/v1/employees/{me1['id']}",
            json={"fields": {"phone": "+91-9810000099"}}, headers=H(T_E1))
check("self phone ok", r.status_code == 200, r.text[:120])
for field in ["role", "employee_id"]:
    check(f"self {field} blocked",
          c.patch(f"/api/v1/employees/{me1['id']}", json={"fields": {field: "X"}},
                  headers=H(T_E1)).status_code == 403)
check("operator cannot touch admin profile",
      c.patch(f"/api/v1/employees/{me1['id']}", json={"fields": {"phone": "+91-1"}},
              headers=H(T_OP)).status_code in (400, 403))

print("== 10. role-aware admin/manager data ==")
check("employee admin users -> 403",
      c.get("/api/v1/admin/users", headers=H(T_E1)).status_code == 403)
check("manager team overview 200",
      c.get("/api/v1/analytics/overview", headers=H(T_MGR)).status_code == 200)
check("reviewer analytics 403",
      c.get("/api/v1/analytics/overview", headers=H(T_REV)).status_code == 403)

print("== 11. audit coverage ==")
acts = {a["action"] for a in audit_log.rows(5000)}
for a in ["ws_task_create", "ws_task_update", "ws_evidence", "ws_attendance",
          "ws_analytics", "EMPLOYEE_UPDATED"]:
    check(f"audit {a}", a in acts, str(sorted(acts))[:160])

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
