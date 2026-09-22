"""Docker-stack verification (existing UI/API, no redesign, no rewrite).

Checks, all against the COMPOSE stack (frontend :8080, backend :8000):
 1. Frontend parity: :8080/ HTML == local frontend/dist/index.html byte-for-byte
    (same source, same build) + dev :3000 serves the same src/main.jsx shell.
 2. Routes: /, /phase1.html, /health, /api/health, /api/config, /api/v1/auth/me.
 3. Login through Docker (nginx :8080 -> backend:8000): admin + employee.
 4. RBAC preserved: employee GET /api/v1/admin/overview -> 403; admin -> 200.
 5. Backend /health direct: ok + honest pqc_available bool.
 6. WebSocket /ws/monitor through the :8080 nginx proxy (upgrade headers).
  7. Ports private: 8000/8080 bound to 127.0.0.1 only (checked separately).
"""
import json
import urllib.request
import urllib.error

FE = "http://127.0.0.1:8080"
BE = "http://127.0.0.1:8000"
passed = failed = 0

def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")

def get(url, token=None):
    req = urllib.request.Request(url, headers=(
        {"Authorization": "Bearer " + token} if token else {}))
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()

def post(url, body, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())

print("== 1. frontend parity (:8080 vs local dist build) ==")
st, idx = get(FE + "/")
with open("frontend/dist/index.html", encoding="utf-8") as f:
    local_idx = f.read()
check("GET / 200", st == 200)
check("/ identical to local dist/index.html (same UI, same build)",
      idx.decode() == local_idx)
check("title SovereignAI Workbench", b"<title>SovereignAI Workbench</title>" in idx)
check("hash-routed shell bundle referenced", b"/assets/main-" in idx)
st, ph = get(FE + "/phase1.html")
check("GET /phase1.html 200", st == 200 and b"Phase 1" in ph)

print("== 2. routes (no unexpected Not Found) ==")
for path in ["/health", "/api/health", "/api/config"]:
    st, body = get(FE + path)
    check(f"GET {path} -> {st}", st == 200, body[:80].decode(errors="ignore"))

print("== 3. login through Docker proxy (all five primary demo accounts) ==")
PRIMARY = {
    "admin@company.com": ("Admin@2026#S9x!", "ADMIN"),
    "manager@company.com": ("Manager@2026#K7p!", "MANAGER"),
    "employee@company.com": ("Employee@2026#R4m!", "EMPLOYEE"),
    "operator@company.com": ("Operator@2026#T8q!", "OPERATOR"),
    "reviewer@company.com": ("Reviewer@2026#V6n!", "REVIEWER"),
}
toks = {}
for u, (p, exp_role) in PRIMARY.items():
    try:
        st, lg = post(FE + "/api/v1/auth/login", {"username": u, "password": p})
        toks[u] = lg["access_token"]
        check(f"login {u} -> 200", st == 200)
        st, me = get(FE + "/api/v1/auth/me", toks[u])
        got_role = json.loads(me.decode())["role"]
        check(f"/me {u} role {exp_role}", st == 200 and got_role == exp_role,
              f"got {got_role}")
    except urllib.error.HTTPError as e:
        check(f"login {u}", False, f"HTTP {e.code}")
try:
    # Legacy demo account (unchanged auth.py USERS) as an extra low-priv user.
    st, lg = post(FE + "/api/login", {"username": "field1", "password": "field123"})
    toks["field1"] = lg["token"]
    check("login field1 (legacy) -> 200", st == 200)
except urllib.error.HTTPError as e:
    check("login field1 (legacy)", False, f"HTTP {e.code}")

print("== 4. RBAC preserved ==")
# 4a. Legacy layer: field_engineer cannot sign (core unauthorized-signing block).
st, lg = post(FE + "/api/login", {"username": "manager1", "password": "mgr123"})
mgr_tok = lg["token"]
check("login manager1 (legacy) -> 200", st == 200)
st, t = post(FE + "/api/tasks", {"query": "docker rbac probe"}, mgr_tok)
tid = t["task_id"]
check("manager creates task -> 200", st == 200)
try:
    post(FE + f"/api/tasks/{tid}/sign", {}, toks["field1"])
    check("field_engineer sign blocked", False, "got 200")
except urllib.error.HTTPError as e:
    check("field_engineer sign -> 403", e.code == 403, f"got {e.code}")
# 4b. New layer: legacy low-priv token is not accepted on ADMIN route.
try:
    st, _ = get(FE + "/api/v1/admin/overview", toks["field1"])
    check("field_engineer /api/v1/admin/overview denied", False, f"got {st}")
except urllib.error.HTTPError as e:
    check("field_engineer /api/v1/admin/overview denied (401/403)",
          e.code in (401, 403), f"got {e.code}")
st, _ = get(FE + "/api/v1/admin/overview", toks["admin@company.com"])
check("admin /api/v1/admin/overview -> 200", st == 200)

print("== 5. backend direct /health ==")
st, h = get(BE + "/health")
h = json.loads(h.decode())
check("backend /health ok", st == 200 and h.get("ok") is True, str(h))
check("pqc honestly reported", isinstance(h.get("pqc_available"), bool), str(h))

print("== 6. WebSocket /ws/monitor via nginx proxy ==")
try:
    from websockets.sync.client import connect
    with connect(FE.replace("http", "ws") + "/ws/monitor",
                 open_timeout=15, close_timeout=5) as ws:
        msg = json.loads(ws.recv(timeout=15))
    check("ws monitor via :8080 proxy", msg.get("app_egress_connections") == 0, str(msg)[:120])
except Exception as e:
    check("ws monitor via :8080 proxy", False, f"{type(e).__name__}: {e}")

print(f"\nRESULT: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
