"""Phase 2 tests: role-aware AI assistant (run: python tests/test_phase2.py).

Covers: AI authentication, unauthorized tool access, role-specific behavior,
conversation creation, message storage, provider failure, Ollama unavailable,
invalid requests, streaming, audit metadata. Uses isolated temp DBs + mock
provider — no Ollama, no network needed.
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_phase2_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_AI_PROVIDER"] = "mock"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import audit_log  # noqa: E402
from backend.app.ai.tools import run_tool  # noqa: E402
from fastapi import HTTPException as _HTTPException  # noqa: E402


def _denied(role, tool):
    fake = {"id": "u-x", "username": "x", "role": role}
    try:
        run_tool(tool, fake, {})
        return False
    except _HTTPException as e:
        return e.status_code == 403

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


def login(u, p):
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


toks = {role: login(u, p) for role, (u, p) in CREDS.items()}
CHAT = lambda role, body: c.post("/api/v1/ai/chat", json=body, headers=H(toks[role]))
M = lambda t: [{"role": "user", "content": t}]

print("== 1. AI authentication ==")
check("no token -> 401/403", c.post("/api/v1/ai/chat", json={"messages": M("hi")}).status_code in (401, 403))
check("bad token -> 401", c.post("/api/v1/ai/chat", json={"messages": M("hi")},
      headers=H("junk")).status_code == 401)
for role in CREDS:
    r = CHAT(role, {"messages": M("hello")})
    check(f"{role} chat 200", r.status_code == 200, r.text[:200])

print("== 2. role-specific behavior ==")
for role in CREDS:
    r = CHAT(role, {"messages": M("status report")}).json()
    check(f"{role} mock prefix", r["message"]["content"].startswith(f"[{role} assistant via mock]"))
    check(f"{role} prompt_id == role", r["prompt_id"] == role)

print("== 3. invalid requests ==")
check("empty messages -> 422", CHAT("USER", {"messages": []}).status_code == 422)
check("empty content -> 422", CHAT("USER", {"messages": [{"role": "user", "content": " "}]}).status_code == 422)
check("assistant-last -> 422",
      CHAT("USER", {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}).status_code == 422)
check("bad role value -> 422",
      CHAT("USER", {"messages": [{"role": "system", "content": "x"}]}).status_code == 422)
check("unknown tool -> 422", CHAT("ADMIN", {"messages": M("hi"), "tools": ["nope"]}).status_code == 422)
check("oversize -> 422", CHAT("USER", {"messages": M("x" * 4001)}).status_code == 422)
check("unknown convo -> 404", CHAT("USER", {"messages": M("hi"), "conversation_id": "c-nope"}).status_code == 404)
check("missing body -> 422", c.post("/api/v1/ai/chat", json={}, headers=H(toks["USER"])).status_code == 422)

print("== 4. unauthorized tool access (fail-closed) ==")
for role, tool in [("USER", "list_users"), ("OPERATOR", "get_audit_summary"),
                   ("USER", "get_system_info"), ("MANAGER", "get_audit_summary")]:
    r = CHAT(role, {"messages": M("do it"), "tools": [tool]})
    check(f"{role}+{tool} -> 403", r.status_code == 403, r.text[:150])
check("direct run_tool USER/list_users -> 403", _denied("USER", "list_users"))
check("direct run_tool OPERATOR/get_audit_summary -> 403",
      _denied("OPERATOR", "get_audit_summary"))

print("== 5. authorized tool access ==")
r = CHAT("ADMIN", {"messages": M("who are my users?"), "tools": ["list_users"]})
check("ADMIN list_users 200", r.status_code == 200, r.text[:200])
body = r.json()
check("usernames present, no hashes",
      "admin" in body["message"]["content"] and "pass_hash" not in body["message"]["content"])
check("tools_used recorded", body["tools_used"] == ["list_users"])
r = CHAT("REVIEWER", {"messages": M("audit?"), "tools": ["get_audit_summary"]})
check("REVIEWER audit 200", r.status_code == 200)
r = CHAT("ADMIN", {"messages": M("sys?"), "tools": ["get_system_info"]})
check("ADMIN system info 200", r.status_code == 200)
r = CHAT("USER", {"messages": M("me?"), "tools": ["get_my_info"]})
check("USER own-info 200", r.status_code == 200 and "USER" in r.json()["message"]["content"])

print("== 6. tools visibility per role ==")
t_user = c.get("/api/v1/ai/tools", headers=H(toks["USER"])).json()["tools"]
t_admin = c.get("/api/v1/ai/tools", headers=H(toks["ADMIN"])).json()["tools"]
names_u = {t["name"] for t in t_user}
names_a = {t["name"] for t in t_admin}
check("USER sees get_my_info, not list_users",
      "get_my_info" in names_u and "list_users" not in names_u)
check("ADMIN sees list_users + get_system_info",
      {"list_users", "get_system_info"} <= names_a)

print("== 7. conversations + message storage ==")
r1 = CHAT("OPERATOR", {"messages": M("first question")}).json()
cid = r1["conversation_id"]
check("new conversation id", cid.startswith("c-"))
r2 = CHAT("OPERATOR", {"messages": M("follow-up"), "conversation_id": cid}).json()
check("continued same convo", r2["conversation_id"] == cid)
d = c.get(f"/api/v1/ai/conversations/{cid}", headers=H(toks["OPERATOR"])).json()
check("4 messages stored", len(d["messages"]) == 4, str(len(d["messages"])))
roles_seq = [m["role"] for m in d["messages"]]
check("user/assistant alternating", roles_seq == ["user", "assistant", "user", "assistant"], str(roles_seq))
check("assistant msgs carry provider+model",
      all(m["provider"] == "mock" and m["model"] == "mock" for m in d["messages"] if m["role"] == "assistant"))
check("owner listed", any(x["id"] == cid for x in
      c.get("/api/v1/ai/conversations", headers=H(toks["OPERATOR"])).json()["conversations"]))
check("foreign user -> 404",
      c.get(f"/api/v1/ai/conversations/{cid}", headers=H(toks["USER"])).status_code == 404)
check("foreign delete -> 404",
      c.delete(f"/api/v1/ai/conversations/{cid}", headers=H(toks["USER"])).status_code == 404)
check("owner delete ok",
      c.delete(f"/api/v1/ai/conversations/{cid}", headers=H(toks["OPERATOR"])).status_code == 200)
check("deleted gone -> 404",
      c.get(f"/api/v1/ai/conversations/{cid}", headers=H(toks["OPERATOR"])).status_code == 404)

print("== 8. provider failure ==")
os.environ["SOV_AI_PROVIDER"] = "mock-fail"
r = CHAT("USER", {"messages": M("hi")})
check("failing provider -> 502", r.status_code == 502, r.text[:150])
st = c.get("/api/v1/ai/status", headers=H(toks["USER"])).json()
check("status unreachable", st["reachable"] is False and st["provider"] == "mock")
os.environ["SOV_AI_PROVIDER"] = "mock"

print("== 9. ollama unavailable ==")
os.environ["SOV_AI_PROVIDER"] = "ollama"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
os.environ["OLLAMA_MODEL"] = "does-not-exist"
r = CHAT("USER", {"messages": M("hi")})
check("ollama down -> 502", r.status_code == 502, r.text[:200])
st = c.get("/api/v1/ai/status", headers=H(toks["USER"])).json()
check("ollama status unreachable, model named",
      st["provider"] == "ollama" and st["reachable"] is False and st["model"] == "does-not-exist",
      str(st))
os.environ["SOV_AI_PROVIDER"] = "mock"
del os.environ["OLLAMA_BASE_URL"]
r = CHAT("USER", {"messages": M("back")})
check("recovered after ollama test", r.status_code == 200)

print("== 10. streaming ==")
with c.stream("POST", "/api/v1/ai/chat/stream", json={"messages": M("stream me")},
              headers=H(toks["USER"])) as r:
    check("stream 200 event-stream", r.status_code == 200)
    text = r.read().decode()
check("stream has meta + done", "event: meta" in text and "event: done" in text, text[:200])
check("stream carried text", len(text) > 60)

print("== 11. audit logging (metadata only) ==")
rows = audit_log.rows(500)
by_action = {}
for r in rows:
    by_action.setdefault(r["action"], []).append(r)
check("ai_chat audited", "ai_chat" in by_action)
check("ai_chat_denied audited", "ai_chat_denied" in by_action)
check("ai_provider_error audited", "ai_provider_error" in by_action)
blob = " ".join(r.get("detail", "") for rs in by_action.values() for r in rs)
check("audit has no message content", "stream me" not in blob and "first question" not in blob)
check("audit has provider/latency meta", "provider=mock" in blob and "latency=" in blob)

print("== 12. status shape ==")
st = c.get("/api/v1/ai/status", headers=H(toks["ADMIN"])).json()
check("status keys", {"provider", "model", "reachable", "role", "backend"} <= set(st), str(st))
check("no secrets in status", "secret" not in str(st).lower())

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
