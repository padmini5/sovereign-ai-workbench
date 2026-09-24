"""Step 2 tests: REAL server-side multi-model task routing (run:
python tests/test_model_routing.py). Isolated temp DBs, mock AI.

Maps to the 12 required proofs:
  1. DOCUMENT routes to llama3.2:3b          (sections 1, 3)
  2. GENERAL routes to llama3.2:3b           (sections 1, 3)
  3. CODING routes to qwen2.5-coder:1.5b     (sections 1, 3)
  4. routing occurs after authentication     (section 4)
  5. unauthorized users cannot bypass RBAC   (section 5)
  6. unauthorized docs never reach the model (section 6)
  7. the selected model is actually invoked  (section 7)
  8. model-unavailable failure is honest     (section 8)
  9. audit records the actual selected model (section 9)
 10. inspection workflow still works         -> test_sih_workflow.py (unchanged)
 11. RAG authorization tests still pass      -> test_phase2/3, test_assistant_demo
 12. agent security tests still pass         -> test_step21/26, test_image_privacy
(10-12: full suite runs the UNCHANGED existing files; section 10 below pins
the structural invariants this change must not break.)
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_route_")
for k in ["SOV_AUTH_DB", "SOV_AUTH_AUDIT_DB", "SOV_CHAT_DB", "SOV_DOCS_DB",
          "SOV_VECTORS_DB", "SOV_UPLOADS_DIR", "SOV_AGENTS_DB",
          "SOV_APPROVALS_DB", "SOV_APPROVALS_DIR", "SOV_SYSCONFIG_PATH",
          "SOV_WORK_DB"]:
    os.environ[k] = os.path.join(_tmp, k.replace("SOV_", "").lower())
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"
# Deterministic model-config defaults for this process (restored by exit).
for k in ("OLLAMA_MODEL", "SOV_MODEL_GENERAL", "SOV_MODEL_CODING",
          "SOV_OLLAMA_MODEL", "SOV_MODEL_DEFAULT", "OLLAMA_BASE_URL"):
    os.environ.pop(k, None)

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


def chat(tok, body):
    return c.post("/api/v1/ai/chat", json=body, headers=H(tok))


def msg(text, **kw):
    return {"messages": [{"role": "user", "content": text}], **kw}


def rows():
    return audit_log.rows(8000)


def n_routes():
    return sum(1 for x in rows() if x["action"] == "model_route")


T_ADM = login("admin", "Admin123!")
T_MGR = login("manager", "Mgr123!")


def mk_user(username, role):
    r = c.post("/api/v1/admin/users",
               json={"username": username, "password": "Tmp12345", "role": role},
               headers=H(T_ADM))
    assert r.status_code in (200, 201), r.text
    return login(username, "Tmp12345")


T_EMP = mk_user("emp_rt", "EMPLOYEE")
T_E2 = mk_user("emp2_rt", "EMPLOYEE")
T_AUD = mk_user("aud_rt", "auditor")


# ---------------- 1. classification rules (deterministic, no LLM) ----------------
print("== 1. task classification rules ==")
from backend.app.ai.task_router import classify, model_for, normalize_category  # noqa: E402

check("report summary -> DOCUMENT",
      classify("Summarize this inspection report") == "DOCUMENT")
check("procedure explain -> DOCUMENT",
      classify("Explain this company procedure") == "DOCUMENT")
check("python function -> CODING",
      classify("Write a Python function to parse CSV files") == "CODING")
check("fix python error -> CODING",
      classify("Fix this Python error") == "CODING")
check("assigned work -> GENERAL",
      classify("Help me understand today's assigned work") == "GENERAL")
check("explicit metadata wins over keywords",
      classify("Summarize this inspection report", explicit="CODING") == "CODING")
try:
    normalize_category("gpt-4o")
    check("invalid category raises (fail-loud)", False)
except ValueError:
    check("invalid category raises (fail-loud)", True)
check("coding/doc tie resolves to DOCUMENT (conservative)",
      classify("Fix this Python error in the report") == "DOCUMENT")
check("verified doc context fallback -> DOCUMENT",
      classify("what does this say", has_docs=True) == "DOCUMENT")
check("empty/no signal -> GENERAL", classify("") == "GENERAL")
check("case-insensitive", classify("SUMMARIZE THE REPORT") == "DOCUMENT")

# ---------------- 2. model configuration (env, not hardcoded) ----------------
print("== 2. model configuration ==")
check("GENERAL default llama3.2:3b", model_for("GENERAL") == "llama3.2:3b")
check("DOCUMENT default llama3.2:3b", model_for("DOCUMENT") == "llama3.2:3b")
check("CODING default qwen2.5-coder:1.5b", model_for("CODING") == "qwen2.5-coder:1.5b")
try:
    os.environ["SOV_MODEL_CODING"] = "unit-coder-override"
    check("SOV_MODEL_CODING env override",
          model_for("CODING") == "unit-coder-override")
finally:
    os.environ.pop("SOV_MODEL_CODING", None)
try:
    os.environ["OLLAMA_MODEL"] = "unit-general-override"
    check("existing OLLAMA_MODEL chain feeds general/doc",
          model_for("DOCUMENT") == "unit-general-override"
          and model_for("GENERAL") == "unit-general-override")
finally:
    os.environ.pop("OLLAMA_MODEL", None)
from backend.app.sysconfig import CONFIG_SCHEMA  # noqa: E402
check("sysconfig schema exposes SOV_MODEL_CODING",
      CONFIG_SCHEMA.get("SOV_MODEL_CODING", {}).get("type") == "model")
check("sysconfig schema exposes SOV_MODEL_GENERAL",
      CONFIG_SCHEMA.get("SOV_MODEL_GENERAL", {}).get("type") == "model")
st = c.get("/api/v1/ai/status", headers=H(T_EMP)).json()
check("status reports backend routing config",
      st.get("routing", {}).get("general") == "llama3.2:3b"
      and st.get("routing", {}).get("coding") == "qwen2.5-coder:1.5b", str(st)[:200])

# ---------------- 3. chat routes to the right local model ----------------
print("== 3. chat routing (DOCUMENT/GENERAL/CODING) ==")
r = chat(T_EMP, msg("Summarize this inspection report"))
j = r.json() if r.status_code == 200 else {}
check("DOCUMENT chat 200", r.status_code == 200, r.text[:200])
check("DOCUMENT task_type", j.get("task_type") == "DOCUMENT", str(j)[:200])
check("DOCUMENT selected llama3.2:3b",
      j.get("selected_model") == "llama3.2:3b", str(j)[:200])
check("mock honestly reports itself as answerer",
      j.get("provider") == "mock" and j.get("model") == "mock", str(j)[:200])
convo_doc = j.get("conversation_id", "")

r = chat(T_EMP, msg("Help me understand today's assigned work"))
j = r.json() if r.status_code == 200 else {}
check("GENERAL chat 200", r.status_code == 200, r.text[:200])
check("GENERAL -> task_type+llama3.2:3b",
      j.get("task_type") == "GENERAL" and j.get("selected_model") == "llama3.2:3b",
      str(j)[:200])

r = chat(T_EMP, msg("Write a Python function to parse CSV files"))
j = r.json() if r.status_code == 200 else {}
check("CODING chat 200", r.status_code == 200, r.text[:200])
check("CODING -> task_type+qwen2.5-coder:1.5b",
      j.get("task_type") == "CODING" and j.get("selected_model") == "qwen2.5-coder:1.5b",
      str(j)[:200])

r = chat(T_EMP, msg("Summarize this inspection report", task_type="coding"))
j = r.json() if r.status_code == 200 else {}
check("explicit task_type beats keyword classification",
      r.status_code == 200 and j.get("task_type") == "CODING"
      and j.get("selected_model") == "qwen2.5-coder:1.5b", str(j)[:200])

check("invalid task_type -> 422",
      chat(T_EMP, msg("hello", task_type="SUMMARY")).status_code == 422)
check("model name as task_type -> 422 (clients never name models)",
      chat(T_EMP, msg("hello", task_type="qwen2.5-coder:1.5b")).status_code == 422)
check("ChatIn has no model field",
      "model" not in __import__("backend.app.ai_api", fromlist=["x"]).ChatIn.model_fields)

if convo_doc:
    d = c.get(f"/api/v1/ai/conversations/{convo_doc}", headers=H(T_EMP)).json()
    amsgs = [m for m in d["messages"] if m["role"] == "assistant"]
    check("stored assistant msgs keep honest provider+model (no fake label)",
          bool(amsgs) and all(m["provider"] == "mock" and m["model"] == "mock"
                              for m in amsgs), str(amsgs)[:200])

# ---------------- 4. routing happens only after authentication ----------------
print("== 4. routing after authentication ==")
before = n_routes()
r = c.post("/api/v1/ai/chat", json=msg("Write a Python function to sort a list"))
check("no token -> 401/403", r.status_code in (401, 403), str(r.status_code))
check("no token -> no routing decision recorded", n_routes() == before,
      f"{before} -> {n_routes()}")
before = n_routes()
r = chat(T_EMP, msg("Write a Python function to zip two lists"))
check("authenticated -> 200", r.status_code == 200, r.text[:150])
check("authenticated -> exactly one routing decision", n_routes() == before + 1,
      f"{before} -> {n_routes()}")

# ---------------- 5. RBAC: unauthorized cannot bypass routing ----------------
print("== 5. RBAC before routing ==")
before = n_routes()
r = chat(T_AUD, msg("Write a Python function to parse CSV files"))
check("auditor (no AI_CHAT) -> 403", r.status_code == 403, str(r.status_code))
r = chat(T_AUD, msg("hello", task_type="CODING"))
check("explicit task_type does not bypass RBAC -> 403",
      r.status_code == 403, str(r.status_code))
check("denied attempts produced no routing decision", n_routes() == before,
      f"{before} -> {n_routes()}")

# ---------------- 6. authorization before routing (documents) ----------------
print("== 6. document authorization before the model is selected ==")
import backend.app.ai_api as ai_api_mod  # noqa: E402


def up(tok, name, data):
    rr = c.post("/api/v1/docs/upload", files={"f": (name, data, "text/plain")},
                headers=H(tok))
    assert rr.status_code == 200, rr.text
    did = rr.json()["id"]
    # Upload alone does not index; the explicit analyze step runs
    # extract -> chunk -> embed -> store vectors (same as assistant_demo).
    aa = c.post(f"/api/v1/docs/{did}/analyze", headers=H(tok))
    assert aa.status_code == 200, aa.text
    assert aa.json().get("proc_status") == "done", aa.text[:200]
    return did


did_a = up(T_E2, "own.txt",
           b"ROUTERALPHA routing procedure. The emergency valve must be "
           b"closed before maintenance.")
did_b = up(T_EMP, "foreign.txt",
           b"ROUTERBETA confidential compensation figure forty two.")


class _Recorder:
    def __init__(self):
        self.calls = []

    def chat(self, user, messages, tools=None, lang="en", extra_context="",
             task_type="", **kw):
        self.calls.append({"task_type": task_type, "extra_context": extra_context})
        return {"text": "RECORDER-ANSWER", "provider": "recorder",
                "model": "recorder", "task_type": task_type,
                "selected_model": model_for(task_type) if task_type else "",
                "prompt_id": user["role"], "tools_used": list(tools or []),
                "elapsed_s": 0.0}


rec = _Recorder()
orig_gs = ai_api_mod.get_service
ai_api_mod.get_service = lambda: rec
try:
    before = n_routes()
    r = chat(T_E2, msg("What does this document say?", document_ids=[did_b]))
    check("foreign document -> 403", r.status_code == 403, str(r.status_code))
    check("foreign document -> model never called", len(rec.calls) == 0,
          str(rec.calls)[:200])
    check("foreign document -> no routing decision", n_routes() == before)
    check("foreign document content absent from response",
          "ROUTERBETA" not in r.text, r.text[:200])

    before = n_routes()
    r = chat(T_E2, msg("ROUTERALPHA procedure emergency valve",
                       document_ids=[did_a]))
    check("own document -> 200", r.status_code == 200, r.text[:200])
    check("own document -> model invoked once with DOCUMENT route",
          len(rec.calls) == 1 and rec.calls[0]["task_type"] == "DOCUMENT",
          str(rec.calls)[:200])
    ctx = rec.calls[0]["extra_context"] if rec.calls else ""
    check("model received OWN authorized content",
          "ROUTERALPHA" in ctx, ctx[:200])
    check("model did NOT receive foreign content",
          "ROUTERBETA" not in ctx, ctx[:200])
    check("authorized request -> one routing decision", n_routes() == before + 1,
          f"{before} -> {n_routes()}")
finally:
    ai_api_mod.get_service = orig_gs

# ---------------- 7. the selected model is actually invoked ----------------
print("== 7. selected model is actually invoked ==")
from backend.app.ai.mock_provider import MockProvider  # noqa: E402
from backend.app.ai.ollama_provider import OllamaProvider  # noqa: E402
from backend.app.ai.service import AIService  # noqa: E402

prov = OllamaProvider(base_url="http://127.0.0.1:9", model="llama3.2:3b")
p = prov._chat_payload([{"role": "user", "content": "x"}], "sys", False,
                       model="qwen2.5-coder:1.5b")
check("ollama payload uses the routed model override",
      p["model"] == "qwen2.5-coder:1.5b", str(p.get("model")))
p = prov._chat_payload([{"role": "user", "content": "x"}], "sys", False)
check("ollama payload defaults to configured model",
      p["model"] == "llama3.2:3b", str(p.get("model")))

svc = AIService(MockProvider())
user7 = {"id": "u-rt7", "role": "OPERATOR"}
res = svc.chat(user7, [{"role": "user", "content": "parse the csv"}],
               task_type="CODING")
check("service reports CODING selection",
      res["task_type"] == "CODING"
      and res["selected_model"] == "qwen2.5-coder:1.5b", str(res)[:200])
check("provider.generate received the selected model",
      svc.provider.calls[-1]["model"] == "qwen2.5-coder:1.5b",
      str(svc.provider.calls[-1]))
check("service reports honest answerer (mock, not the route target)",
      res["model"] == "mock", str(res.get("model")))
res = svc.chat(user7, [{"role": "user", "content": "read the report"}],
               task_type="DOCUMENT")
check("provider.generate received llama3.2:3b for DOCUMENT",
      svc.provider.calls[-1]["model"] == "llama3.2:3b",
      str(svc.provider.calls[-1]))
svc.generate([{"role": "user", "content": "hi"}], task_type="GENERAL")
check("agent wrapper generate() routes GENERAL to llama3.2:3b",
      svc.provider.calls[-1]["model"] == "llama3.2:3b",
      str(svc.provider.calls[-1]))

# ---------------- 8. honest failure when selected model unavailable ----------------
print("== 8. honest model-unavailable failure ==")
os.environ["SOV_AI_PROVIDER"] = "mock-fail"
before_chat = sum(1 for x in rows() if x["action"] == "ai_chat")
before = n_routes()
r = chat(T_EMP, msg("hello there"))
det = str((r.json() or {}).get("detail", "")) if r.status_code == 502 else ""
check("failing provider -> 502", r.status_code == 502, r.text[:200])
check("honest wording present",
      "Selected local model is unavailable." in det, det[:200])
check("legacy honest prefix kept (verify_frontend_live pin)",
      "AI provider unavailable" in det, det[:200])
check("no fake answer payload", "message" not in (r.json() or {}), str(r.json())[:150])
check("no success ai_chat recorded for the failed attempt",
      sum(1 for x in rows() if x["action"] == "ai_chat") == before_chat)
check("failed attempt still audited the routing decision", n_routes() == before + 1)
prov_err = [x for x in rows() if x["action"] == "ai_provider_error"]
check("provider error audited with task_type+model",
      any("task_type=GENERAL model=llama3.2:3b" in (x.get("detail") or "")
          for x in prov_err), str(prov_err[-1:]))

os.environ["SOV_AI_PROVIDER"] = "ollama"
os.environ["OLLAMA_MODEL"] = "unit-missing-model-not-installed"
r = chat(T_EMP, msg("hello there again"))
det = str((r.json() or {}).get("detail", "")) if r.status_code == 502 else ""
check("missing real ollama model -> honest 502",
      r.status_code == 502 and "Selected local model is unavailable." in det,
      f"{r.status_code} {det[:150]}")
check("no silent fallback claim in the error",
      "qwen2.5-coder" not in det and "llama3.2" not in det, det[:150])
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ.pop("OLLAMA_MODEL", None)

# ---------------- 9. audit records the actual selected model ----------------
print("== 9. audit: routing decision + actual model ==")
details = [x.get("detail") or "" for x in rows() if x["action"] == "model_route"]
check("model_route: CODING -> qwen2.5-coder:1.5b exact",
      "task_type=CODING model=qwen2.5-coder:1.5b" in details, str(details[:3]))
check("model_route: DOCUMENT -> llama3.2:3b exact",
      "task_type=DOCUMENT model=llama3.2:3b" in details)
check("model_route: GENERAL -> llama3.2:3b exact",
      "task_type=GENERAL model=llama3.2:3b" in details)
check("model_route carries no message/document content",
      all(d.startswith("task_type=") for d in details)
      and not any("ROUTERALPHA" in d or "ROUTERBETA" in d for d in details))
ai_blob = " ".join(x.get("detail") or "" for x in rows() if x["action"] == "ai_chat")
check("ai_chat detail carries task_type + selected model",
      "task_type=CODING selected_model=qwen2.5-coder:1.5b" in ai_blob,
      ai_blob[:250])

r = c.post("/api/v1/agents/document_analysis/run",
           json={"goal": "Summarize the company safety procedure document "
                         "for review"}, headers=H(T_MGR))
jr = r.json() if r.status_code == 200 else {}
check("agent run completes", r.status_code == 200
      and jr.get("state") == "COMPLETED", r.text[:200])
run_routes = [x for x in rows() if x["action"] == "model_route"
              and x.get("resource") == jr.get("id")]
check("agent run audited task_type=DOCUMENT model=llama3.2:3b",
      any((x.get("detail") or "") == "task_type=DOCUMENT model=llama3.2:3b"
          for x in run_routes), str(run_routes))

# ---------------- 10-12. structural invariants (full suites run unchanged) ----------------
print("== 10-12. workflow/registry invariants ==")
from backend.app.agents import AGENT_REGISTRY  # noqa: E402
ia = AGENT_REGISTRY.get("inspection_approval", {})
check("inspection workflow intact + routed DOCUMENT",
      ia.get("requires_document") is True and ia.get("deterministic_plan") is True
      and ia.get("task_type") == "DOCUMENT", str(ia)[:200])
check("document agents route DOCUMENT, assistant agents GENERAL",
      AGENT_REGISTRY["document_analysis"].get("task_type") == "DOCUMENT"
      and AGENT_REGISTRY["work_assistant"].get("task_type") == "GENERAL"
      and AGENT_REGISTRY["admin_assistant"].get("task_type") == "GENERAL")
from backend.app.agents_api import RunIn  # noqa: E402
check("agent API exposes no model/task_type input (server-side routing only)",
      "model" not in RunIn.model_fields and "task_type" not in RunIn.model_fields,
      str(list(RunIn.model_fields)))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
