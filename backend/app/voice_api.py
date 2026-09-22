"""Step 20 voice API (mounted under /api/v1/voice).

  POST /voice/stt    audio -> transcript (AI_CHAT)
  POST /voice/tts    text  -> spoken audio (AI_CHAT)
  POST /voice/chat   audio -> STT -> chat_respond -> TTS (AI_CHAT)
  GET  /voice/status provider availability + languages (auth)

Voice chat calls ai_api.chat_respond verbatim — the SAME auth, RBAC,
document verification, permission-filtered retrieval, citations,
translation, storage, and audit as typed chat. No parallel path exists.
Audit carries metadata only (byte/char counts, langs, providers).
"""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import audit_log
from .ai_api import ChatIn, ChatMessage, chat_respond
from .auth_api import get_current_user, require_perm
from .i18n import SUPPORTED
from .voice import (MAX_TTS_CHARS, STTError, TTSError, get_stt_provider,
                    get_tts_provider, max_audio_bytes, sniff_audio)

router = APIRouter(prefix="/api/v1/voice", tags=["step20-voice"])


def _check_lang(lang: str) -> str:
    if lang not in SUPPORTED:
        raise HTTPException(422, f"unsupported language '{lang}'")
    return lang


async def _read_audio(f: UploadFile) -> bytes:
    limit = max_audio_bytes()
    chunks, total = [], 0
    while True:
        c = await f.read(512 * 1024)
        if not c:
            break
        total += len(c)
        if total > limit:
            raise HTTPException(413, f"audio exceeds {limit // (1024 * 1024)} MB limit")
        chunks.append(c)
    if total < 44:  # smaller than any valid header
        raise HTTPException(400, "empty or truncated audio")
    return b"".join(chunks)


def _parse_doc_ids(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        ids = json.loads(raw)
    except Exception:
        raise HTTPException(422, "document_ids must be a JSON array")
    if not isinstance(ids, list) or len(ids) > 20:
        raise HTTPException(422, "document_ids must be a list of <=20 ids")
    return [str(i) for i in ids]


@router.post("/stt")
async def stt(f: UploadFile, lang: str = Form("en"),
              user: dict = Depends(require_perm("AI_CHAT"))):
    from . import ratelimit
    from .sysconfig import ensure_feature
    ensure_feature("voice", user)
    ratelimit.check("voice", user["id"], user)
    _check_lang(lang)
    data = await _read_audio(f)
    try:
        fmt = sniff_audio(data)
    except STTError as e:
        raise HTTPException(415, str(e))
    try:
        out = get_stt_provider().transcribe(data, fmt, lang)
    except STTError as e:
        audit_log.append(user["id"], user["role"], "voice_error", resource="stt",
                         decision="deny", detail=str(e)[:150])
        raise HTTPException(502, str(e))
    audit_log.append(user["id"], user["role"], "voice_stt", resource="stt",
                     detail=f"bytes={len(data)} fmt={fmt} lang={lang} "
                            f"out_chars={len(out['text'])}")
    return {**out, "provider": get_stt_provider().name}


class TTSIn(BaseModel):
    text: str
    lang: str = "en"


@router.post("/tts")
def tts(b: TTSIn, user: dict = Depends(require_perm("AI_CHAT"))):
    from . import ratelimit
    from .sysconfig import ensure_feature
    ensure_feature("voice", user)
    ratelimit.check("voice", user["id"], user)
    _check_lang(b.lang)
    if not b.text or not b.text.strip():
        raise HTTPException(422, "text must be non-empty")
    if len(b.text) > MAX_TTS_CHARS:
        raise HTTPException(422, f"text exceeds {MAX_TTS_CHARS} chars")
    try:
        out = get_tts_provider().synthesize(b.text, b.lang)
    except TTSError as e:
        audit_log.append(user["id"], user["role"], "voice_error", resource="tts",
                         decision="deny", detail=str(e)[:150])
        raise HTTPException(502, str(e))
    audit_log.append(user["id"], user["role"], "voice_tts", resource="tts",
                     detail=f"chars={len(b.text)} lang={b.lang} bytes={len(out['audio'])}")
    return Response(content=out["audio"], media_type=out["mime"],
                    headers={"X-Voice-Engine": out["engine"], "X-Voice-Lang": b.lang})


@router.post("/chat")
async def voice_chat(f: UploadFile, lang: str = Form("en"), mode: str = Form("general"),
                     conversation_id: str | None = Form(None),
                     document_ids: str | None = Form(None),
                     user: dict = Depends(require_perm("AI_CHAT"))):
    from . import ratelimit
    from .sysconfig import ensure_feature
    ensure_feature("voice", user)
    ratelimit.check("voice", user["id"], user)
    _check_lang(lang)
    if mode not in ("general", "my_docs"):
        raise HTTPException(422, "mode must be general|my_docs")
    data = await _read_audio(f)
    try:
        fmt = sniff_audio(data)
    except STTError as e:
        raise HTTPException(415, str(e))
    try:
        heard = get_stt_provider().transcribe(data, fmt, lang)
    except STTError as e:
        audit_log.append(user["id"], user["role"], "voice_error", resource="voice_chat",
                         decision="deny", detail=str(e)[:150])
        raise HTTPException(502, f"transcription failed: {e}")
    body = ChatIn(messages=[ChatMessage(role="user", content=heard["text"][:4000])],
                  conversation_id=conversation_id, lang=lang, mode=mode,
                  document_ids=_parse_doc_ids(document_ids))
    res = chat_respond(user, body)  # identical enforcement to typed chat
    spoken, _sep, _tail = res["message"]["content"].partition("\n\nSources:\n")
    audio_b64, audio_mime, audio_error = None, None, None
    try:
        out = get_tts_provider().synthesize(spoken[:MAX_TTS_CHARS], res.get("lang", lang))
        audio_b64, audio_mime = base64.b64encode(out["audio"]).decode(), out["mime"]
    except TTSError as e:  # graceful: chat answer stands, speech omitted
        audio_error = str(e)[:150]
        audit_log.append(user["id"], user["role"], "voice_error", resource="voice_tts",
                         decision="deny", detail=audio_error)
    audit_log.append(user["id"], user["role"], "voice_chat", resource=res["conversation_id"],
                     detail=f"lang={res.get('lang')} audio={'yes' if audio_b64 else 'no'} "
                            f"stt_chars={len(heard['text'])}")
    return {"transcript": heard["text"], "stt_confidence": heard.get("confidence", 0.0),
            **res, "audio_base64": audio_b64, "audio_mime": audio_mime,
            "audio_error": audio_error}


@router.get("/status")
def status(user: dict = Depends(get_current_user)):
    from .i18n import LANGS
    stt, tts = get_stt_provider(), get_tts_provider()
    return {"stt": {"provider": stt.name, "available": stt.available()},
            "tts": {"provider": tts.name, "available": tts.available()},
            "languages": [{"code": c, "label": l} for c, l in LANGS],
            "remote_configured": {
                "stt": bool(__import__("os").getenv("SOV_STT_REMOTE_URL", "")),
                "tts": bool(__import__("os").getenv("SOV_TTS_REMOTE_URL", ""))}}
