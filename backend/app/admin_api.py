"""Step 22 ADMIN system controls (mounted under /api/v1/admin).

ADMIN + security_admin only: every endpoint requires one of those roles
server-side (require_role), checked before any logic runs. Frontend buttons
are convenience only.

Secret hygiene: responses are built EXCLUSIVELY from the allowlisted
sysconfig schema (non-secret by construction) plus provider health labels.
The final payload is scrubbed once more against secret patterns. Audit
details carry key names and counts — never values that could be secrets.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import audit_log
from . import sysconfig
from .auth_api import require_role

router = APIRouter(prefix="/api/v1/admin", tags=["step22-admin"])
admin = require_role("ADMIN", "security_admin")


def _safe(fn, fallback="unavailable"):
    try:
        return fn()
    except Exception as e:
        return {"ok": False, "detail": f"{fallback}: {str(e)[:120]}"}


@router.get("/overview")
def overview(user: dict = Depends(admin)):
    from . import monitor
    from .ai import get_service
    from .agents import describe_registry
    from .ai.tools import TOOLS as CHAT_TOOLS
    from .agents import TOOL_REGISTRY as AGENT_TOOLS
    from .embeddings import get_embedding_provider
    from .i18n import get_translation_provider
    from .vectorstore import get_vector_store
    from .voice import get_stt_provider, get_tts_provider

    svc = get_service()
    ai_health = _safe(svc.provider.health)
    emb = get_embedding_provider()
    vs = get_vector_store()
    rag_cfg = {"top_k": __import__("os").getenv("SOV_RAG_TOP_K", "4"),
               "min_score": __import__("os").getenv("SOV_RAG_MIN_SCORE", "0.15")}

    def chroma():
        from .kb import kb
        n = kb._chroma.count() if kb._chroma is not None else 0
        return {"backend": kb.chroma_backend, "seed_chunks": len(kb.chunks),
                "indexed": n, "ok": True}

    payload = {
        "ai": {"provider": sysconfig.effective("SOV_AI_PROVIDER") or "auto",
               "health": ai_health if isinstance(ai_health, dict) else {"ok": False}},
        "llm": {"model": sysconfig.effective("OLLAMA_MODEL") or sysconfig.effective("SOV_OLLAMA_MODEL"),
                "base_url": sysconfig.effective("OLLAMA_BASE_URL") or "default",
                "tier": _safe(lambda: __import__("backend.app.router", fromlist=["x"]).MODEL_TIER)},
        "embeddings": {"provider": emb.name, "dim": emb.dim,
                       "configured": sysconfig.effective("SOV_EMBED_PROVIDER") or "mock",
                       "model": sysconfig.effective("SOV_EMBED_MODEL") or ""},
        "rag": {"vector_backend": vs.health().get("backend"), **rag_cfg},
        "chroma": _safe(chroma),
        "translate": {"provider": get_translation_provider().name,
                      "available": get_translation_provider().available()},
        "stt": {"provider": get_stt_provider().name,
                "available": get_stt_provider().available()},
        "tts": {"provider": get_tts_provider().name,
                "available": get_tts_provider().available()},
        "agents": describe_registry("ADMIN"),
        "tools": {"chat_tools": len(CHAT_TOOLS), "agent_tools": len(AGENT_TOOLS),
                  "confirm_gated": sorted(n for n, s in AGENT_TOOLS.items() if s["confirm"])},
        "features": {f: sysconfig.feature_enabled(f) for f in ("voice", "agents", "translate")},
        "system": _safe(monitor.snapshot),
    }
    return json.loads(sysconfig.scrub_text(json.dumps(payload)))


@router.get("/config")
def get_config(user: dict = Depends(admin)):
    audit_log.append(user["id"], user["role"], "config_viewed", resource="sysconfig")
    return {"config": sysconfig.describe()}


class ConfigSet(BaseModel):
    key: str
    value: str | int | float | bool


@router.put("/config")
def set_config(b: ConfigSet, user: dict = Depends(admin)):
    from . import ratelimit
    ratelimit.check("admin_config", user["id"], user)
    try:
        norm = sysconfig.set_override(b.key, b.value)
    except sysconfig.ConfigError as e:
        audit_log.append(user["id"], user["role"], "config_validation_failed",
                         resource=b.key, decision="deny", detail=str(e)[:150])
        raise HTTPException(422, str(e))
    audit_log.append(user["id"], user["role"], "config_changed", resource=b.key,
                     detail=f"value={norm} source=override")
    lk = b.key
    if "PROVIDER" in lk or lk in ("OLLAMA_MODEL", "SOV_EMBED_MODEL", "OLLAMA_BASE_URL"):
        audit_log.append(user["id"], user["role"],
                         "provider_changed" if "PROVIDER" in lk or "BASE_URL" in lk else "model_changed",
                         resource=lk, detail=f"value={norm}")
    return {"key": b.key, "value": norm, "source": "override"}


class ConfigReset(BaseModel):
    key: str


@router.post("/config/reset")
def reset_config(b: ConfigReset, user: dict = Depends(admin)):
    try:
        existed = sysconfig.clear_override(b.key)
    except sysconfig.ConfigError as e:
        raise HTTPException(400, str(e))
    audit_log.append(user["id"], user["role"], "config_changed", resource=b.key,
                     detail="override cleared; back to env/default")
    return {"key": b.key, "cleared": existed,
            "value": sysconfig.effective(b.key), "source": sysconfig.source_of(b.key)}


class AgentToggle(BaseModel):
    enabled: bool


@router.put("/agents/{name}")
def toggle_agent(name: str, b: AgentToggle, user: dict = Depends(admin)):
    from .agents import AGENT_REGISTRY
    from . import ratelimit
    ratelimit.check("admin_config", user["id"], user)
    if name not in AGENT_REGISTRY:
        raise HTTPException(404, "unknown agent")
    sysconfig.set_override(f"AGENT_{name.upper()}_ENABLED", b.enabled)
    audit_log.append(user["id"], user["role"],
                     "agent_enabled" if b.enabled else "agent_disabled",
                     resource=name)
    return {"agent": name, "enabled": b.enabled}


class FeatureToggle(BaseModel):
    enabled: bool


@router.put("/features/{name}")
def toggle_feature(name: str, b: FeatureToggle, user: dict = Depends(admin)):
    from . import ratelimit
    ratelimit.check("admin_config", user["id"], user)
    if name not in ("voice", "agents", "translate"):
        raise HTTPException(404, "unknown feature")
    sysconfig.set_override(f"SOV_FEATURE_{name.upper()}", b.enabled)
    audit_log.append(user["id"], user["role"],
                     "feature_enabled" if b.enabled else "feature_disabled",
                     resource=name)
    return {"feature": name, "enabled": b.enabled}
