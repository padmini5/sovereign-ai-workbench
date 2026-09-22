"""Step 21 controlled agent orchestration.

Design: plan-then-execute, code-driven. The LLM may SUGGEST a plan (parsed
defensively, allow-listed, falling back to a default), but it never decides
access: every tool call is verified server-side against the authenticated
user's role, permissions, and resource ownership. Adversarial goal text
("ignore permissions…") is inert data — denials are recorded, nothing leaks.

States: CREATED RUNNING WAITING_CONFIRMATION COMPLETED FAILED CANCELLED TIMEOUT.
"""
from __future__ import annotations

import json
import os
import time

from fastapi import HTTPException

from . import agent_store, audit_log
from .rbac import has_permission

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}


class ToolError(Exception):
    pass


# ---------------- tool implementations (all permission-checked) ----------------

def _t_rag_search(user: dict, args: dict) -> dict:
    from . import rag as ragmod
    if not has_permission(user["role"], "DOCUMENT_READ"):
        raise _deny(user, "rag_search", "DOCUMENT_READ")
    ragmod.verify_doc_access(args.get("document_ids") or [], user)
    hits, meta = ragmod.retrieve(user, args.get("query", ""), args.get("document_ids"),
                                 top_k=min(int(args.get("top_k", 4)), 8))
    return {"hits": [{k: h[k] for k in ("doc_id", "filename", "page", "chunk_index",
                                        "score") if k in h} | {"excerpt": h["text"][:600]}
                     for h in hits], "retrieval": meta}


def _t_analyze_document(user: dict, args: dict) -> dict:
    from . import docs_store as store
    from .docs_api import _get_owned
    if not has_permission(user["role"], "DOCUMENT_ANALYZE"):
        raise _deny(user, "analyze_document", "DOCUMENT_ANALYZE")
    doc = _get_owned(args.get("doc_id", ""), user)  # 404 for foreign ids
    text = store.get_text(doc["id"])[:2000]
    return {"doc_id": doc["id"], "filename": doc["filename"], "status": doc["status"],
            "proc_status": doc["proc_status"], "excerpt": text}


def _t_get_image_info(user: dict, args: dict) -> dict:
    import json as _json
    from .docs_api import _get_owned
    if not has_permission(user["role"], "DOCUMENT_READ"):
        raise _deny(user, "get_image_info", "DOCUMENT_READ")
    doc = _get_owned(args.get("doc_id", ""), user)
    if doc["kind"] != "image":
        raise ToolError("not an image document")
    try:
        vision = _json.loads(doc.get("vision") or "{}")
    except Exception:
        vision = {}
    return {"doc_id": doc["id"], "filename": doc["filename"], "vision": vision,
            "proc_status": doc["proc_status"]}


def _t_analyze_authorized_image(user: dict, args: dict) -> dict:
    """Narrow explicit image analysis. No directory/file enumeration: a single
    caller-supplied id, ownership + DOCUMENT_ANALYZE verified server-side,
    result ephemeral (never persisted/indexed)."""
    from . import private_images
    if not has_permission(user["role"], "DOCUMENT_ANALYZE"):
        raise _deny(user, "analyze_authorized_image", "DOCUMENT_ANALYZE")
    try:
        res = private_images.analyze_private_image(
            user, str(args.get("doc_id", "")), str(args.get("question", ""))[:500])
    except HTTPException as e:
        if e.status_code == 403:
            raise
        raise ToolError(str(e.detail)[:200] if hasattr(e, "detail") else "unavailable")
    return {"doc_id": res["doc_id"], "filename": res["filename"],
            "dims": res["dims"], "ocr_status": res["ocr_status"],
            "has_text": res["has_text"], "summary": res["summary"][:1500]}


def _t_list_my_docs(user: dict, args: dict) -> dict:
    from . import docs_store as store
    if not has_permission(user["role"], "DOCUMENT_READ"):
        raise _deny(user, "list_my_docs", "DOCUMENT_READ")
    docs = store.list_for(None if user["role"] == "ADMIN" else user["id"],
                          q=(args.get("q") or "")[:60])
    return {"documents": [{"id": d["id"], "filename": d["filename"], "status": d["status"],
                           "kind": d["kind"]} for d in docs[:20]], "count": len(docs)}


def _t_summarize(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "AI_CHAT"):
        raise _deny(user, "summarize", "AI_CHAT")
    from .ai import get_service
    text = (args.get("text") or "")[:4000]
    if not text.strip():
        raise ToolError("nothing to summarize")
    try:
        out = get_service().provider.generate(
            [{"role": "user", "content": f"Summarize in 5 bullets:\n{text}"}],
            system="You summarize faithfully. Never invent content.", role=user["role"])
    except Exception as e:
        raise ToolError(f"summarizer failed: {e}")
    return {"summary": out[:2000]}


def _t_translate_text(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "AI_CHAT"):
        raise _deny(user, "translate_text", "AI_CHAT")
    from .i18n import SUPPORTED, get_translation_provider
    target = args.get("target", "en")
    if target not in SUPPORTED:
        raise ToolError(f"unsupported language '{target}'")
    try:
        out = get_translation_provider().translate((args.get("text") or "")[:2000], target)
    except Exception as e:
        raise ToolError(f"translation failed: {e}")
    return {"text": out, "target": target}


def _t_generate_report(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "REPORT_CREATE"):
        raise _deny(user, "generate_report", "REPORT_CREATE")
    title = (args.get("title") or "Untitled report")[:120]
    sections = args.get("sections") or []
    if not isinstance(sections, list):
        raise ToolError("sections must be a list")
    body = "\n\n".join(f"## {str(s)[:200]}" for s in sections[:10])
    return {"title": title, "markdown": f"# {title}\n\n{body}"[:8000],
            "note": "draft — requires review before distribution"}


def _t_get_my_info(user: dict, args: dict) -> dict:
    from .ai.tools import run_tool
    return run_tool("get_my_info", user, {})


def _t_list_users(user: dict, args: dict) -> dict:
    from .ai.tools import run_tool
    return run_tool("list_users", user, {})


def _t_get_audit_summary(user: dict, args: dict) -> dict:
    from .ai.tools import run_tool
    return run_tool("get_audit_summary", user, {})


def _t_get_model_info(user: dict, args: dict) -> dict:
    from .ai.tools import run_tool
    return run_tool("get_model_info", user, {})


# ---------------- Step 27 work tools (scope-checked) ----------------

def _work_scope_ids(user: dict) -> list[str] | None:
    """None = ADMIN global. Managers = same-department ids. Others = [self].
    Roles without WORK_READ get [] (no work data at all)."""
    from . import userstore
    from .rbac import has_permission
    if user["role"] == "ADMIN":
        return None
    if not has_permission(user["role"], "WORK_READ"):
        return []
    if user["role"] in ("MANAGER", "approving_manager"):
        rec = userstore.find_by_id(user["id"])
        dept = (rec.get("department") or "") if rec else ""
        ids = [u["id"] for u in userstore.list_users()
               if (u.get("department") or "") == dept and u["active"]]
        return ids or [user["id"]]
    return [user["id"]]


def _t_my_work_summary(user: dict, args: dict) -> dict:
    from . import work as store
    if not has_permission(user["role"], "WORK_READ"):
        raise _deny(user, "my_work_summary", "WORK_READ")
    tasks = store.list_tasks(assignee_ids=[user["id"]])
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    ev = store.list_evidence(owner_ids=[user["id"]])
    asg = store.list_assignments(assignee_ids=[user["id"]])
    att = store.list_attendance([user["id"]])
    return {"tasks_total": len(tasks), "tasks_by_status": by_status,
            "evidence_count": len(ev),
            "assignments_open": sum(1 for a in asg if a["status"] not in ("approved", "reviewed")),
            "attendance_days": len(att),
            "recent_tasks": [{"id": t["id"], "title": t["title"], "status": t["status"],
                              "progress": t["progress"]} for t in tasks[:3]]}


def _t_work_detail(user: dict, args: dict) -> dict:
    from . import work as store
    if not has_permission(user["role"], "WORK_READ"):
        raise _deny(user, "work_detail", "WORK_READ")
    t = store.get_task(str(args.get("work_id", "")))
    if not t:
        raise HTTPException(404, "work item not found")
    scope = _work_scope_ids(user)
    if scope == [] or (scope is not None and t["assignee_id"] not in scope
                       and t["created_by"] != user["id"]):
        raise HTTPException(404, "work item not found")
    return {"id": t["id"], "title": t["title"], "instructions": t["instructions"],
            "status": t["status"], "progress": t["progress"], "due_date": t["due_date"],
            "review_remarks": t["review_remarks"]}


def _t_team_performance(user: dict, args: dict) -> dict:
    from . import userstore
    from . import work as store
    if not has_permission(user["role"], "ANALYTICS_READ"):
        raise _deny(user, "team_performance", "ANALYTICS_READ")
    dept = (args.get("department") or "")[:120]
    if user["role"] != "ADMIN":
        rec = userstore.find_by_id(user["id"])
        own = (rec.get("department") or "") if rec else ""
        if dept and dept != own:
            raise HTTPException(403, "department outside your scope")
        dept = own
    uids = [u["id"] for u in userstore.list_users()
            if u["active"] and (dept == "" or (u.get("department") or "") == dept)]
    rows = []
    for uid in uids:
        tasks = store.list_tasks(assignee_ids=[uid])
        done = sum(1 for t in tasks if t["status"] in ("approved", "reviewed"))
        att = store.list_attendance([uid])
        present = sum(1 for a in att if a["status"] == "present")
        rows.append({"user_id": uid, "tasks": len(tasks), "completed": done,
                     "completion_pct": round(100 * done / len(tasks), 1) if tasks else None,
                     "attendance_days": len(att), "present_days": present})
    return {"department": dept, "employees": rows}


def _t_spreadsheet_insights(user: dict, args: dict) -> dict:
    from . import docs_api
    from . import spreadsheet as sheet
    if not has_permission(user["role"], "DOCUMENT_READ"):
        raise _deny(user, "spreadsheet_insights", "DOCUMENT_READ")
    doc = docs_api._get_owned(str(args.get("doc_id", "")), user)  # 404 for foreign ids
    if doc["kind"] not in ("csv", "xlsx"):
        raise ToolError("document is not a spreadsheet")
    with open(docs_api._stored_path(doc), "rb") as fh:
        data = fh.read()
    try:
        headers, rows = sheet.raw_rows(doc["kind"], data)
        agg = sheet.aggregate_rows(
            headers, rows,
            [str(c)[:80] for c in (args.get("revenue_cols") or [])[:20]],
            [str(c)[:80] for c in (args.get("expense_cols") or [])[:20]],
            str(args.get("group_by") or "")[:80])
    except sheet.SheetError as e:
        raise ToolError(f"spreadsheet unreadable: {e}")
    if agg["missing_columns"] or not rows:
        return {"doc_id": doc["id"], "filename": doc["filename"],
                "verdict": "insufficient data to calculate this metric",
                "missing_columns": agg["missing_columns"]}
    return {"doc_id": doc["id"], "filename": doc["filename"],
            "verdict": "calculated from data",
            "totals": agg["totals"], "groups": agg["groups"],
            "missing_columns": agg["missing_columns"]}


def _deny(user: dict, tool: str, perm: str) -> HTTPException:
    audit_log.append(user["id"], user["role"], "tool_denied",
                     resource=f"agent_tool:{tool}", decision="deny",
                     detail=f"role {user['role']} lacks {perm}")
    return HTTPException(403, f"role '{user['role']}' lacks permission '{perm}' for tool '{tool}'")


# ---------------- tool registry ----------------

TOOL_REGISTRY: dict[str, dict] = {
    "rag_search": {"desc": "Permission-filtered document retrieval", "perm": "DOCUMENT_READ",
                   "roles": None, "confirm": False, "fn": _t_rag_search,
                   "args": {"query": "string!", "document_ids": "list?", "top_k": "int?"},
                   "returns": "{hits[{doc_id,filename,page,chunk_index,score,excerpt}]}"},
    "analyze_document": {"desc": "Owned-document excerpt + status", "perm": "DOCUMENT_ANALYZE",
                         "roles": None, "confirm": False, "fn": _t_analyze_document,
                         "args": {"doc_id": "string!"}, "returns": "{excerpt,status}"},
    "get_image_info": {"desc": "Owned-image vision metadata", "perm": "DOCUMENT_READ",
                       "roles": None, "confirm": False, "fn": _t_get_image_info,
                       "args": {"doc_id": "string!"}, "returns": "{vision,proc_status}"},
    "analyze_authorized_image": {"desc": "Explicit local analysis of ONE owned image",
                                 "perm": "DOCUMENT_ANALYZE", "roles": None,
                                 "confirm": False, "fn": _t_analyze_authorized_image,
                                 "args": {"doc_id": "string!", "question": "string?"},
                                 "returns": "{dims,ocr_status,summary}"},
    "list_my_docs": {"desc": "List accessible documents", "perm": "DOCUMENT_READ",
                     "roles": None, "confirm": False, "fn": _t_list_my_docs,
                     "args": {"q": "string?"}, "returns": "{documents[]}"},
    "summarize": {"desc": "Summarize provided text", "perm": "AI_CHAT",
                  "roles": None, "confirm": False, "fn": _t_summarize,
                  "args": {"text": "string!"}, "returns": "{summary}"},
    "translate_text": {"desc": "Translate provided text (Step-19 engine)", "perm": "AI_CHAT",
                       "roles": None, "confirm": False, "fn": _t_translate_text,
                       "args": {"text": "string!", "target": "string!"},
                       "returns": "{text,target}"},
    "generate_report": {"desc": "Draft a report (SENSITIVE: needs confirmation)",
                        "perm": "REPORT_CREATE", "roles": None, "confirm": True,
                        "fn": _t_generate_report,
                        "args": {"title": "string!", "sections": "list!"},
                        "returns": "{title,markdown}"},
    "get_my_info": {"desc": "Own identity", "perm": None, "roles": None,
                    "confirm": False, "fn": _t_get_my_info, "args": {}, "returns": "{user}"},
    "list_users": {"desc": "List users (ADMIN workflows)", "perm": "USER_READ",
                   "roles": ["ADMIN", "security_admin"], "confirm": False, "fn": _t_list_users,
                   "args": {}, "returns": "{users[]}"},
    "get_audit_summary": {"desc": "Recent audit events", "perm": "AUDIT_READ",
                          "roles": ["ADMIN", "security_admin"], "confirm": False,
                          "fn": _t_get_audit_summary,
                          "args": {"limit": "int?"}, "returns": "{recent[]}"},
    "get_model_info": {"desc": "Model info", "perm": "MODEL_READ",
                       "roles": None, "confirm": False, "fn": _t_get_model_info,
                       "args": {}, "returns": "{info}"},
    "my_work_summary": {"desc": "Own tasks/attendance/evidence summary", "perm": "WORK_READ",
                        "roles": None, "confirm": False, "fn": _t_my_work_summary,
                        "args": {}, "returns": "{tasks_total,tasks_by_status,recent_tasks}"},
    "work_detail": {"desc": "One scoped work item (own or team)", "perm": "WORK_READ",
                    "roles": None, "confirm": False, "fn": _t_work_detail,
                    "args": {"work_id": "string!"},
                    "returns": "{id,title,instructions,status,progress}"},
    "team_performance": {"desc": "Department aggregates (managers)", "perm": "ANALYTICS_READ",
                         "roles": ["ADMIN", "MANAGER", "approving_manager"],
                         "confirm": False, "fn": _t_team_performance,
                         "args": {"department": "string?"},
                         "returns": "{department,employees[]}"},
    "spreadsheet_insights": {"desc": "Computed metrics over an owned spreadsheet",
                             "perm": "DOCUMENT_READ", "roles": None, "confirm": False,
                             "fn": _t_spreadsheet_insights,
                             "args": {"doc_id": "string!", "revenue_cols": "list?",
                                      "expense_cols": "list?", "group_by": "string?"},
                             "returns": "{verdict,totals,groups}"},
}


# ---------------- agent registry ----------------

AGENT_REGISTRY: dict[str, dict] = {
    "document_analysis": {
        "purpose": "Analyze owned documents via retrieval + excerpts + summary",
        "roles": ["ADMIN", "MANAGER", "OPERATOR", "REVIEWER", "USER",
                  "field_engineer", "process_engineer", "safety_inspector",
                  "approving_manager"],
        "tools": ["rag_search", "analyze_document", "get_image_info", "list_my_docs",
                  "analyze_authorized_image", "summarize", "translate_text"],
        "max_steps": 8, "timeout_s": 120, "max_tool_calls": 10, "enabled": True},
    "report_generation": {
        "purpose": "Draft reports from authorized sources (confirmation gated)",
        "roles": ["ADMIN", "MANAGER", "REVIEWER", "process_engineer",
                  "approving_manager"],
        "tools": ["rag_search", "analyze_document", "list_my_docs", "summarize",
                  "translate_text", "generate_report"],
        "max_steps": 8, "timeout_s": 180, "max_tool_calls": 10, "enabled": True},
    "review": {
        "purpose": "Review documents: gaps, summary, cross-checks",
        "roles": ["ADMIN", "MANAGER", "REVIEWER", "safety_inspector",
                  "approving_manager"],
        "tools": ["rag_search", "analyze_document", "get_image_info", "list_my_docs",
                  "analyze_authorized_image", "summarize", "translate_text"],
        "max_steps": 8, "timeout_s": 120, "max_tool_calls": 10, "enabled": True},
    "data_analysis": {
        "purpose": "Analyze structured (CSV/XLSX) content via extracted text",
        "roles": ["ADMIN", "MANAGER", "OPERATOR", "process_engineer"],
        "tools": ["rag_search", "analyze_document", "list_my_docs", "summarize",
                  "translate_text"],
        "max_steps": 6, "timeout_s": 120, "max_tool_calls": 8, "enabled": True},
    "admin_assistant": {
        "purpose": "Admin workflows: users, audit, models (ADMIN / security_admin)",
        "roles": ["ADMIN", "security_admin"],
        "tools": ["get_my_info", "list_users", "get_audit_summary", "get_model_info",
                  "summarize", "translate_text"],
        "max_steps": 6, "timeout_s": 60, "max_tool_calls": 8, "enabled": True},
    "work_assistant": {
        "purpose": "Work help: own records, scoped team data, spreadsheet math",
        "roles": ["ADMIN", "MANAGER", "OPERATOR", "REVIEWER", "USER", "EMPLOYEE",
                  "field_engineer", "process_engineer", "safety_inspector",
                  "approving_manager"],
        "tools": ["my_work_summary", "work_detail", "team_performance",
                  "spreadsheet_insights", "rag_search", "list_my_docs",
                  "summarize", "translate_text"],
        "max_steps": 8, "timeout_s": 120, "max_tool_calls": 10, "enabled": True},
}


def _default_plan(agent: str, goal: str, document_ids: list[str]) -> list[dict]:
    g = (goal or "")[:500]
    if agent == "report_generation":
        steps = [{"tool": "rag_search", "args": {"query": g}}]
        if document_ids:
            steps.append({"tool": "analyze_document", "args": {"doc_id": document_ids[0]}})
        steps.append({"tool": "summarize", "args": {"text": g}})
        steps.append({"tool": "generate_report",
                      "args": {"title": g[:80] or "Agent report", "sections": [g]}})
        return steps
    if agent == "admin_assistant":
        return [{"tool": "get_my_info", "args": {}},
                {"tool": "summarize", "args": {"text": g or "status overview"}}]
    if agent == "work_assistant":
        steps = [{"tool": "my_work_summary", "args": {}}]
        if document_ids:
            steps.append({"tool": "spreadsheet_insights",
                          "args": {"doc_id": document_ids[0]}})
        steps.append({"tool": "summarize", "args": {"text": g or "my work"}})
        return steps
    steps = []
    if document_ids:
        steps.append({"tool": "rag_search",
                      "args": {"query": g, "document_ids": document_ids}})
        steps.append({"tool": "analyze_document", "args": {"doc_id": document_ids[0]}})
    else:
        steps.append({"tool": "rag_search", "args": {"query": g}})
    steps.append({"tool": "summarize", "args": {"text": g}})
    return steps


def _planner_suggest(agent: str, goal: str) -> list[dict] | None:
    """Ask the LLM for a plan. Untrusted output: validated + allow-listed by caller."""
    from .ai import get_service
    prompt = (f"Agent '{agent}'. Goal: {goal[:500]}. Reply ONLY with JSON like "
              '{"steps": [{"tool": "<name>", "args": {}}]}.')
    try:
        raw = get_service().provider.generate([{"role": "user", "content": prompt}],
                                              system="You output JSON plans only.",
                                              role="planner")
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        steps = data.get("steps")
        if not isinstance(steps, list):
            return None
        return [{"tool": str(s.get("tool", "")), "args": s.get("args") or {}}
                for s in steps if isinstance(s, dict)]
    except Exception:
        return None  # malformed AI output -> default plan (safe failure)


# ---------------- execution engine ----------------

def exec_tool(user: dict, agent: str, tool: str, args: dict) -> dict:
    """Single permission-checked tool call. Used by the engine AND tests."""
    spec = TOOL_REGISTRY.get(tool)
    if spec is None:
        raise HTTPException(400, f"unknown tool '{tool}'")
    if tool not in AGENT_REGISTRY[agent]["tools"]:
        audit_log.append(user["id"], user["role"], "tool_denied",
                         resource=f"agent_tool:{tool}", decision="deny",
                         detail=f"agent '{agent}' forbids tool")
        raise HTTPException(403, f"agent '{agent}' may not use tool '{tool}'")
    if spec["roles"] and user["role"] not in spec["roles"]:
        audit_log.append(user["id"], user["role"], "tool_denied",
                         resource=f"agent_tool:{tool}", decision="deny",
                         detail="role not in tool allow-list")
        raise HTTPException(403, f"role '{user['role']}' may not use tool '{tool}'")
    audit_log.append(user["id"], user["role"], "tool_requested",
                     resource=f"agent_tool:{tool}")
    try:
        out = spec["fn"](user, args or {})
    except HTTPException as e:
        if e.status_code == 403:  # fn already audited its denial
            raise
        audit_log.append(user["id"], user["role"], "tool_denied",
                         resource=f"agent_tool:{tool}", decision="deny",
                         detail=str(e.detail)[:150])
        raise
    except ToolError as e:
        audit_log.append(user["id"], user["role"], "tool_denied",
                         resource=f"agent_tool:{tool}", decision="deny",
                         detail=str(e)[:150])
        raise HTTPException(502, f"tool '{tool}' failed: {e}")
    audit_log.append(user["id"], user["role"], "tool_allowed",
                     resource=f"agent_tool:{tool}")
    return out


def _finish(run: dict, user: dict, lang: str, state: str, note: str = "") -> dict:
    agent_store.save(run["id"], state=state)
    audit_log.append(user["id"], user["role"],
                     {"COMPLETED": "agent_completed", "FAILED": "agent_failed",
                      "TIMEOUT": "agent_timeout", "CANCELLED": "agent_cancelled"}[state],
                     resource=run["id"], decision="deny" if state in ("FAILED", "TIMEOUT") else "allow",
                     detail=note[:200])
    rec = agent_store.get(run["id"], user["id"], user["role"] == "ADMIN")
    return rec


def _summarize_run(user: dict, steps: list[dict], goal: str) -> tuple[str, str]:
    """Final summary via LLM with deterministic template fallback (malformed/
    missing provider output never fails the run by itself)."""
    facts = "; ".join(f"{s['tool']}={s.get('status')}" for s in steps)[:800]
    try:
        from .ai import get_service
        text = get_service().provider.generate(
            [{"role": "user", "content": f"Goal: {goal[:300]}. Step outcomes: {facts}. "
                                         "Write a 3-line grounded summary."}],
            system="Summarize tool outcomes only. Never invent.", role=user["role"])
        if not text or not text.strip():
            raise ValueError("empty provider output")
        return text[:2000], get_service().provider.name
    except Exception:
        return (f"Agent summary (template fallback): goal '{goal[:120]}' — "
                f"{len([s for s in steps if s.get('status') == 'ok'])}/{len(steps)} steps ok."), "none"


def _resume(run: dict, user: dict, cfg: dict, lang: str, t0: float,
            tool_calls: int) -> dict:
    steps = run["steps"]
    start_idx = run.get("_resume_from", len(steps))
    for idx in range(start_idx, len(steps)):
        if time.time() - t0 > cfg["timeout_s"]:
            return _finish(run, user, lang, "TIMEOUT", f"exceeded {cfg['timeout_s']}s")
        if idx >= cfg["max_steps"]:
            agent_store.save(run["id"], steps=steps)
            return _finish(run, user, lang, "FAILED", "maximum steps exceeded")
        step = steps[idx]
        spec = TOOL_REGISTRY[step["tool"]]
        if spec["confirm"] and not step.get("confirmed"):
            agent_store.save(run["id"], state="WAITING_CONFIRMATION",
                             pending=json.dumps({"step": idx, "tool": step["tool"]}),
                             steps=steps)
            audit_log.append(user["id"], user["role"], "confirmation_requested",
                             resource=run["id"], detail=f"tool={step['tool']}")
            rec = agent_store.get(run["id"], user["id"], user["role"] == "ADMIN")
            rec["state"] = "WAITING_CONFIRMATION"
            return rec
        tool_calls += 1
        if tool_calls > cfg["max_tool_calls"]:
            return _finish(run, user, lang, "FAILED", "tool-call limit exceeded")
        try:
            out = exec_tool(user, run["agent"], step["tool"], step.get("args") or {})
            step.update({"status": "ok", "result": str(out)[:800]})
            if step["tool"] == "rag_search":
                step["sources"] = [
                    {k: h.get(k) for k in ("doc_id", "filename", "page",
                                           "chunk_index", "score")}
                    for h in (out.get("hits") or [])[:4]]
        except HTTPException as e:
            step.update({"status": "denied" if e.status_code == 403 else "error",
                         "error": str(e.detail)[:200]})
            agent_store.save(run["id"], steps=steps)
            return _finish(run, user, lang, "FAILED",
                           f"step {idx} {step['tool']}: {e.detail}")
        agent_store.save(run["id"], steps=steps)
    summary, prov = _summarize_run(user, steps, run["goal"])
    if lang != "en":
        try:
            from .i18n import get_translation_provider, translate_answer
            summary, _ = translate_answer(summary, lang, get_translation_provider())
        except Exception:
            pass
    sources = [s for st in steps for s in (st.get("sources") or [])]
    if sources:
        summary = summary.rstrip() + "\n\nSources:\n" + "\n".join(
            f"* {s.get('filename')} — "
            f"{'Page ' + str(s.get('page')) + ' ' if s.get('page') else ''}(chunk {s.get('chunk_index')})"
            for s in sources)
    agent_store.save(run["id"], result=summary[:4000], pending="")
    audit_log.append(user["id"], user["role"], "doc_question", resource=run["id"],
                     detail=f"agent={run['agent']} sources={len(sources)} lang={lang}")
    return _finish(run, user, lang, "COMPLETED", f"steps={len(steps)} provider={prov}")


def start_run(user: dict, agent: str, goal: str, document_ids: list[str] | None,
              lang: str, mode: str, max_steps: int | None = None,
              timeout_s: float | None = None) -> dict:
    from . import rag as ragmod
    if not has_permission(user["role"], "AI_AGENT_USE"):
        audit_log.append(user["id"], user["role"], "tool_denied",
                         resource=f"agent:{agent}", decision="deny",
                         detail="lacks AI_AGENT_USE")
        raise HTTPException(403, "agent use not permitted for this role")
    spec = AGENT_REGISTRY.get(agent)
    from .sysconfig import agent_enabled
    if spec is None or not agent_enabled(agent, spec.get("enabled", True)):
        raise HTTPException(404, "unknown or disabled agent")
    if user["role"] not in spec["roles"]:
        audit_log.append(user["id"], user["role"], "tool_denied",
                         resource=f"agent:{agent}", decision="deny",
                         detail="role not allowed for agent")
        raise HTTPException(403, f"role '{user['role']}' may not run agent '{agent}'")
    ragmod.verify_doc_access(document_ids or [], user)  # fail-closed BEFORE work
    cfg = {"max_steps": max(1, min(max_steps or spec["max_steps"], 20)),
           "timeout_s": max(0.0, min(timeout_s if timeout_s is not None else spec["timeout_s"], 600.0)),
           "max_tool_calls": spec["max_tool_calls"]}
    run = agent_store.create(agent, user["id"], user["role"], goal or "", lang)
    audit_log.append(user["id"], user["role"], "agent_started", resource=run["id"],
                     detail=f"agent={agent} docs={','.join(document_ids or []) or '-'} lang={lang}")
    suggested = _planner_suggest(agent, goal or "")
    steps, note = [], ""
    if suggested:
        for s in suggested[:20]:
            if s["tool"] in spec["tools"]:
                steps.append({"tool": s["tool"], "args": s["args"]})
        if not steps:
            note = "planner suggested no allowed tools; "
    if not steps:
        note += "default plan"
        steps = _default_plan(agent, goal or "", document_ids or [])[:20]
    run["steps"] = steps
    run["_resume_from"] = 0
    agent_store.save(run["id"], state="RUNNING", steps=steps)
    if note:
        audit_log.append(user["id"], user["role"], "agent_started", resource=run["id"],
                         detail=f"plan: {note}")
    return _resume(run, user, cfg, lang, time.time(), 0)


def confirm_run(user: dict, run_id: str, approve: bool) -> dict:
    run = agent_store.get(run_id, user["id"], user["role"] == "ADMIN")
    if run is None:
        raise HTTPException(404, "agent run not found")
    if run["state"] != "WAITING_CONFIRMATION":
        raise HTTPException(409, f"run is {run['state']}, nothing to confirm")
    try:
        pending = json.loads(run.get("pending") or "{}")
    except Exception:
        pending = {}
    audit_log.append(user["id"], user["role"], "confirmation_received", resource=run_id,
                     decision="allow" if approve else "deny",
                     detail=f"tool={pending.get('tool')} approve={approve}")
    if not approve:
        return _finish(run, user, run["lang"], "CANCELLED", "confirmation denied")
    steps = run["steps"]
    steps[pending.get("step", 0)]["confirmed"] = True
    agent_store.save(run_id, steps=steps, pending="", state="RUNNING")
    run = agent_store.get(run_id, user["id"], user["role"] == "ADMIN")
    run["_resume_from"] = pending.get("step", 0)
    spec = AGENT_REGISTRY[run["agent"]]
    return _resume(run, user, {"max_steps": spec["max_steps"], "timeout_s": spec["timeout_s"],
                               "max_tool_calls": spec["max_tool_calls"]},
                   run["lang"], time.time(), sum(1 for s in steps if s.get("status") == "ok"))


def cancel_run(user: dict, run_id: str) -> dict:
    run = agent_store.get(run_id, user["id"], user["role"] == "ADMIN")
    if run is None:
        raise HTTPException(404, "agent run not found")
    if run["state"] in TERMINAL:
        raise HTTPException(409, f"run already {run['state']}")
    return _finish(run, user, run["lang"], "CANCELLED", "user cancelled")


def describe_registry(role: str) -> list[dict]:
    from .sysconfig import agent_enabled
    return [{"name": n, "purpose": s["purpose"], "allowed": role in s["roles"],
             "tools": s["tools"], "limits": {"max_steps": s["max_steps"],
                                             "timeout_s": s["timeout_s"],
                                             "max_tool_calls": s["max_tool_calls"]},
             "enabled": agent_enabled(n, s["enabled"])}
            for n, s in AGENT_REGISTRY.items()]
