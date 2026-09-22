"""Live runtime smoke test (real server on :8000 + vite proxy on :3000).
Mirrors the exact calls the workbench frontend makes per role."""
import json
import sys
import urllib.request
import urllib.error

B = "http://localhost:3000"  # through the vite dev proxy (what the SPA uses)
RAW = "http://localhost:8000"
passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def call(method, path, body=None, token=None, base=B, form=None):
    url = base + path
    data = None
    headers = {}
    if form is not None:
        data = form
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw[:80]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:120]
    except Exception as e:
        return 0, str(e)


print("== 0. vite proxy ==")
st, j = call("GET", "/api/config")
check("proxy /api/config 200", st == 200 and j.get("demo_login_enabled") in (True, False), f"{st} {j}")

print("== 1. v1 logins (all 11 accounts) ==")
CREDS = [
    ("admin", "Admin123!"), ("manager", "Mgr123!"), ("operator", "Op123!"),
    ("reviewer", "Rev123!"), ("user", "User123!"),
    ("field1", "field123"), ("process1", "proc123"), ("safety1", "safe123"),
    ("manager1", "mgr123"), ("admin1", "adm123"), ("audit1", "aud123"),
]
toks = {}
for u, p in CREDS:
    st, j = call("POST", "/api/v1/auth/login", {"username": u, "password": p})
    ok = st == 200 and "access_token" in j and j["user"].get("permissions") is not None
    check(f"login {u}", ok, f"{st} {str(j)[:120]}")
    if ok:
        toks[u] = (j["access_token"], j["user"])

print("== 2. wrong password -> 401 honest ==")
st, j = call("POST", "/api/v1/auth/login", {"username": "admin", "password": "nope"})
check("bad login 401", st == 401, str(st))

print("== 3. per-role pages (exact frontend calls) ==")
# admin: dashboard status + admin tabs + users + audit
a = toks["admin"][0]
for path in ["/api/v1/auth/me", "/api/v1/ai/status", "/api/v1/roles",
             "/api/v1/admin/users", "/api/v1/admin/overview", "/api/v1/admin/config",
             "/api/v1/admin/audit", "/api/v1/docs", "/api/v1/agents", "/api/v1/agents/runs",
             "/api/v1/ai/tools", "/api/v1/voice/status", "/api/v1/auth/language"]:
    st, j = call("GET", path, token=a)
    check(f"admin {path}", st == 200, f"{st} {str(j)[:100]}")

# security_admin: admin system + users + audit OK; docs list must 403 (content-blind)
s = toks["admin1"][0]
st, _ = call("GET", "/api/v1/admin/overview", token=s)
check("security_admin overview 200", st == 200, str(st))
st, _ = call("GET", "/api/v1/admin/users", token=s)
check("security_admin users 200", st == 200, str(st))
st, _ = call("GET", "/api/v1/admin/audit", token=s)
check("security_admin audit 200", st == 200, str(st))
st, j = call("GET", "/api/v1/docs", token=s)
check("security_admin docs 403 (content-blind)", st == 403, str(st))

# auditor: audit 200, docs 403, agents 403
au = toks["audit1"][0]
st, _ = call("GET", "/api/v1/admin/audit", token=au)
check("auditor audit 200", st == 200, str(st))
st, _ = call("GET", "/api/v1/docs", token=au)
check("auditor docs 403", st == 403, str(st))
st, _ = call("GET", "/api/v1/agents", token=au)
check("auditor agents 403", st == 403, str(st))

# field_engineer: docs 200, chat 200, audit 403, admin overview 403
f = toks["field1"][0]
st, _ = call("GET", "/api/v1/docs", token=f)
check("field_engineer docs 200", st == 200, str(st))
# Chat: honest behavior. The daemon is reachable but NO model is installed
# in this environment (ollama list is empty), so the backend must return an
# honest 502 "AI provider unavailable: ..." — never a fake answer. Tests set
# SOV_AI_PROVIDER=mock, the live box does not.
st, j = call("POST", "/api/v1/ai/chat",
             {"messages": [{"role": "user", "content": "ping"}], "mode": "general", "lang": "en"},
             token=f)
if st == 200:
    check("field_engineer chat 200", "message" in j, str(j)[:140])
else:
    check("field_engineer chat honest 502",
          st == 502 and "AI provider unavailable" in str(j.get("detail", "")),
          f"{st} {str(j)[:160]}")
# streaming endpoint (the default the Assistant UI uses) must behave the same
st, j = call("POST", "/api/v1/ai/chat/stream",
             {"messages": [{"role": "user", "content": "ping"}], "mode": "general", "lang": "en"},
             token=f)
check("stream chat honest (200 SSE or 502 detail)",
          st == 200 or (st == 502 and "AI provider unavailable" in str(j.get("detail", ""))),
          f"{st} {str(j)[:160]}")
st, _ = call("GET", "/api/v1/admin/audit", token=f)
check("field_engineer audit 403", st == 403, str(st))
st, _ = call("GET", "/api/v1/admin/overview", token=f)
check("field_engineer overview 403", st == 403, str(st))

# MANAGER (phase1) must STILL be 403 on overview (regression guard)
st, _ = call("GET", "/api/v1/admin/overview", token=toks["manager"][0])
check("MANAGER overview still 403", st == 403, str(st))

print("== 4. audit rows shape (frontend mapping) ==")
st, j = call("GET", "/api/v1/admin/audit", token=a)
rows = j.get("rows", [])
check("audit rows list", isinstance(rows, list) and len(rows) >= 0, str(type(rows)))
if rows:
    r0 = rows[-1]
    check("audit row keys", {"ts", "actor_id", "actor_role", "action"} <= set(r0.keys()),
          str(sorted(r0.keys())))

print("== 5. agents registry (5 agents, role flags) ==")
st, j = call("GET", "/api/v1/agents", token=toks["admin"][0])
names = [x["name"] for x in j.get("agents", [])]
check("exactly 5 agents", len(names) == 5, str(names))
check("admin_assistant allowed for admin", any(
    x["name"] == "admin_assistant" and x["allowed"] for x in j["agents"]), "")
st, j = call("GET", "/api/v1/agents", token=s)
check("admin_assistant allowed for security_admin", any(
    x["name"] == "admin_assistant" and x["allowed"] for x in j["agents"]), str(j)[:200])
st, j = call("GET", "/api/v1/agents", token=toks["manager"][0])
check("admin_assistant NOT allowed for MANAGER", not any(
    x["name"] == "admin_assistant" and x["allowed"] for x in j["agents"]), str(j)[:200])

print("== 6. voice status + i18n languages ==")
st, j = call("GET", "/api/v1/voice/status", token=a)
check("voice status 200", st == 200 and "stt" in j and "tts" in j, f"{st} {str(j)[:120]}")
st, j = call("GET", "/api/v1/i18n/languages", token=a)
langs = j.get("languages") or []
codes = [x["code"] if isinstance(x, dict) else x for x in langs]
check("i18n exactly 12 languages", len(codes) == 12, str(codes))

print("== 7. legacy token + legacy endpoints (pipeline demo) ==")
# legacy login + model-tier (pipeline demo inputs)
st, j = call("POST", "/api/login", {"username": "manager1", "password": "mgr123"})
check("legacy login manager1", st == 200 and "token" in j, f"{st}")
leg = j.get("token", "")
if leg:
    st2, j2 = call("GET", "/api/model-tier", token=leg, base=RAW)
    check("legacy model-tier 200", st2 == 200, f"{st2} {str(j2)[:80]}")
# legacy GET /api/audit is gated by roles.yaml action audit_view:
# auditor only (verified in roles.yaml). audit1 legacy -> 200, manager1 -> 403.
st, j = call("POST", "/api/login", {"username": "audit1", "password": "aud123"})
check("legacy login audit1", st == 200 and "token" in j, str(st))
if st == 200:
    st2, j2 = call("GET", "/api/audit", token=j["token"], base=RAW)
    check("legacy audit rows (auditor)", st2 == 200 and "rows" in j2,
          f"{st2} {str(j2)[:80]}")
st, j = call("POST", "/api/login", {"username": "manager1", "password": "mgr123"})
if st == 200:
    st2, _ = call("GET", "/api/audit", token=j["token"], base=RAW)
    check("legacy audit 403 for approving_manager (roles.yaml)", st2 == 403, str(st2))
# v1 token must be REJECTED on legacy user endpoints (no claim mixing)
st, _ = call("POST", "/api/tasks", {"query": "x"}, token=a, base=RAW)
check("v1 token rejected on legacy /api/tasks", st in (401, 403), str(st))

print("== 8. security: no secrets in statuses/overview ==")
st, j = call("GET", "/api/v1/ai/status", token=a)
blob = json.dumps(j).lower()
check("ai status has no secret keys", not any(
    k in blob for k in ["secret", "password", "jwt", "api_key", "apikey"]), blob[:200])
st, j = call("GET", "/api/v1/admin/overview", token=a)
blob = json.dumps(j).lower()
check("overview has no secret values", "password" not in blob and "jwt_secret" not in blob,
      blob[:200])

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
