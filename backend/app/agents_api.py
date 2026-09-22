"""Step 21 agents API (mounted under /api/v1/agents).

  GET  /agents                 registry (role-filtered `allowed` flags)
  GET  /agents/tools           tool registry (perm-filtered affordances)
  POST /agents/{name}/run      start execution (AI_AGENT_USE + agent roles)
  GET  /agents/runs            own history (ADMIN: all)
  GET  /agents/runs/{id}       run detail (owner or ADMIN)
  POST /agents/runs/{id}/confirm {approve}  resolve WAITING_CONFIRMATION
  POST /agents/runs/{id}/cancel cancel a live run

All enforcement lives in agents.py; this layer only validates shapes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from . import agent_store, agents
from .auth_api import get_current_user, require_perm

router = APIRouter(prefix="/api/v1/agents", tags=["step21-agents"])


class RunIn(BaseModel):
    goal: str = ""
    document_ids: list[str] | None = None
    lang: str | None = None
    mode: str = "general"
    max_steps: int | None = None
    timeout_s: float | None = None

    @field_validator("goal")
    @classmethod
    def _goal(cls, v: str) -> str:
        if len(v) > 2000:
            raise ValueError("goal exceeds 2000 chars")
        return v

    @field_validator("document_ids")
    @classmethod
    def _docs(cls, v: list | None) -> list | None:
        if v is not None and len(v) > 20:
            raise ValueError("at most 20 document_ids")
        return v

    @field_validator("lang")
    @classmethod
    def _lang(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from .i18n import SUPPORTED
        if v not in SUPPORTED:
            raise ValueError(f"unsupported language '{v}'")
        return v

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("general", "my_docs"):
            raise ValueError("mode must be general|my_docs")
        return v

    @field_validator("max_steps")
    @classmethod
    def _steps(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 20:
            raise ValueError("max_steps must be 1..20")
        return v

    @field_validator("timeout_s")
    @classmethod
    def _timeout(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 600:
            raise ValueError("timeout_s must be 0..600")
        return v


class ConfirmIn(BaseModel):
    approve: bool


@router.get("")
def registry(user: dict = Depends(require_perm("AI_AGENT_USE"))):
    return {"agents": agents.describe_registry(user["role"])}


@router.get("/tools")
def tool_registry(user: dict = Depends(require_perm("AI_AGENT_USE"))):
    from .rbac import has_permission
    out = []
    for name, spec in agents.TOOL_REGISTRY.items():
        if spec["perm"] and not has_permission(user["role"], spec["perm"]):
            continue
        if spec["roles"] and user["role"] not in spec["roles"]:
            continue
        out.append({"name": name, "desc": spec["desc"], "confirm": spec["confirm"],
                    "args": spec["args"], "returns": spec["returns"]})
    return {"tools": out}


@router.post("/{name}/run")
def run_agent(name: str, b: RunIn, user: dict = Depends(require_perm("AI_CHAT"))):
    from . import ratelimit, userstore
    from .i18n import DEFAULT, SUPPORTED
    from .sysconfig import ensure_feature
    ensure_feature("agents", user)
    ratelimit.check("agent_run", user["id"], user)
    lang = b.lang or userstore.get_lang(user["id"])
    if lang not in SUPPORTED:
        lang = DEFAULT
    rec = agents.start_run(user, name, b.goal, b.document_ids, lang, b.mode,
                           max_steps=b.max_steps, timeout_s=b.timeout_s)
    code = 202 if rec["state"] == "WAITING_CONFIRMATION" else 200
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=code, content=rec)


@router.get("/runs")
def runs(user: dict = Depends(require_perm("AI_AGENT_USE"))):
    return {"runs": agent_store.list_for(None if user["role"] == "ADMIN" else user["id"])}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, user: dict = Depends(require_perm("AI_AGENT_USE"))):
    rec = agent_store.get(run_id, user["id"], user["role"] == "ADMIN")
    if rec is None:
        raise HTTPException(404, "agent run not found")
    return rec


@router.post("/runs/{run_id}/confirm")
def run_confirm(run_id: str, b: ConfirmIn,
                user: dict = Depends(require_perm("AI_AGENT_USE"))):
    from .sysconfig import ensure_feature
    ensure_feature("agents", user)
    return agents.confirm_run(user, run_id, b.approve)


@router.post("/runs/{run_id}/cancel")
def run_cancel(run_id: str, user: dict = Depends(require_perm("AI_AGENT_USE"))):
    from .sysconfig import ensure_feature
    ensure_feature("agents", user)
    return agents.cancel_run(user, run_id)
