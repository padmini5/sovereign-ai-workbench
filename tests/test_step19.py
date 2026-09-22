"""Step 19 tests: Indian multilingual support (run: python tests/test_step19.py).

Mock translation provider only — no external APIs. Isolated temp DBs.
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_step19_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_DOCS_DB"] = os.path.join(_tmp, "docs.db")
os.environ["SOV_VECTORS_DB"] = os.path.join(_tmp, "vectors.db")
os.environ["SOV_UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402

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


def chat(tok, text, **kw):
    return c.post("/api/v1/ai/chat", json={"messages": [{"role": "user", "content": text}], **kw},
                   headers=H(tok))


T_MGR = login("manager", "Mgr123!")
T_ADM = login("admin", "Admin123!")
T_OP = login("operator", "Op123!")

print("== 1. language selection + preference storage ==")
r = c.get("/api/v1/auth/language", headers=H(T_MGR)).json()
check("default en + 12 supported",
      r["lang"] == "en" and len(r["supported"]) == 12, str(r)[:150])
check("all 12 codes present",
      {l["code"] for l in r["supported"]} == {"en", "hi", "te", "ta", "kn", "ml", "mr",
                                              "bn", "gu", "pa", "or", "ur"})
check("set hi", c.put("/api/v1/auth/language", json={"lang": "hi"},
      headers=H(T_MGR)).json() == {"lang": "hi"})
check("get reflects hi",
      c.get("/api/v1/auth/language", headers=H(T_MGR)).json()["lang"] == "hi")
check("pref survives login",
      c.post("/api/v1/auth/login", json={"username": "manager", "password": "Mgr123!"}).json()["user"]["lang"] == "hi")
check("invalid pref -> 422",
      c.put("/api/v1/auth/language", json={"lang": "xx"}, headers=H(T_MGR)).status_code == 422)
c.put("/api/v1/auth/language", json={"lang": "en"}, headers=H(T_MGR))

print("== 2. English / Hindi / Telugu responses ==")
r = chat(T_MGR, "hello").json()
check("en: translated False, no tag",
      r["lang"] == "en" and r["translated"] is False and "[hi]" not in r["message"]["content"])
r = chat(T_MGR, "hello", lang="hi").json()
check("hi: translated, tagged",
      r["lang"] == "hi" and r["translated"] is True and r["message"]["content"].startswith("[hi]"),
      r["message"]["content"][:120])
r = chat(T_MGR, "hello", lang="te").json()
check("te: translated, tagged",
      r["lang"] == "te" and r["translated"] is True and r["message"]["content"].startswith("[te]"),
      r["message"]["content"][:120])

print("== 3. invalid/unsupported language ==")
check("chat lang xx -> 422", chat(T_MGR, "hi", lang="xx").status_code == 422)
check("translate xx -> 422",
      c.post("/api/v1/i18n/translate", json={"text": "x", "target": "xx"},
             headers=H(T_MGR)).status_code == 422)
check("translate empty -> 422",
      c.post("/api/v1/i18n/translate", json={"text": " ", "target": "hi"},
             headers=H(T_MGR)).status_code == 422)

print("== 4. provider unavailable -> graceful English fallback ==")
os.environ["SOV_TRANSLATE_PROVIDER"] = "unavailable"
r = chat(T_MGR, "hello", lang="hi").json()
check("fallback 200, translated False, English",
      r["lang"] == "hi" and r["translated"] is False
      and r["message"]["content"].startswith("[MANAGER assistant via mock]"),
      r["message"]["content"][:150])
st = c.get("/api/v1/ai/status", headers=H(T_MGR)).json()
check("status reports unavailable", st["i18n"]["translate_available"] is False)
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"

print("== 5. RAG answer in selected language ==")
did = c.post("/api/v1/docs/upload", files={"f": ("hindi-rag.txt", b"samba mango harvest festival report", "text/plain")},
             headers={"Authorization": f"Bearer {T_MGR}"}).json()["id"]
c.post(f"/api/v1/docs/{did}/analyze", headers=H(T_MGR))
r = chat(T_MGR, "samba harvest", document_ids=[did], lang="hi").json()
check("rag hi: sources + tagged body",
      r["sources"] and r["sources"][0]["doc_id"] == did
      and "[hi]" in r["message"]["content"], r["message"]["content"][:250])
check("citations preserved in English",
      "Sources:" in r["message"]["content"] and "hindi-rag.txt" in r["message"]["content"])
s = r["sources"][0]
check("source fields intact", {"doc_id", "filename", "chunk_index", "score"} <= set(s))

print("== 6. RBAC still enforced (language is no bypass) ==")
r = c.post("/api/v1/admin/users", json={"username": "tmp_lang", "password": "Tmp12345", "role": "OPERATOR"},
           headers=H(T_ADM))
T_TMP = login("tmp_lang", "Tmp12345")
check("stranger + lang -> 403",
      chat(T_TMP, "samba", document_ids=[did], lang="hi").status_code == 403)
r = chat(T_TMP, "samba", mode="my_docs", lang="hi").json()
check("stranger my_docs hi -> localized not-found, no leak",
      r["sources"] == [] and "samba" not in r["message"]["content"].lower()
      and ("नहीं मिली" in r["message"]["content"] or "couldn't find" in r["message"]["content"]),
      r["message"]["content"][:200])

print("== 7. preference-driven default (no per-request lang) ==")
c.put("/api/v1/auth/language", json={"lang": "te"}, headers=H(T_OP))
r = chat(T_OP, "hello").json()
check("pref te applies", r["lang"] == "te" and r["translated"] is True)
check("explicit lang wins over pref",
      chat(T_OP, "hello", lang="hi").json()["lang"] == "hi")
c.put("/api/v1/auth/language", json={"lang": "en"}, headers=H(T_OP))

print("== 8. message lang persisted ==")
cid = chat(T_MGR, "persist check", lang="hi").json()["conversation_id"]
msgs = c.get(f"/api/v1/ai/conversations/{cid}", headers=H(T_MGR)).json()["messages"]
check("assistant msg lang hi",
      msgs[-1]["role"] == "assistant" and msgs[-1].get("lang") == "hi", str(msgs[-1])[:150])

print("== 9. i18n endpoints ==")
r = c.get("/api/v1/i18n/languages", headers=H(T_MGR)).json()
check("languages endpoint", len(r["languages"]) == 12 and r["engine"]["provider"] == "mock"
      and r["engine"]["available"] is True)
r = c.post("/api/v1/i18n/translate", json={"text": "hello world", "target": "bn"},
           headers=H(T_MGR)).json()
check("direct translate", r["text"].startswith("[bn]") and r["provider"] == "mock")
check("remote disabled by default", r["provider"] == "mock"
      and c.get("/api/v1/i18n/languages", headers=H(T_MGR)).json()["engine"]["remote_configured"] is False)

uid = [u["id"] for u in c.get("/api/v1/admin/users", headers=H(T_ADM)).json()["users"]
       if u["username"] == "tmp_lang"][0]
c.delete(f"/api/v1/admin/users/{uid}", headers=H(T_ADM))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
