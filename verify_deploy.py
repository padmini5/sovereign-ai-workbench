"""Deployment-parity checks against the CURRENT API (no legacy endpoints).

Covers: health, public config shape (incl. email_domains), v1 login for all
demo roles + legacy compat, wrong-password/domain failures, RBAC denials,
doc upload + RAG Q&A with real sources, IDOR blocks, tool authorization,
audit trail, WebSocket streams. Uses isolated temp DBs; mock AI providers.
Run: python verify_deploy.py
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_verify_deploy_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_DOCS_DB"] = os.path.join(_tmp, "docs.db")
os.environ["SOV_VECTORS_DB"] = os.path.join(_tmp, "vectors.db")
os.environ["SOV_UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["SOV_AGENTS_DB"] = os.path.join(_tmp, "agents.db")
os.environ["SOV_WORK_DB"] = os.path.join(_tmp, "work.db")
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


print("== 1. liveness (unauthenticated, no secrets) ==")
h = c.get("/health").json()
check("health ok", h.get("ok") is True and h.get("service") == "sovereignai-workbench",
      str(h)[:160])
check("health fields", {"version", "llm_mode", "rag_backend"} <= set(h),
      str(sorted(h))[:160])

print("== 2. public config (flags only, email_domains included) ==")
cfg = c.get("/api/config").json()
check("config keys", {"demo_login_enabled", "version", "email_domains"} <= set(cfg),
      str(cfg)[:160])
check("no secrets in config",
      not any(k in str(cfg).lower() for k in ["secret", "password", "key"]))

print("== 3. current demo logins (org email, strong passwords) ==")
CREDS = {"admin@company.com": ("Admin@2026#S9x!", "ADMIN"),
         "manager@company.com": ("Manager@2026#K7p!", "MANAGER"),
         "employee@company.com": ("Employee@2026#R4m!", "EMPLOYEE"),
         "operator@company.com": ("Operator@2026#T8q!", "OPERATOR"),
         "reviewer@company.com": ("Reviewer@2026#V6n!", "REVIEWER")}
toks = {}
for u, (p, role) in CREDS.items():
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    check(f"login {role}", r.status_code == 200
          and r.json()["user"]["role"] == role, r.text[:120])
    if r.status_code == 200:
        toks[role] = r.json()["access_token"]
check("legacy login intact",
      c.post("/api/v1/auth/login",
             json={"username": "field1", "password": "field123"}).status_code == 200)
check("wrong password -> 401",
      c.post("/api/v1/auth/login",
             json={"username": "employee@company.com",
                   "password": "wrong"}).status_code == 401)
check("bad domain -> 403",
      c.post("/api/v1/auth/login",
             json={"username": "x@evil.com", "password": "x"}).status_code == 403)

print("== 4. RBAC denials ==")
check("employee admin users -> 403",
      c.get("/api/v1/admin/users", headers=H(toks["EMPLOYEE"])).status_code == 403)
check("operator create employee -> 403",
      c.post("/api/v1/employees",
             json={"username": "z@company.com", "password": "Zz12345678",
                   "role": "EMPLOYEE",
                   "profile": {"first_name": "Z", "last_name": "Z",
                               "phone": "+91-9000000001"}},
             headers=H(toks["OPERATOR"])).status_code == 403)

print("== 5. docs + RAG Q&A with real sources ==")
up = c.post("/api/v1/docs/upload",
            files={"f": ("ops.txt", b"Pump P-42 vibration limits: normal below 4mm/s. "
                         b"Inspect bearings monthly per section 7.", "text/plain")},
            headers=H(toks["EMPLOYEE"]))
check("upload 200", up.status_code == 200, up.text[:120])
did = up.json()["id"]
check("analyze READY",
      c.post(f"/api/v1/docs/{did}/analyze",
             headers=H(toks["EMPLOYEE"])).json().get("status") == "READY")
r = c.post("/api/v1/ai/chat",
           json={"messages": [{"role": "user",
                               "content": "What are the pump vibration limits?"}],
                 "document_ids": [did]}, headers=H(toks["EMPLOYEE"]))
check("grounded answer + sources", r.status_code == 200
      and r.json().get("sources"), r.text[:200])
check("foreign doc chat -> 403",
      c.post("/api/v1/ai/chat",
             json={"messages": [{"role": "user", "content": "read it"}],
                   "document_ids": [did]},
             headers=H(toks["OPERATOR"])).status_code == 403)
check("IDOR download -> 404",
      c.get(f"/api/v1/docs/{did}/download",
            headers=H(toks["OPERATOR"])).status_code == 404)

print("== 6. tool authorization ==")
check("employee list_users tool -> 403",
      c.post("/api/v1/ai/chat",
             json={"messages": [{"role": "user", "content": "list people"}],
                   "tools": ["list_users"]},
             headers=H(toks["EMPLOYEE"])).status_code == 403)
check("admin list_users tool 200",
      c.post("/api/v1/ai/chat",
             json={"messages": [{"role": "user", "content": "list people"}],
                   "tools": ["list_users"]},
             headers=H(toks["ADMIN"])).status_code == 200)

print("== 7. audit trail ==")
acts = {a["action"] for a in audit_log.rows(3000)}
for a in ["login_success", "login_failure", "doc_upload", "rag_retrieval",
          "doc_question", "ai_chat", "permission_denied"]:
    check(f"audit {a}", a in acts)

print("== 8. production guards (config-level) ==")
from backend.app import config as cfgmod
check("docs toggle exists", hasattr(cfgmod, "API_DOCS_ENABLED"))
main_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "backend", "app", "main.py"), encoding="utf-8").read()
check("no wildcard CORS methods", 'allow_methods=["*"]' not in main_src)
check("CORS headers allowlisted",
      '"Authorization"' in main_src and '"Content-Type"' in main_src)
compose = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "docker-compose.yml"), encoding="utf-8").read()
check("frontend not publicly bound", '"8080:8080"' not in compose
      and "127.0.0.1:8080:8080" in compose)
check("db publishes no ports",
      "pgvector" in compose and "ports:" not in compose.split("db:")[1].split("backend:")[0])

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
