"""Phase 4 tests: RAG document intelligence (run: python tests/test_phase4.py).

Uses hash embeddings (local, lexical) + mock LLM: no Ollama, no network.
Isolated temp DBs + temp upload/vector dirs.
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_phase4_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_DOCS_DB"] = os.path.join(_tmp, "docs.db")
os.environ["SOV_VECTORS_DB"] = os.path.join(_tmp, "vectors.db")
os.environ["SOV_UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import audit_log  # noqa: E402
from backend.app.embeddings import HashEmbeddingProvider, MockEmbeddingProvider  # noqa: E402
from backend.app.vectorstore import get_vector_store  # noqa: E402

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


def up(tok, name, data):
    return c.post("/api/v1/docs/upload", files={"f": (name, data, "application/octet-stream")},
                  headers={"Authorization": f"Bearer {tok}"})


def analyze(tok, did):
    return c.post(f"/api/v1/docs/{did}/analyze", headers=H(tok))


def chat(tok, text, **kw):
    body = {"messages": [{"role": "user", "content": text}], **kw}
    return c.post("/api/v1/ai/chat", json=body, headers=H(tok))


T_MGR = login("manager", "Mgr123!")
T_ADM = login("admin", "Admin123!")
T_USR = login("user", "User123!")
r = c.post("/api/v1/admin/users", json={"username": "rag_op2", "password": "Tmp12345", "role": "OPERATOR"},
           headers=H(T_ADM))
T_OP2 = login("rag_op2", "Tmp12345")

DOC = (b"zebra alpha project timeline: phase one completes in march. "
       b"budget approved for copper wiring section seven. contact field team redux.")

print("== 1. document indexing ==")
did = up(T_MGR, "zebra.txt", DOC).json()["id"]
r = analyze(T_MGR, did).json()
check("analyze READY", r["status"] == "READY" and r["proc_status"] == "done", str(r)[:200])
check("chunks reported", r.get("chunks", 0) >= 1, str(r.get("chunks")))
check("vectors stored", get_vector_store().count_for(did) >= 1)

print("== 2. chunk + embedding creation ==")
hits, _ = __import__("backend.app.rag", fromlist=["retrieve"]).retrieve(
    {"id": "u-manager", "username": "manager", "role": "MANAGER"}, "zebra timeline", [did])
check("retrieval hit", len(hits) >= 1)
h = hits[0]
check("chunk metadata", {"doc_id", "chunk_index", "page", "filename", "text", "score"} <= set(h),
      str(sorted(h)))
check("hit belongs to doc", h["doc_id"] == did and h["filename"] == "zebra.txt")
check("mock embed dim 16", MockEmbeddingProvider().dim == 16
      and len(MockEmbeddingProvider().embed(["x"])[0]) == 16)
check("hash embed normalized",
      abs(sum(v * v for v in HashEmbeddingProvider().embed(["zebra"])[0]) - 1.0) < 1e-6)

print("== 3. grounded chat + sources ==")
r = chat(T_MGR, "when does phase one complete?", document_ids=[did]).json()
check("chat 200 + sources", r.get("sources") and r["sources"][0]["doc_id"] == did,
      str(r)[:300])
check("Sources section in answer", "Sources:" in r["message"]["content"]
      and "zebra.txt" in r["message"]["content"])
s = r["sources"][0]
check("source attribution fields",
      {"doc_id", "filename", "chunk_index", "score"} <= set(s), str(sorted(s)))

print("== 4. my_docs mode ==")
r = chat(T_MGR, "zebra budget", mode="my_docs").json()
check("my_docs cites own doc", r.get("sources") and r["sources"][0]["filename"] == "zebra.txt",
      str(r)[:300])

print("== 5. authorized cross-user (ADMIN) ==")
r = chat(T_ADM, "zebra timeline", document_ids=[did])
check("ADMIN reads mgr doc", r.status_code == 200 and r.json().get("sources"), r.text[:200])

print("== 6. unauthorized retrieval ==")
r = chat(T_OP2, "zebra timeline", document_ids=[did])
check("stranger scoped chat -> 403", r.status_code == 403, r.text[:150])
r = chat(T_OP2, "zebra timeline", mode="my_docs").json()
check("stranger my_docs -> not found, no leak",
      r["sources"] == [] and "couldn't find" in r["message"]["content"]
      and "zebra" not in r["message"]["content"].lower().replace("zebra timeline", "q"),
      r["message"]["content"][:200])

print("== 7. cross-user isolation ==")
up(T_OP2, "other.txt", b"xylophone quantum bananas ultraviolet")
d2 = c.get("/api/v1/docs", headers=H(T_OP2)).json()["documents"][0]["id"]
c.post(f"/api/v1/docs/{d2}/analyze", headers=H(T_ADM))  # admin can analyze (owner override)
r = chat(T_MGR, "xylophone bananas", mode="my_docs").json()
check("A never sees B content",
      r["sources"] == [] and "xylophone" not in r["message"]["content"].lower(),
      r["message"]["content"][:200])

print("== 8. missing information ==")
import hashlib as _hl
_pool = ("quantum entanglement horsepower photosynthesis democracy reactor "
         "fjord waltz jukebox kayak mop zeppelin quasar nebula glacier "
         "tundra vortex onyx ivory umbra pixel gizmo widget").split()
_doc_buckets = {int(_hl.sha256(w.encode()).hexdigest(), 16) % 255 for w in DOC.decode().split()}
_neg = [w for w in _pool if int(_hl.sha256(w.encode()).hexdigest(), 16) % 255 not in _doc_buckets][:5]
assert len(_neg) >= 3, "negative-word pool exhausted"
r = chat(T_MGR, " ".join(_neg), document_ids=[did]).json()
check("honest not-found", r["sources"] == [] and "couldn't find" in r["message"]["content"],
      r["message"]["content"][:200])

print("== 9. re-index ==")
r = c.post(f"/api/v1/docs/{did}/reindex", headers=H(T_MGR)).json()
check("reindex READY + chunks", r["status"] == "READY" and r.get("chunks", 0) >= 1, str(r)[:200])
check("vectors rebuilt", get_vector_store().count_for(did) >= 1)

print("== 10. deletion purges vectors ==")
c.delete(f"/api/v1/docs/{did}", headers=H(T_ADM))
check("vectors dropped", get_vector_store().count_for(did) == 0)
r = chat(T_MGR, "zebra timeline", mode="my_docs").json()
check("deleted doc not retrieved", r["sources"] == []
      and "couldn't find" in r["message"]["content"])

print("== 11. empty + malformed docs ==")
e1 = up(T_MGR, "blank.txt", b"   \n  ").json()["id"]
r = analyze(T_MGR, e1).json()
check("whitespace doc READY 0 chunks", r["status"] == "READY" and r.get("chunks", 0) == 0,
      str(r)[:200])
import glob as _g
for f in _g.glob(os.path.join(_tmp, "uploads", "*")):
    pass
e2 = up(T_MGR, "gone.txt", b"will vanish").json()["id"]
os.remove(os.path.join(os.environ["SOV_UPLOADS_DIR"],
                       __import__("backend.app.docs_store", fromlist=["get"]).get(e2)["stored"]))
r = analyze(T_MGR, e2).json()
check("missing file -> FAILED", r["status"] == "FAILED", str(r)[:200])

print("== 12. provider failures ==")
os.environ["SOV_EMBED_PROVIDER"] = "fail"
f1 = up(T_MGR, "f.txt", b"content here").json()["id"]
check("embed down -> analyze FAILED",
      analyze(T_MGR, f1).json()["status"] == "FAILED")
check("embed down -> chat 502",
      chat(T_MGR, "hi", mode="my_docs").status_code == 502)
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_VECTOR_BACKEND"] = "fail"
check("vector down -> analyze FAILED",
      analyze(T_MGR, f1).json()["status"] == "FAILED")
check("vector down -> chat 502",
      chat(T_MGR, "hi", mode="my_docs").status_code == 502)
del os.environ["SOV_VECTOR_BACKEND"]
check("recovered", chat(T_MGR, "hi").status_code == 200)

print("== 13. invalid RAG requests ==")
check("bad mode -> 422", chat(T_MGR, "hi", mode="nope").status_code == 422)
check(">20 doc_ids -> 422", chat(T_MGR, "hi", document_ids=[f"d-{i}" for i in range(21)]).status_code == 422)

print("== 14. audit ==")
rows = audit_log.rows(1000)
actions = {r["action"] for r in rows}
for a in ["doc_index", "rag_retrieval", "doc_question", "doc_reindex"]:
    check(f"{a} audited", a in actions, str(sorted(actions)))
blob = " ".join((r.get("detail") or "") for r in rows)
check("no chunk content in audit", "copper wiring section seven" not in blob)

print("== 15. status rag block ==")
st = c.get("/api/v1/ai/status", headers=H(T_MGR)).json()
check("rag status", st.get("rag", {}).get("embed_provider") == "hash"
      and "vector_backend" in st.get("rag", {}), str(st.get("rag")))

uid = [u["id"] for u in c.get("/api/v1/admin/users", headers=H(T_ADM)).json()["users"]
       if u["username"] == "rag_op2"][0]
c.delete(f"/api/v1/admin/users/{uid}", headers=H(T_ADM))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
