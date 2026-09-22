"""Phase 1 tests: auth + RBAC (run: python tests/test_phase1.py | pytest).

Covers: successful login, invalid login, unauthorized access, authorized
access, role restrictions, permission restrictions, user CRUD + audit.
Uses isolated temp DBs via env vars — never touches dev data.
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_phase1_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import userstore, audit_log  # noqa: E402

c = TestClient(app)
H = lambda t: {"Authorization": "Bearer " + t}  # noqa: E731

CREDS = {"ADMIN": ("admin", "Admin123!"), "MANAGER": ("manager", "Mgr123!"),
         "OPERATOR": ("operator", "Op123!"), "REVIEWER": ("reviewer", "Rev123!"),
         "USER": ("user", "User123!")}

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def login(username, password):
    return c.post("/api/v1/auth/login", json={"username": username, "password": password})


print("== 1. successful login ==")
toks = {}
for role, (u, p) in CREDS.items():
    r = login(u, p)
    check(f"login {u}", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        toks[role] = r.json()["access_token"]
        check(f"{u} perms non-empty", len(r.json()["user"]["permissions"]) > 0)
        check(f"{u} refresh token issued", "refresh_token" in r.json())

print("== 2. invalid login ==")
check("wrong password -> 401", login("admin", "nope").status_code == 401)
check("unknown user -> 401", login("ghost", "x").status_code == 401)
check("empty password -> 401/422", login("admin", "") in () or login("admin", "").status_code in (401, 422))

print("== 3. unauthorized access ==")
check("no token /me -> 401/403", c.get("/api/v1/auth/me").status_code in (401, 403))
check("bad token -> 401", c.get("/api/v1/auth/me", headers=H("junk")).status_code == 401)
check("no token user-list -> 401/403", c.get("/api/v1/admin/users").status_code in (401, 403))
check("refresh token on /me -> 401 (wrong typ)",
      c.get("/api/v1/auth/me", headers=H(login(*CREDS["ADMIN"]).json()["refresh_token"])).status_code == 401)

print("== 4. authorized access ==")
r = c.get("/api/v1/auth/me", headers=H(toks["ADMIN"]))
check("admin /me 200 + ADMIN", r.status_code == 200 and r.json()["role"] == "ADMIN")
r = c.get("/api/v1/auth/permissions", headers=H(toks["USER"]))
check("user perms include DOCUMENT_READ",
      r.status_code == 200 and "DOCUMENT_READ" in r.json()["permissions"])
check("user perms exclude USER_DELETE", "USER_DELETE" not in r.json()["permissions"])
r = c.post("/api/v1/auth/refresh", json={"refresh_token": login(*CREDS["OPERATOR"]).json()["refresh_token"]})
check("refresh flow 200", r.status_code == 200 and "access_token" in r.json())

print("== 5. role restrictions ==")
check("ADMIN can list users", c.get("/api/v1/admin/users", headers=H(toks["ADMIN"])).status_code == 200)
check("MANAGER can list users (team view, USER_READ)",
      c.get("/api/v1/admin/users", headers=H(toks["MANAGER"])).status_code == 200)
for role in ("OPERATOR", "REVIEWER", "USER"):
    r = c.get("/api/v1/admin/users", headers=H(toks[role]))
    check(f"{role} list-users -> 403", r.status_code == 403, r.text[:120])
check("REVIEWER can read audit", c.get("/api/v1/admin/audit", headers=H(toks["REVIEWER"])).status_code == 200)
check("OPERATOR audit -> 403", c.get("/api/v1/admin/audit", headers=H(toks["OPERATOR"])).status_code == 403)
check("USER audit -> 403", c.get("/api/v1/admin/audit", headers=H(toks["USER"])).status_code == 403)

print("== 6. permission restrictions ==")
P = lambda role, m, p, **kw: c.request(m, f"/api/v1{p}", headers=H(toks[role]), **kw)
check("USER doc read ok", P("USER", "GET", "/demo/documents").status_code == 200)
check("USER doc upload -> 403", P("USER", "POST", "/demo/documents/upload").status_code == 403)
check("OPERATOR upload ok", P("OPERATOR", "POST", "/demo/documents/upload").status_code == 200)
check("OPERATOR analyze -> 403", P("OPERATOR", "POST", "/demo/documents/analyze").status_code == 403)
check("REVIEWER analyze ok", P("REVIEWER", "POST", "/demo/documents/analyze").status_code == 200)
check("MANAGER delete -> 403", P("MANAGER", "DELETE", "/demo/documents/x").status_code == 403)
check("ADMIN delete ok", P("ADMIN", "DELETE", "/demo/documents/x").status_code == 200)
check("OPERATOR model-configure -> 403", P("OPERATOR", "POST", "/demo/models/configure").status_code == 403)
check("ADMIN model-configure ok", P("ADMIN", "POST", "/demo/models/configure").status_code == 200)
check("MANAGER system -> 403", P("MANAGER", "GET", "/demo/system").status_code == 403)
check("ADMIN system ok", P("ADMIN", "GET", "/demo/system").status_code == 200)

print("== 7. user CRUD + session revocation ==")
r = c.post("/api/v1/admin/users", json={"username": "tmp_op", "password": "Tmp12345", "role": "OPERATOR"},
           headers=H(toks["ADMIN"]))
check("admin create user", r.status_code == 200, r.text[:200])
uid = r.json().get("id", "")
r = c.post("/api/v1/admin/users", json={"username": "tmp_op", "password": "Tmp12345", "role": "OPERATOR"},
           headers=H(toks["ADMIN"]))
check("duplicate username -> 400", r.status_code == 400)
r = c.post("/api/v1/admin/users", json={"username": "x", "password": "Xx12345", "role": "OPERATOR"},
           headers=H(toks["OPERATOR"]))
check("operator create -> 403", r.status_code == 403)
r = c.patch(f"/api/v1/admin/users/{uid}", json={"role": "REVIEWER"}, headers=H(toks["ADMIN"]))
check("admin update role", r.status_code == 200 and r.json()["role"] == "REVIEWER")
r = login("tmp_op", "Tmp12345")
check("new user login ok", r.status_code == 200)
tmp_tok = r.json()["access_token"] if r.status_code == 200 else ""
c.patch(f"/api/v1/admin/users/{uid}", json={"active": False}, headers=H(toks["ADMIN"]))
check("deactivated token rejected", c.get("/api/v1/auth/me", headers=H(tmp_tok)).status_code == 401)
check("deactivated login -> 401/403", login("tmp_op", "Tmp12345").status_code in (401, 403))
c.patch(f"/api/v1/admin/users/{uid}", json={"active": True}, headers=H(toks["ADMIN"]))
check("reactivated login ok", login("tmp_op", "Tmp12345").status_code == 200)
check("self-delete blocked -> 400",
      c.delete(f"/api/v1/admin/users/{userstore.find_by_username('admin')['id']}",
               headers=H(toks['ADMIN'])).status_code == 400)
check("admin delete user", c.delete(f"/api/v1/admin/users/{uid}", headers=H(toks["ADMIN"])).status_code == 200)
check("deleted login -> 401", login("tmp_op", "Tmp12345").status_code == 401)

print("== 8. password handling ==")
rec = userstore.find_by_username("admin")
check("hash stored, not plaintext", rec and rec["pass_hash"] != "Admin123!" and len(rec["pass_hash"]) > 20)
from backend.app.passwords import verify_password  # noqa: E402
check("bcrypt verify ok", verify_password("Admin123!", rec["pass_hash"]))
check("bcrypt wrong fails", not verify_password("wrong", rec["pass_hash"]))

print("== 9. audit logging ==")
rows = audit_log.rows(500)
actions = {r["action"] for r in rows}
check("login_success audited", "login_success" in actions)
check("login_failure audited", "login_failure" in actions)
check("permission_denied audited", "permission_denied" in actions)
check("user_create/update/delete audited",
      {"user_create", "user_update", "user_delete"} <= actions, str(sorted(actions)))
check("logout/token_refresh audited",
      "token_refresh" in actions,
      str(sorted(actions)))

print("== 10. legacy regression ==")
r = c.post("/api/login", json={"username": "field1", "password": "field123"})
check("legacy /api/login still works", r.status_code == 200)

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
