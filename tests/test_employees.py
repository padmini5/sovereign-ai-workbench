"""Employee profiles + phone OTP reset + RBAC privacy tests.

Run: python tests/test_employees.py. Isolated temp DBs, mock AI providers.
Covers: new strong demo creds, legacy compat, OTP reset, admin create,
role-escalation block, IDOR/blood-group privacy, password change, audits.
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_emp_")
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
    assert r.status_code == 200, (u, r.status_code, r.text)
    return r.json()["access_token"]


CREDS = {
    "admin@company.com": ("Admin@2026#S9x!", "ADMIN"),
    "manager@company.com": ("Manager@2026#K7p!", "MANAGER"),
    "employee@company.com": ("Employee@2026#R4m!", "EMPLOYEE"),
    "operator@company.com": ("Operator@2026#T8q!", "OPERATOR"),
    "reviewer@company.com": ("Reviewer@2026#V6n!", "REVIEWER"),
}

print("== 1. new demo credentials + dashboards ==")
toks = {}
for u, (p, role) in CREDS.items():
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    check(f"login {u}", r.status_code == 200 and r.json()["user"]["role"] == role,
          r.text[:120])
    if r.status_code == 200:
        toks[u] = r.json()["access_token"]
        me = c.get("/api/v1/work/me", headers=H(toks[u]))
        check(f"dashboard {u}", me.status_code == 200, me.text[:100])

print("== 2. old weak passwords rejected; legacy intact ==")
check("old employee pw -> 401",
      c.post("/api/v1/auth/login",
             json={"username": "employee@company.com", "password": "employee123"}).status_code == 401)
for u, p in [("field1", "field123"), ("process1", "proc123"), ("safety1", "safe123"),
             ("manager1", "mgr123"), ("admin1", "adm123"), ("audit1", "aud123")]:
    check(f"legacy {u}",
          c.post("/api/v1/auth/login", json={"username": u, "password": p}).status_code == 200)

print("== 3. phone OTP reset (generic + demo) ==")
r = c.post("/api/v1/auth/password/forgot/request",
           json={"email": "ghost@company.com", "phone": "+91-9000000000"}).json()
check("unknown account generic", r.get("ok") is True and "demo_otp" not in r, str(r)[:120])
r = c.post("/api/v1/auth/password/forgot/request",
           json={"email": "employee@company.com", "phone": "+91-9810000003"}).json()
check("known account generic+demo otp", r.get("ok") is True and "demo_otp" in r
      and "no SMS was sent" in r.get("demo_notice", ""), str(r)[:150])
otp = r["demo_otp"]
r = c.post("/api/v1/auth/password/forgot/confirm",
           json={"email": "employee@company.com", "otp": "000000",
                 "new_password": "Employee@2026#R4m!", "confirm_password": "Employee@2026#R4m!"})
check("wrong OTP rejected", r.status_code == 400)
r = c.post("/api/v1/auth/password/forgot/confirm",
           json={"email": "employee@company.com", "otp": otp,
                 "new_password": "Employee@2026#R4m!", "confirm_password": "Employee@2026#R4m!"})
check("OTP confirm 200", r.status_code == 200, r.text[:120])
toks["employee@company.com"] = login("employee@company.com", "Employee@2026#R4m!")
check("re-login after reset", True)

print("== 4. admin creates employee; new login works ==")
r = c.post("/api/v1/employees",
           json={"username": "demo.employee@company.com", "password": "DemoEmp@2026#1A",
                 "role": "EMPLOYEE",
                 "profile": {"first_name": "Demo", "last_name": "Employee",
                             "phone": "+91-9820000010", "department": "Production",
                             "designation": "Technician", "team": "Line-A"}},
           headers=H(toks["admin@company.com"]))
check("admin create 200", r.status_code == 200
      and "created successfully" in r.text, r.text[:150])
tn = login("demo.employee@company.com", "DemoEmp@2026#1A")
check("new employee login", True)
check("duplicate email rejected",
      c.post("/api/v1/employees",
             json={"username": "demo.employee@company.com", "password": "DemoEmp@2026#1B",
                   "role": "EMPLOYEE", "profile": {"first_name": "D", "last_name": "E",
                   "phone": "+91-9820000011"}},
             headers=H(toks["admin@company.com"])).status_code == 400)
check("admin-role creation blocked",
      c.post("/api/v1/employees",
             json={"username": "evil@company.com", "password": "EvilAdmin@2026#1A",
                   "role": "ADMIN", "profile": {"first_name": "E", "last_name": "V",
                   "phone": "+91-9820000012"}},
             headers=H(toks["admin@company.com"])).status_code == 400)

print("== 5. create RBAC: manager/employee/operator/reviewer denied ==")
body = {"username": "nope@company.com", "password": "NopeEmp@2026#1A", "role": "EMPLOYEE",
        "profile": {"first_name": "N", "last_name": "P", "phone": "+91-9820000013"}}
for u in ["manager@company.com", "employee@company.com", "operator@company.com",
          "reviewer@company.com"]:
    check(f"{u.split('@')[0]} create -> 403",
          c.post("/api/v1/employees", json=body, headers=H(toks[u])).status_code == 403)

print("== 6. self-update + escalation block + privacy ==")
nid = c.get("/api/v1/auth/me", headers=H(tn)).json()["id"]
r = c.patch(f"/api/v1/employees/{nid}", json={"fields": {"phone": "+91-9820000020"}},
            headers=H(tn))
check("self phone update", r.status_code == 200 and r.json().get("phone") == "+91-9820000020",
      r.text[:120])
check("self role escalation -> 403",
      c.patch(f"/api/v1/employees/{nid}", json={"fields": {"role": "ADMIN"}},
              headers=H(tn)).status_code == 403)
adm_id = c.get("/api/v1/auth/me", headers=H(toks["admin@company.com"])).json()["id"]
r = c.get(f"/api/v1/employees/{adm_id}", headers=H(tn))
check("IDOR admin profile denied", r.status_code in (403, 404), str(r.status_code))
# blood group: admin sets own, employee must not see it
c.patch(f"/api/v1/employees/{adm_id}", json={"fields": {"blood_group": "O+"}},
        headers=H(toks["admin@company.com"]))
lst = c.get("/api/v1/employees", headers=H(toks["manager@company.com"])).json()["employees"]
admin_row = [e for e in lst if e["id"] == adm_id]
check("blood group hidden from others",
      bool(admin_row) and "blood_group" not in admin_row[0], str(admin_row)[:150])
own = c.get("/api/v1/employees/me", headers=H(toks["admin@company.com"])).json()
check("self sees own blood group", own.get("blood_group") == "O+", str(own)[:100])

print("== 7. password change + admin reset ==")
r = c.post("/api/v1/auth/password/change",
           json={"current_password": "wrong", "new_password": "DemoEmp@2026#2B",
                 "confirm_password": "DemoEmp@2026#2B"}, headers=H(tn))
check("wrong current -> 401", r.status_code == 401)
r = c.post("/api/v1/auth/password/change",
           json={"current_password": "DemoEmp@2026#1A", "new_password": "DemoEmp@2026#2B",
                 "confirm_password": "DemoEmp@2026#2B"}, headers=H(tn))
check("change 200", r.status_code == 200, r.text[:120])
tn = login("demo.employee@company.com", "DemoEmp@2026#2B")
check("login with changed pw", True)
r = c.post(f"/api/v1/employees/{nid}/reset-password", json={"password": "DemoEmp@2026#3C"},
           headers=H(toks["admin@company.com"]))
check("admin reset 200 + demo label", r.status_code == 200 and "demo" in r.text.lower(),
      r.text[:150])
tn = login("demo.employee@company.com", "DemoEmp@2026#3C")
check("login with reset pw", True)

print("== 8. suspend/archive ==")
r = c.post(f"/api/v1/employees/{nid}/suspend", headers=H(toks["admin@company.com"]))
check("suspend 200", r.status_code == 200)
check("suspended login -> 403",
      c.post("/api/v1/auth/login",
             json={"username": "demo.employee@company.com",
                   "password": "DemoEmp@2026#3C"}).status_code == 403)

print("== 9. audit trail (no secrets) ==")
acts = {a["action"] for a in audit_log.rows(3000)}
for a in ["EMPLOYEE_CREATED", "EMPLOYEE_UPDATED", "PASSWORD_CHANGED",
          "PASSWORD_RESET_REQUESTED", "PASSWORD_RESET_COMPLETED", "ACCOUNT_SUSPENDED"]:
    check(f"audit {a}", a in acts)
secrets_leak = any("DemoEmp@" in (a.get("detail") or "") or "R4m" in (a.get("detail") or "")
                   for a in audit_log.rows(3000))
check("no password in audit details", not secrets_leak)

print("== 10. device by employee_id ==")
import time
os.environ["SOV_DEVICE_KEYS"] = "test-device-key"
me = c.get("/api/v1/employees/me",
           headers=H(login("employee@company.com", "Employee@2026#R4m!"))).json()
r = c.post("/api/v1/devices/attendance",
           json={"employee": me["employee_id"], "timestamp": time.time(),
                 "device_id": "DEVICE-01", "event": "CHECK_IN"},
           headers={"X-Device-Key": "test-device-key"})
check("device via EMP id", r.status_code == 200, r.text[:120])

# cleanup
for u in ["demo.employee@company.com"]:
    rows = c.get("/api/v1/admin/users", headers=H(toks["admin@company.com"])).json()["users"]
    uid = [x["id"] for x in rows if x["username"] == u]
    if uid:
        c.delete(f"/api/v1/admin/users/{uid[0]}", headers=H(toks["admin@company.com"]))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
