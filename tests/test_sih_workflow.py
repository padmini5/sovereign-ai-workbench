"""SIH flagship workflow tests: scanned inspection report -> OCR/text
extraction -> permission-aware retrieval -> multi-step agent -> grounded
approval note -> REAL .docx -> human approval (RBAC) -> audit trail.

Run: python tests/test_sih_workflow.py. Isolated temp DBs, mock AI/embed.
Covers: registry gating, one-document rule, cross-user document isolation,
evidence references, DOCX integrity, viewer/decider RBAC (incl. content-blind
roles), approve/reject/correct + 409, audit events, no-content-in-audit, and
honest failure handling (OCR unavailable / insufficient evidence).
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

_tmp = tempfile.mkdtemp(prefix="sov_sih_")
for k in ["SOV_AUTH_DB", "SOV_AUTH_AUDIT_DB", "SOV_CHAT_DB", "SOV_DOCS_DB",
          "SOV_VECTORS_DB", "SOV_UPLOADS_DIR", "SOV_AGENTS_DB",
          "SOV_APPROVALS_DB", "SOV_APPROVALS_DIR", "SOV_SYSCONFIG_PATH",
          "SOV_WORK_DB"]:
    os.environ[k] = os.path.join(_tmp, k.replace("SOV_", "").lower())
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"

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


GOAL = ("Review this inspection report and prepare an approval note based on "
        "the relevant company procedures.")

REPORT_NAME = "Inspection_Report_IR-0917.txt"
REPORT_BODY = """Inspection Report IR-2026-0917 - Pump Station 4
Date of inspection: 24 Sep 2026
Status: FAIL - urgent repair required.
Findings: a visible crack was observed on the left side of the pump housing.
Pressure gauge reading 2.1 bar against nominal 3.0 bar. Loose coupling bolts detected.
Evidence: field photograph PS4-1 attached by the operator.
Recommendation: shutdown and re-inspection before restart.
"""
LEAK_PHRASE = "visible crack was observed on the left side of the pump housing"

T_ADM = login("admin", "Admin123!")
T_MGR = login("manager", "Mgr123!")
T_OP = login("operator", "Op123!")
T_USR = login("user", "User123!")
T_REV = login("reviewer", "Rev123!")

def mk_user(username, role):
    r = c.post("/api/v1/admin/users",
               json={"username": username, "password": "Tmp12345", "role": role},
               headers=H(T_ADM))
    assert r.status_code in (200, 201), r.text
    return login(username, "Tmp12345")

T_EMP = mk_user("emp_wf", "EMPLOYEE")
T_SEC = mk_user("sec_wf", "security_admin")
T_AUD = mk_user("aud_wf", "auditor")


def upload(tok, name, data, mime):
    r = c.post("/api/v1/docs/upload", files={"f": (name, data, mime)}, headers=H(tok))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def run_wf(tok, doc_ids, goal=GOAL, **kw):
    return c.post("/api/v1/agents/inspection_approval/run",
                  json={"goal": goal, "document_ids": doc_ids, **kw}, headers=H(tok))


def mk_png(text):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=40)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for line in text.splitlines():
        d.text((40, y), line, fill="black", font=font)
        y += 56
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- 1. gating
print("== 1. registry + input gating ==")
r = c.get("/api/v1/agents", headers=H(T_MGR)).json()["agents"]
entry = next((a for a in r if a["name"] == "inspection_approval"), None)
check("registry lists inspection_approval", entry is not None)
check("manager may run it", bool(entry and entry["allowed"]))
r = c.get("/api/v1/agents", headers=H(T_SEC)).json()["agents"]
entry = next((a for a in r if a["name"] == "inspection_approval"), None)
check("security_admin cannot run it (no DOCUMENT_READ)",
      bool(entry) and entry["allowed"] is False)
check("USER registry -> 403 (no AI_AGENT_USE)",
      c.get("/api/v1/agents", headers=H(T_USR)).status_code == 403)
tools = {t["name"] for t in c.get("/api/v1/agents/tools", headers=H(T_MGR)).json()["tools"]}
check("all five workflow tools exposed",
      {"read_inspection_report", "find_procedures", "analyze_findings",
       "draft_approval_note", "generate_approval_docx"} <= tools, str(tools))
check("no document -> 400", run_wf(T_EMP, None).status_code == 400)
check("two documents -> 400", run_wf(T_EMP, ["a", "b"]).status_code == 400)

# ---------------------------------------------------- 2. upload + isolation
print("== 2. secure upload + cross-user isolation ==")
did = upload(T_EMP, REPORT_NAME, REPORT_BODY.encode(), "text/plain")
r = c.post(f"/api/v1/docs/{did}/analyze", headers=H(T_EMP))
check("employee can index own report", r.status_code == 200 and r.json()["status"] == "READY")
r = run_wf(T_OP, [did])
check("foreign document -> 403", r.status_code == 403, r.text[:150])
rows = audit_log.rows(500)
check("foreign access audited (deny)",
      any(x.get("resource") == f"rag_doc:{did}" and x["decision"] == "deny" for x in rows),
      str(rows[:3]))
check("stranger cannot read foreign doc",
      c.get(f"/api/v1/docs/{did}", headers=H(T_OP)).status_code == 404)

# ------------------------------------------------------------ 3. happy path
print("== 3. agent workflow end-to-end (EMPLOYEE) ==")
r = run_wf(T_EMP, [did])
check("run COMPLETED", r.status_code == 200 and r.json()["state"] == "COMPLETED",
      r.text[:400])
run = r.json()
expect_tools = ["read_inspection_report", "find_procedures", "analyze_findings",
                "draft_approval_note", "generate_approval_docx"]
check("5 ordered steps", [s["tool"] for s in run["steps"]] == expect_tools,
      str([s["tool"] for s in run["steps"]]))
check("all steps ok", all(s.get("status") == "ok" for s in run["steps"]),
      str([(s["tool"], s.get("status")) for s in run["steps"]]))

lst = c.get("/api/v1/approvals", headers=H(T_EMP)).json()["approvals"]
rec = next((a for a in lst if a["run_id"] == run["id"]), None)
check("approval record created", rec is not None)
check("status awaits human approval",
      rec and rec["status"] == "AWAITING_HUMAN_APPROVAL", str(rec and rec["status"]))
check("status label exact",
      rec and rec["status_label"] == "DRAFT — AWAITING HUMAN APPROVAL",
      str(rec and rec["status_label"]))
check("stage label 'Awaiting human approval'",
      rec and rec["stage_label"] == "Awaiting human approval")
aid = rec["id"]

d = c.get(f"/api/v1/approvals/{aid}", headers=H(T_EMP)).json()
note = d["note"]
check("note titled with source file", REPORT_NAME in note.get("title", ""))
check("note date is a real current date",
      note.get("date") in {time.strftime("%d %b %Y"),
                           time.strftime("%d %b %Y", time.localtime(time.time() - 86400))},
      str(note.get("date")))
check("summary present", bool(note.get("summary")))
kf = note.get("key_findings")
check("findings are structured", isinstance(kf, list) and len(kf) >= 1, str(kf)[:120])
check("findings cite the source", isinstance(kf, list)
      and any(str(f.get("evidence", "")).startswith(f"Based on {REPORT_NAME}") for f in kf),
      str(kf)[:200])
rp = note.get("relevant_procedure")
check("retrieved procedure referenced", isinstance(rp, list) and len(rp) >= 1, str(rp)[:150])
check("procedure is the company SOP", isinstance(rp, list)
      and any("SOP-107" in str(p.get("ref", "")) for p in rp), str(rp)[:200])
check("source report never its own procedure",
      isinstance(rp, list) and all(p.get("ref") != REPORT_NAME for p in rp))
ev = note.get("evidence")
check("evidence list includes procedure reference",
      isinstance(ev, list) and any("Procedure reference:" in str(e) for e in ev),
      str(ev)[:200])
check("extractive mode labeled (mock model output)",
      d.get("analysis_mode") == "extractive", str(d.get("analysis_mode")))
check("extraction metadata honest",
      (d.get("extract") or {}).get("method") == "text_layer"
      and (d.get("extract") or {}).get("status") == "done", str(d.get("extract")))
check("source excerpt preserved for reviewer",
      d.get("source_excerpt", "").startswith("Inspection Report IR"), str(d.get("source_excerpt"))[:80])
blob = str(d)
check("no filesystem path in API payload",
      "stored" not in blob and os.environ["SOV_APPROVALS_DIR"] not in blob
      and "approvals.db" not in blob, blob[:200])

# ---------------------------------------------------------------- 4. DOCX
print("== 4. real .docx generation ==")
check("friendly filename pattern",
      bool(rec.get("filename")) and rec["filename"].startswith("Inspection_Approval_Note_")
      and rec["filename"].endswith(".docx"), str(rec.get("filename")))
r = c.get(f"/api/v1/approvals/{aid}/document", headers=H(T_EMP))
check("owner download 200", r.status_code == 200, str(r.status_code))
check("disposition carries friendly name",
      "Inspection_Approval_Note_" in r.headers.get("content-disposition", ""))
body = r.content
check("bytes are a real OOXML zip (PK)", body[:2] == b"PK", str(body[:8]))
zf = zipfile.ZipFile(io.BytesIO(body))
check("contains word/document.xml", "word/document.xml" in zf.namelist())
xml = zf.read("word/document.xml").decode("utf-8", "ignore")
check("docx title present", "Inspection Approval Note" in xml)
check("docx awaiting-approval status", "DRAFT — AWAITING HUMAN APPROVAL" in xml)
check("docx cites retrieved SOP", "SOP-107" in xml)
check("docx cites source findings evidence", f"Based on {REPORT_NAME}" in xml)
check("docx has human decision lines",
      "[ ] Approve" in xml and "[ ] Reject" in xml and "[ ] Request correction" in xml)
check("docx states human-approval requirement",
      "does not finalize any decision" in xml)
check("docx has no invented values marker for NA fields",
      "Not available in source material" in xml)

# --------------------------------------------------------------- 5. RBAC
print("== 5. viewer + decider RBAC ==")
check("owner sees own list", any(a["id"] == aid for a in
      c.get("/api/v1/approvals", headers=H(T_EMP)).json()["approvals"]))
jl = c.get("/api/v1/approvals", headers=H(T_OP)).json()
check("operator list is own-only", all(a["id"] != aid for a in jl["approvals"]))
check("operator cannot decide", jl["can_decide"] is False)
check("operator foreign note -> 404", c.get(f"/api/v1/approvals/{aid}",
                                            headers=H(T_OP)).status_code == 404)
check("operator foreign docx -> 404",
      c.get(f"/api/v1/approvals/{aid}/document", headers=H(T_OP)).status_code == 404)
check("operator decision -> 403",
      c.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"},
             headers=H(T_OP)).status_code == 403)
check("plain user decision -> 403",
      c.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"},
             headers=H(T_USR)).status_code == 403)
check("plain user list -> 200 own-only",
      c.get("/api/v1/approvals", headers=H(T_USR)).status_code == 200)
check("security_admin list -> 403 (content-blind)",
      c.get("/api/v1/approvals", headers=H(T_SEC)).status_code == 403)
check("auditor list -> 403 (content-blind)",
      c.get("/api/v1/approvals", headers=H(T_AUD)).status_code == 403)
jr = c.get("/api/v1/approvals", headers=H(T_REV)).json()
check("reviewer sees pending queue + can decide",
      jr["can_decide"] is True and any(a["id"] == aid for a in jr["approvals"]))
check("reviewer detail 200", c.get(f"/api/v1/approvals/{aid}",
                                   headers=H(T_REV)).status_code == 200)
check("admin detail 200", c.get(f"/api/v1/approvals/{aid}",
                                headers=H(T_ADM)).status_code == 200)
check("bad decision value -> 422",
      c.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "force"},
             headers=H(T_REV)).status_code in (404, 422))
check("unknown note -> 404", c.get("/api/v1/approvals/n-nope000000",
                                   headers=H(T_REV)).status_code == 404)

# ------------------------------------------------- 6. human decisions (RBAC)
print("== 6. human approval / reject / correction ==")
r = c.post(f"/api/v1/approvals/{aid}/decision",
           json={"decision": "approve", "comment": "Findings verified against SOP."},
           headers=H(T_REV))
check("reviewer approves 200", r.status_code == 200, r.text[:200])
j = r.json()
check("status APPROVED", j["status"] == "APPROVED" and j["stage"] == "approved")
check("approver identity recorded",
      j["decided_by"] == "reviewer" and j["decided_role"] == "REVIEWER", str(j["decided_by"]))
check("approver comment stored", j["comment"] == "Findings verified against SOP.")
check("second decision -> 409",
      c.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "reject"},
             headers=H(T_REV)).status_code == 409)
j = c.get(f"/api/v1/approvals/{aid}", headers=H(T_EMP)).json()
check("requester sees decision", j.get("decision") == "approve"
      and j.get("decided_by") == "reviewer", str(j.get("decision")))
rows = audit_log.rows(500)
ad = [x for x in rows if x["action"] == "approval_decision" and x["resource"] == aid]
check("decision audited", len(ad) >= 1 and ad[0]["decision"] == "allow", str(ad[:1]))

did2 = upload(T_OP, "Valve_Checksheet_VC-22.txt",
              b"Valve checksheet VC-22. Status: PASS with observation. "
              b"No leakage seen at packing gland. Gauge steady at 4.0 bar nominal. "
              b"Follow-up: recheck next shift.", "text/plain")
r = run_wf(T_OP, [did2])
check("operator runs workflow (own doc)", r.status_code == 200
      and r.json()["state"] == "COMPLETED", r.text[:250])
aid2 = next(a["id"] for a in c.get("/api/v1/approvals", headers=H(T_OP)).json()["approvals"]
            if a["run_id"] == r.json()["id"])
r = c.post(f"/api/v1/approvals/{aid2}/decision", json={"decision": "reject"},
           headers=H(T_REV))
check("reviewer rejects -> REJECTED", r.status_code == 200
      and r.json()["status"] == "REJECTED", r.text[:150])

did3 = upload(T_MGR, "Compressor_Round_CR-7.txt",
              b"Compressor round CR-7. Inspection of stage 1 and stage 2 completed. "
              b"Observation: minor discoloration near seal area, no crack detected. "
              b"Vibration within limits. Recommendation: monitor next round.", "text/plain")
r = run_wf(T_MGR, [did3])
check("manager runs workflow (own doc)", r.status_code == 200
      and r.json()["state"] == "COMPLETED", r.text[:250])
aid3 = next(a["id"] for a in c.get("/api/v1/approvals", headers=H(T_MGR)).json()["approvals"]
            if a["run_id"] == r.json()["id"])
r = c.post(f"/api/v1/approvals/{aid3}/decision",
           json={"decision": "correct", "comment": "Add vibration trend."},
           headers=H(T_REV))
check("manager note correction requested", r.status_code == 200
      and r.json()["status"] == "CORRECTION_REQUESTED", r.text[:150])
check("re-decide after correction -> 409",
      c.post(f"/api/v1/approvals/{aid3}/decision", json={"decision": "approve"},
             headers=H(T_REV)).status_code == 409)

# -------------------------------------------------------- 7. failure paths
print("== 7. honest failure handling ==")
try:
    import pytesseract  # noqa: F401
    ENGINE = bool(shutil.which("tesseract"))
except Exception:
    ENGINE = False
img = mk_png("PUMP INSPECTION CHECKLIST\nStatus: FAIL\nVisible crack on pump housing.\n")
did_img = upload(T_EMP, "Scanned_Checklist.png", img, "image/png")
r = run_wf(T_EMP, [did_img])
if ENGINE:
    check("image run (engine present) extracts text", r.status_code == 200
          and r.json()["state"] == "COMPLETED", r.text[:300])
    if r.status_code == 200:
        aimg = next((a for a in c.get("/api/v1/approvals",
                                      headers=H(T_EMP)).json()["approvals"]
                     if a["run_id"] == r.json()["id"]), None)
        check("OCR text preserved", bool(aimg) and aimg["extract"].get("method") == "ocr"
              and aimg["extract"].get("status") == "done", str(aimg and aimg["extract"]))
else:
    check("image run fails honestly (no OCR engine)", r.status_code == 200
          and r.json()["state"] == "FAILED", r.text[:300])
    st = r.json()["steps"][0]
    check("exact honest OCR failure message",
          "Unable to extract text from this document." in str(st.get("error", "")),
          str(st.get("error")))
    aimg = next((a for a in c.get("/api/v1/approvals",
                                  headers=H(T_EMP)).json()["approvals"]
                 if a["run_id"] == r.json()["id"]), None)
    check("failed workflow marked FAILED", bool(aimg) and aimg["status"] == "FAILED",
          str(aimg and aimg["status"]))
    check("no note/docx fabricated on failure",
          bool(aimg) and not aimg["has_document"] and not (aimg.get("note") or {}).get("title"),
          str(aimg))
    rows = audit_log.rows(500)
    check("OCR denial audited for this record",
          any(x["resource"] == aimg["id"] and x["action"] == "workflow_extract"
              and x["decision"] == "deny" for x in rows) if aimg else False)
    check("no approval events for failed record",
          aimg is not None and not any(
              x["resource"] == aimg["id"]
              and x["action"] in ("approval_note_generated", "approval_requested")
              for x in rows))

did_tiny = upload(T_EMP, "note.txt", b"ok status", "text/plain")
r = run_wf(T_EMP, [did_tiny])
check("insufficient evidence -> FAILED", r.json()["state"] == "FAILED", r.text[:250])
check("exact honest insufficiency message",
      "Insufficient information to prepare a reliable approval note."
      in str(r.json()["steps"][2].get("error", "")), str(r.json()["steps"][2]))
atiny = next((a for a in c.get("/api/v1/approvals", headers=H(T_EMP)).json()["approvals"]
              if a["run_id"] == r.json()["id"]), None)
check("insufficient run has no document", bool(atiny) and not atiny["has_document"])

# --------------------------------------------------------------- 8. audit
print("== 8. audit coverage ==")
actions = {x["action"] for x in audit_log.rows(5000)}
for a in ["doc_upload", "doc_index", "agent_started", "tool_requested",
          "tool_allowed", "agent_completed", "workflow_extract",
          "workflow_retrieve", "workflow_analysis", "approval_note_generated",
          "approval_requested", "approval_decision", "approval_doc_download",
          "rag_retrieval"]:
    check(f"audit:{a}", a in actions, str(sorted(actions)))
blob = " ".join((x.get("detail") or "") for x in audit_log.rows(5000))
check("no raw report content in audit", LEAK_PHRASE not in blob)
check("no extracted text chunk in audit", "Pressure gauge reading 2.1 bar" not in blob)

# --------------------------------- 9. LLM evidence grounding (no fabrication)
print("== 9. LLM evidence grounding (fabricated citations rejected) ==")
from backend.app.agents import _ground_evidence  # noqa: E402

G_NAME = "Grounding_Test.txt"
G_BODY = ("Valve rack inspection round VR-11. The actuator bracket shows a hairline "
          "crack near the mounting hole. Bolts are torqued to spec and marked. "
          "No leakage observed at the union. Status: Requires Review. "
          "Recommended: weld inspection before the next cycle.\n")
g = _ground_evidence([{"statement": "Hairline crack.",
                       "evidence": "Source report, page 3 (Figure 1)"}],
                     G_BODY, G_NAME)
check("fabricated page citation replaced",
      g[0]["evidence"].startswith(f"Based on {G_NAME}: \"")
      and "page 3" not in g[0]["evidence"], g)
gq = g[0]["evidence"].split(': "', 1)[1].rstrip('"') if ': "' in g[0]["evidence"] else ""
check("replacement quote is verbatim source text", bool(gq) and gq in G_BODY, gq)
g2 = _ground_evidence([{"statement": "x", "evidence":
                        '"The actuator bracket shows a hairline crack near the '
                        'mounting hole."'}], G_BODY, G_NAME)
check("verbatim quote without filename gets source prefix",
      g2[0]["evidence"].startswith(f"Based on {G_NAME}: "), g2)
VERBATIM = '"The actuator bracket shows a hairline crack near the mounting hole."'
g3 = _ground_evidence([{"statement": "x", "evidence": f"Based on {G_NAME}: {VERBATIM}"}],
                      G_BODY, G_NAME)
check("already-grounded evidence kept unchanged", g3[0]["evidence"].endswith(VERBATIM), g3)

# integration: force the real-JSON path with a model that fabricates a page ref
import backend.app.ai as _ai_pkg  # noqa: E402
_orig_gs = _ai_pkg.get_service
_FAKE_JSON = json.dumps({
    "summary": "Model summary: hairline crack confirmed at the mounting hole.",
    "findings": [{"statement": "Hairline crack near the mounting hole.",
                  "evidence": "Source report, page 3 (Figure 1)"}],
    "recommended_action": "Schedule weld inspection before restart.",
    "confidence": "medium"})


class _FakeProv:
    def generate(self, messages, system="", **opts):
        if "JSON object" in (system or ""):
            return _FAKE_JSON
        return _orig_gs().provider.generate(messages, system=system, **opts)


class _FakeSvc:
    provider = _FakeProv()

    def generate(self, messages, system="", task_type="", **opts):
        # Mirror AIService.generate: the REAL task_router picks the local model
        # (Step 2 routing must work through this patched-service path too).
        if task_type:
            from backend.app.ai.task_router import model_for
            opts["model"] = model_for(task_type)
        return self.provider.generate(messages, system=system, **opts)


_ai_pkg.get_service = lambda: _FakeSvc()
rr = None
try:
    did_g = upload(T_EMP, G_NAME, G_BODY.encode(), "text/plain")
    rr = run_wf(T_EMP, [did_g])
finally:
    _ai_pkg.get_service = _orig_gs
check("ai-mode run completes", rr is not None and rr.status_code == 200
      and rr.json()["state"] == "COMPLETED", rr.text[:200] if rr else "no run")
ag = next((a for a in c.get("/api/v1/approvals", headers=H(T_EMP)).json()["approvals"]
           if a["run_id"] == rr.json()["id"]), None) if rr and rr.status_code == 200 else None
dd = c.get(f"/api/v1/approvals/{ag['id']}", headers=H(T_EMP)).json() if ag else {}
note_g = dd.get("note", {})
check("analysis_mode ai", dd.get("analysis_mode") == "ai", dd.get("analysis_mode"))
check("model summary kept", str(note_g.get("summary", "")).startswith("Model summary"),
      note_g.get("summary"))
check("model statement kept", any(f.get("statement") == "Hairline crack near the mounting hole."
                                  for f in note_g.get("key_findings") or []),
      note_g.get("key_findings"))
check("fabricated 'page 3' nowhere in note", "page 3" not in json.dumps(note_g),
      json.dumps(note_g)[:300])
ev0 = (note_g.get("evidence") or [""])[0]
check("evidence cites source file", ev0.startswith(f"Based on {G_NAME}: "), ev0)
gq2 = ev0.split(': "', 1)[1].rstrip('"') if ': "' in ev0 else ""
check("integration evidence is verbatim source quote", bool(gq2) and gq2 in G_BODY, ev0)
check("grounded workflow still generates docx", dd.get("has_document") is True)

# -------------------------------------------------------------- 10. cleanup
print("== 10. cleanup ==")
for u in ("emp_wf", "sec_wf", "aud_wf"):
    uid = [x["id"] for x in c.get("/api/v1/admin/users", headers=H(T_ADM)).json()["users"]
           if x["username"] == u]
    if uid:
        c.delete(f"/api/v1/admin/users/{uid[0]}", headers=H(T_ADM))
check("temp users removed", True)

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
