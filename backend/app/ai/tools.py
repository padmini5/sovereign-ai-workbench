"""Backend-verified AI tools (Phase 2).

The LLM NEVER enforces security: every tool call is permission-checked
here, server-side, against rbac.has_permission() for the authenticated
user's role. Denials raise ToolDenied (HTTP 403) and are audit-logged.
Tool outputs are sanitized (no password hashes, no secrets).
"""
from __future__ import annotations

from fastapi import HTTPException

from .. import audit_log
from ..rbac import has_permission


class ToolDenied(HTTPException):
    def __init__(self, role: str, tool: str, perm: str):
        super().__init__(403, f"role '{role}' lacks permission '{perm}' for tool '{tool}'")


def _deny(user: dict, tool: str, perm: str) -> ToolDenied:
    audit_log.append(user["id"], user["role"], "permission_denied",
                     resource=f"ai_tool:{tool}", decision="deny",
                     detail=f"role {user['role']} lacks {perm}")
    return ToolDenied(user["role"], tool, perm)


def tool_get_my_info(user: dict, args: dict) -> dict:
    """Own identity + permissions. Requires only authentication."""
    from ..rbac import role_permissions
    return {"id": user["id"], "username": user["username"], "role": user["role"],
            "permissions": sorted(role_permissions(user["role"]))}


def tool_list_users(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "USER_READ"):
        raise _deny(user, "list_users", "USER_READ")
    from .. import userstore
    return {"users": userstore.list_users()}  # public fields only, no hashes


def tool_get_system_info(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "SYSTEM_CONFIGURE"):
        raise _deny(user, "get_system_info", "SYSTEM_CONFIGURE")
    from .. import config as cfg
    return {"app": "SovereignAI Workbench", "version": cfg.APP_VERSION, "env": cfg.ENV}


def tool_list_reports(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "REPORT_READ"):
        raise _deny(user, "list_reports", "REPORT_READ")
    from .. import work as store
    # Scoped exactly like GET /api/v1/reports: own + manager-scope + ADMIN.
    rows = store.list_reports()
    out = []
    for r in rows:
        if user["role"] == "ADMIN" or r["created_by"] == user["id"]:
            out.append({"id": r["id"], "title": r["title"],
                        "report_type": r["report_type"]})
            continue
        if user["role"] in ("MANAGER", "approving_manager") \
                and r["scope_type"] in ("team", "department"):
            from .. import userstore
            rec = userstore.find_by_id(user["id"])
            dept = (rec.get("department") or "") if rec else ""
            if r["scope_id"] in (dept, "", "team"):
                out.append({"id": r["id"], "title": r["title"],
                            "report_type": r["report_type"]})
    return {"reports": out[:20], "count": len(out)}


def tool_my_work_summary(user: dict, args: dict) -> dict:
    """Own work summary inside chat (same scope as the work_assistant tool)."""
    if not has_permission(user["role"], "WORK_READ"):
        raise _deny(user, "my_work_summary", "WORK_READ")
    from ..agents import _t_my_work_summary
    return _t_my_work_summary(user, {})


def tool_get_audit_summary(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "AUDIT_READ"):
        raise _deny(user, "get_audit_summary", "AUDIT_READ")
    rows = audit_log.rows(limit=int(args.get("limit", 20)))
    return {"recent": rows, "count": len(rows)}


def tool_get_model_info(user: dict, args: dict) -> dict:
    if not has_permission(user["role"], "MODEL_READ"):
        raise _deny(user, "get_model_info", "MODEL_READ")
    return {"provider_note": "resolved live by the API layer (see /ai/status)"}


TOOLS: dict[str, dict] = {
    "get_my_info": {"perm": None, "fn": tool_get_my_info,
                    "desc": "Own identity and permissions"},
    "list_users": {"perm": "USER_READ", "fn": tool_list_users,
                   "desc": "List users (public fields only)"},
    "get_system_info": {"perm": "SYSTEM_CONFIGURE", "fn": tool_get_system_info,
                        "desc": "System/version info"},
    "list_reports": {"perm": "REPORT_READ", "fn": tool_list_reports,
                     "desc": "List reports (scoped: own, team, admin)"},
    "my_work_summary": {"perm": "WORK_READ", "fn": tool_my_work_summary,
                        "desc": "Own tasks/attendance/evidence summary"},
    "get_audit_summary": {"perm": "AUDIT_READ", "fn": tool_get_audit_summary,
                          "desc": "Recent audit events"},
    "get_model_info": {"perm": "MODEL_READ", "fn": tool_get_model_info,
                       "desc": "Model/provider info"},
}


def visible_tools(role: str) -> list[dict]:
    """Tool affordances for this role (UI hint only — execution re-checks)."""
    return [{"name": n, "desc": t["desc"]} for n, t in TOOLS.items()
            if t["perm"] is None or has_permission(role, t["perm"])]


def run_tool(name: str, user: dict, args: dict | None = None) -> dict:
    spec = TOOLS.get(name)
    if spec is None:
        raise HTTPException(400, f"unknown tool '{name}'")
    return spec["fn"](user, args or {})
