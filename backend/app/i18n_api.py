"""Step 19 i18n info API (mounted under /api/v1/i18n)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import audit_log
from .auth_api import get_current_user, require_perm
from .i18n import LANGS, TranslationError, get_translation_provider

router = APIRouter(prefix="/api/v1/i18n", tags=["step19-i18n"])


@router.get("/languages")
def languages(user: dict = Depends(get_current_user)):
    from . import userstore
    tr = get_translation_provider()
    return {"default": "en", "current": userstore.get_lang(user["id"]),
            "languages": [{"code": c, "label": l} for c, l in LANGS],
            "engine": {"provider": tr.name, "available": tr.available(),
                       "remote_configured": bool(__import__("os").getenv("SOV_TRANSLATE_REMOTE_URL", ""))}}


class TranslateIn(BaseModel):
    text: str
    target: str = "hi"


@router.post("/translate")
def translate_text(b: TranslateIn, user: dict = Depends(require_perm("AI_CHAT"))):
    """Translate short UI/answer text (NOT a document pipeline).

    RBAC note: this endpoint translates caller-supplied text only. Document
    content reaches translation solely through /ai/chat, after ownership
    verification + permission-filtered retrieval.
    """
    from .sysconfig import ensure_feature
    ensure_feature("translate", user)
    from .i18n import SUPPORTED
    if b.target not in SUPPORTED:
        from fastapi import HTTPException
        raise HTTPException(422, f"unsupported language '{b.target}'")
    if not b.text or not b.text.strip():
        from fastapi import HTTPException
        raise HTTPException(422, "text must be non-empty")
    if len(b.text) > 5000:
        from fastapi import HTTPException
        raise HTTPException(422, "text exceeds 5000 chars")
    tr = get_translation_provider()
    try:
        out = tr.translate(b.text[:5000], b.target)
    except TranslationError as e:
        from fastapi import HTTPException
        raise HTTPException(502, str(e))
    audit_log.append(user["id"], user["role"], "translate", resource=b.target,
                     detail=f"provider={tr.name} in_chars={len(b.text)}")
    return {"text": out, "target": b.target, "provider": tr.name}
