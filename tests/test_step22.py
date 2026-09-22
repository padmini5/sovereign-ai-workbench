"""Step 22 tests: ADMIN AI/system controls (run: python tests/test_step22.py).

Isolated temp DBs + temp sysconfig file. Restores every mutated env key.
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_step22_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_DOCS_DB"] = os.path.join(_tmp, "docs.db")
os.environ["SOV_VECTORS_DB"] = os.path.join(_tmp, "vectors.db")
os.environ["SOV_UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["SOV_AGENTS_DB"] = os.path.join(_tmp, "agents.db")
os.environ["SOV_SYSCONFIG_PATH"] = os.path.join(_tmp, "sysconfig.json")
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"
os.environ["SOV_STT_PROVIDER"] = "mock"
os.environ["SOV_TTS_PROVIDER"] = "mock"

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


T_ADM = login("admin", "Admin123!")
T_MGR = login("manager", "Mgr123!")
T_OP = login("operator", "Op123!")
T_REV = login("reviewer", "Rev123!")
T_USR = login("user", "User123!")
NONADMIN = [("MANAGER", T_MGR), ("OPERATOR", T_OP), ("REVIEWER", T_REV), ("USER", T_USR)]

ADMIN_URLS = [
    ("GET", "/api/v1/admin/overview", None),
    ("GET", "/api/v1/admin/config", None),
    ("PUT", "/api/v1/admin/config", {"key": "SOV_RAG_TOP_K", "value": 4}),
    ("POST", "/api/v1/admin/config/reset", {"key": "SOV_RAG_TOP_K"}),
    ("PUT", "/api/v1/admin/agents/review", {"enabled": True}),
    ("PUT", "/api/v1/admin/features/voice", {"enabled": True}),
]

print("== 1. ADMIN access succeeds ==")
r = c.get("/api/v1/admin/overview", headers=H(T_ADM)).json()
for section in ["ai", "llm", "embeddings", "rag", "chroma", "translate", "stt",
                "tts", "agents", "tools", "features", "system"]:
    check(f"overview has {section}", section in r, str(sorted(r)))
check("expected agents listed",
      {"document_analysis", "report_generation", "review", "data_analysis",
       "admin_assistant"} <= {a["name"] for a in r["agents"]})
check("config listed", len(c.get("/api/v1/admin/config", headers=H(T_ADM)).json()["config"]) >= 15)

print("== 2. non-admin denied on every admin API ==")
for role, tok in NONADMIN:
    for method, url, body in ADMIN_URLS:
        r = c.request(method, url, json=body, headers=H(tok))
        if r.status_code != 403:
            check(f"{role} {method} {url} -> 403", False, str(r.status_code))
            break
    else:
        check(f"{role} denied everywhere", True)
check("no token -> 401/403",
      c.get("/api/v1/admin/overview").status_code in (401, 403))

print("== 3. validation ==")
put = lambda k, v: c.put("/api/v1/admin/config", json={"key": k, "value": v}, headers=H(T_ADM))
check("bad enum -> 422", put("SOV_AI_PROVIDER", "skynet").status_code == 422)
check("int out of range -> 422", put("SOV_RAG_TOP_K", 99).status_code == 422)
check("bad url -> 422", put("OLLAMA_BASE_URL", "ftp://x").status_code == 422)
check("bad model id -> 422", put("OLLAMA_MODEL", "evil;rm -rf").status_code == 422)
check("unknown key -> 422", put("SOV_JWT_SECRET", "x").status_code == 422)
check("unknown key 2 -> 422", put("EVIL_KEY", "x").status_code == 422)
check("bad agent -> 404",
      c.put("/api/v1/admin/agents/nope", json={"enabled": True}, headers=H(T_ADM)).status_code == 404)
check("bad feature -> 404",
      c.put("/api/v1/admin/features/nope", json={"enabled": True}, headers=H(T_ADM)).status_code == 404)

print("== 4. apply + effect + reset ==")
r = put("SOV_RAG_TOP_K", 2).json()
check("applied with source", r == {"key": "SOV_RAG_TOP_K", "value": "2", "source": "override"}, str(r))
cfg = {x["key"]: x for x in c.get("/api/v1/admin/config", headers=H(T_ADM)).json()["config"]}
check("visible as override", cfg["SOV_RAG_TOP_K"]["value"] == "2"
      and cfg["SOV_RAG_TOP_K"]["source"] == "override")
r = c.post("/api/v1/admin/config/reset", json={"key": "SOV_RAG_TOP_K"}, headers=H(T_ADM)).json()
check("reset works", r["cleared"] is True and r["source"] != "override", str(r))

print("== 5. secret protection ==")
os.environ["SOV_JWT_SECRET"] = "supersecret-test-xyz-123"
os.environ["DATABASE_URL"] = "postgresql://sov:hunter2@localhost/db"
blob = (c.get("/api/v1/admin/overview", headers=H(T_ADM)).text
        + c.get("/api/v1/admin/config", headers=H(T_ADM)).text
        + " ".join((x.get("detail") or "") for x in audit_log.rows(2000)))
check("jwt secret absent", "supersecret-test-xyz-123" not in blob)
check("db password absent", "hunter2" not in blob)
check("no secret keys listed",
      "SOV_JWT_SECRET" not in c.get("/api/v1/admin/config", headers=H(T_ADM)).text)
del os.environ["SOV_JWT_SECRET"]
del os.environ["DATABASE_URL"]

print("== 6. provider change + unavailable fallback ==")
put("SOV_EMBED_PROVIDER", "hash")
st = c.get("/api/v1/ai/status", headers=H(T_MGR)).json()
check("embed change reflected", st["rag"]["embed_provider"] == "hash")
put("SOV_AI_PROVIDER", "mock-fail")
check("broken provider -> honest 502, app alive",
      c.post("/api/v1/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]},
             headers=H(T_MGR)).status_code == 502
      and c.get("/api/v1/ai/status", headers=H(T_MGR)).status_code == 200)
put("SOV_AI_PROVIDER", "mock")
check("restored", c.post("/api/v1/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]},
      headers=H(T_MGR)).status_code == 200)

print("== 7. feature + agent toggles enforced ==")
import io as _io
from PIL import Image as _Im
_im = _Im.new("RGB", (8, 8), "red")
_b = _io.BytesIO()
_im.save(_b, "PNG")
c.put("/api/v1/admin/features/voice", json={"enabled": False}, headers=H(T_ADM))
check("voice off -> 503",
      c.post("/api/v1/voice/stt", files={"f": ("v.png", _b.getvalue(), "image/png")},
             data={"lang": "en"}, headers=H(T_MGR)).status_code == 503)
c.put("/api/v1/admin/features/voice", json={"enabled": True}, headers=H(T_ADM))
c.put("/api/v1/admin/agents/review", json={"enabled": False}, headers=H(T_ADM))
check("agent off -> 404",
      c.post("/api/v1/agents/review/run", json={"goal": "x"}, headers=H(T_MGR)).status_code == 404)
c.put("/api/v1/admin/agents/review", json={"enabled": True}, headers=H(T_ADM))
check("agent back on",
      c.post("/api/v1/agents/review/run", json={"goal": "x"}, headers=H(T_MGR)).status_code == 200)

print("== 8. audit events ==")
actions = {r["action"] for r in audit_log.rows(3000)}
for a in ["config_viewed", "config_changed", "provider_changed",
          "feature_enabled", "feature_disabled", "agent_enabled", "agent_disabled",
          "config_validation_failed", "feature_denied"]:
    check(f"{a}", a in actions, str(sorted(actions)))

print("== 9. model change audited ==")
r = put("OLLAMA_MODEL", "qwen2.5:7b-instruct")
check("model PUT 200", r.status_code == 200, r.text[:200])
check("model_changed",
      "model_changed" in {r["action"] for r in audit_log.rows(3000)})
c.post("/api/v1/admin/config/reset", json={"key": "OLLAMA_MODEL"}, headers=H(T_ADM))

print("== 10. spot regressions (RBAC/RAG/agents) ==")
check("manager chat ok",
      c.post("/api/v1/ai/chat", json={"messages": [{"role": "user", "content": "hi"}]},
             headers=H(T_MGR)).status_code == 200)
did = c.post("/api/v1/docs/upload",
             files={"f": ("spot.txt", b"documents orchid harbor manifest", "text/plain")},
             headers={"Authorization": f"Bearer {T_MGR}"}).json()["id"]
c.post(f"/api/v1/docs/{did}/analyze", headers=H(T_MGR))
r = c.post("/api/v1/ai/chat",
           json={"messages": [{"role": "user", "content": "orchid harbor"}],
                 "document_ids": [did], "mode": "my_docs"}, headers=H(T_MGR)).json()
check("rag sources intact", r.get("sources") and "Sources:" in r["message"]["content"])
r = c.post("/api/v1/agents/document_analysis/run", json={"goal": "spot check"},
           headers=H(T_MGR))
check("agent run ok", r.status_code == 200 and r.json()["state"] == "COMPLETED", r.text[:200])
check("manager still cannot admin",
      c.get("/api/v1/admin/overview", headers=H(T_MGR)).status_code == 403)

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
