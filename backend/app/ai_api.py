"""Phase 2 chat API (mounted under /api/v1/ai).

  POST /ai/chat               role-aware chat (perm AI_CHAT), persists turn
  POST /ai/chat/stream        same, streamed as SSE
  GET  /ai/conversations      own conversations
  GET  /ai/conversations/{id} own conversation + messages
  DELETE /ai/conversations/{id}
  GET  /ai/status              provider/model/reachability (no secrets)
  GET  /ai/tools               tools visible to caller (execution re-checks)

Audit: ai_chat / ai_chat_denied / ai_provider_error events carry metadata
only (counts, lengths, provider, model, latency, tools) — never prompt or
message content.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from . import audit_log
from . import conversations as store
from .ai import AIError, ProviderUnavailable, get_service
from .ai.tools import TOOLS, visible_tools
from .auth_api import get_current_user, require_perm

router = APIRouter(prefix="/api/v1/ai", tags=["phase2-ai"])

MAX_MESSAGES, MAX_MSG_CHARS, MAX_TOTAL_CHARS = 50, 4000, 20000
VALID_ROLES = ("user", "assistant")


class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError("role must be user|assistant")
        return v

    @field_validator("content")
    @classmethod
    def _content(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content must be non-empty")
        if len(v) > MAX_MSG_CHARS:
            raise ValueError(f"content exceeds {MAX_MSG_CHARS} chars")
        return v


class ChatIn(BaseModel):
    messages: list[ChatMessage]
    conversation_id: str | None = None
    tools: list[str] | None = None
    lang: str | None = None  # None -> stored user preference -> 'en'
    document_ids: list[str] | None = None  # RAG scope: verified before retrieval
    image_ids: list[str] | None = None  # explicit authorized image refs ONLY
    mode: str = "general"  # general | my_docs

    @field_validator("messages")
    @classmethod
    def _msgs(cls, v: list) -> list:
        if not v or len(v) > MAX_MESSAGES:
            raise ValueError(f"messages must hold 1..{MAX_MESSAGES} items")
        if sum(len(m.content) for m in v) > MAX_TOTAL_CHARS:
            raise ValueError(f"messages exceed {MAX_TOTAL_CHARS} chars total")
        if v[-1].role != "user":
            raise ValueError("last message must be from user")
        return v

    @field_validator("tools")
    @classmethod
    def _tools(cls, v: list | None) -> list | None:
        if v is None:
            return v
        unknown = [t for t in v if t not in TOOLS]
        if unknown:
            raise ValueError(f"unknown tools: {unknown}")
        return v

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("general", "my_docs"):
            raise ValueError("mode must be general|my_docs")
        return v

    @field_validator("document_ids")
    @classmethod
    def _docs(cls, v: list | None) -> list | None:
        if v is not None and len(v) > 20:
            raise ValueError("at most 20 document_ids")
        return v

    @field_validator("image_ids")
    @classmethod
    def _imgs(cls, v: list | None) -> list | None:
        if v is not None and len(v) > 3:
            raise ValueError("at most 3 image_ids")
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


def _audit_meta(user: dict, n_in: int, tools: list[str] | None, lang: str) -> dict:
    return {"n_in": n_in, "tools": tools or [], "lang": lang}


def _resolve_lang(user: dict, b: ChatIn) -> str:
    """Request lang wins; else stored preference; else English. Validated."""
    from . import userstore
    from .i18n import DEFAULT, SUPPORTED
    if b.lang and b.lang in SUPPORTED:
        return b.lang
    pref = userstore.get_lang(user["id"])
    return pref if pref in SUPPORTED else DEFAULT


def _cite(page: int | None, idx: int) -> str:
    return f"Page {page} (chunk {idx})" if page else f"chunk {idx}"


def _prepare_rag(user: dict, b: ChatIn) -> tuple[str, list[dict]]:
    """Verify doc access, retrieve permission-filtered chunks.

    Returns (extra_context, sources). Empty hits -> ("", []) and the caller
    answers NOT_FOUND without invoking the provider (no invention possible).
    Raises HTTPException (403 denied docs, 502 backend failures).
    """
    from . import rag as ragmod
    from .embeddings import EmbeddingError
    from .vectorstore import VectorError
    want = bool(b.document_ids) or b.mode == "my_docs"
    if not want:
        return "", []
    ragmod.verify_doc_access(b.document_ids or [], user)  # fail-closed 403
    try:
        hits, _ = ragmod.retrieve(user, b.messages[-1].content, b.document_ids)
    except (EmbeddingError, VectorError) as e:
        audit_log.append(user["id"], user["role"], "ai_provider_error",
                         resource="rag", decision="deny", detail=str(e)[:200])
        raise HTTPException(502, f"document retrieval unavailable: {e}")
    if not hits:
        return "", []
    ctx = "\n".join(f"[doc:{h['filename']} {_cite(h['page'], h['chunk_index'])}] "
                    f"{h['text'][:800]}" for h in hits)
    sources = [{"doc_id": h["doc_id"], "filename": h["filename"], "page": h["page"],
                "chunk_index": h["chunk_index"], "score": h["score"]} for h in hits]
    return ctx, sources


def _prepare_images(user: dict, b: ChatIn) -> str:
    """Explicit authorized image context for the chatbot.

    Every id is resolved as: exists? (404 "Resource unavailable" if not —
    no existence oracle) -> owned-or-ADMIN + DOCUMENT_ANALYZE? (403 "I don't
    have permission to access that image." if not). Only then does the local
    pipeline see the bytes. OCR text is labeled UNTRUSTED, never persisted,
    never indexed. The chatbot receives NO image collection — only these ids.
    """
    from . import docs_store as _store
    from . import private_images
    from .rbac import has_permission
    ids = b.image_ids or []
    if not ids:
        return ""
    if not has_permission(user["role"], "DOCUMENT_ANALYZE"):
        audit_log.append(user["id"], user["role"], "permission_denied",
                         resource="chat_image", decision="deny")
        raise HTTPException(403, "I don't have permission to access that image.")
    blocks = []
    for did in ids:
        doc = _store.get(did)
        if doc is None or doc.get("kind") != "image":
            raise HTTPException(404, "Resource unavailable.")
        if doc["owner_id"] != user["id"] and user["role"] != "ADMIN":
            audit_log.append(user["id"], user["role"], "permission_denied",
                             resource=f"chat_image:{did}", decision="deny")
            raise HTTPException(403, "I don't have permission to access that image.")
        res = private_images.analyze_private_image(
            user, did, b.messages[-1].content[:500])
        if res.get("summary"):
            blocks.append(f"[image:{doc['filename']}]\n{private_images.UNTRUSTED_PREFIX}"
                          f"{res['summary'][:1500]}")
        else:
            blocks.append(f"[image:{doc['filename']}] analyzed locally: "
                          f"{res['dims']['width']}x{res['dims']['height']}, "
                          f"ocr={res['ocr_status']}, no text recognized.")
    return "\n".join(blocks)


def _with_sources(text: str, sources: list[dict]) -> str:
    if not sources:
        return text
    lines = "\n\nSources:\n" + "\n".join(
        f"* {s['filename']} — {_cite(s['page'], s['chunk_index'])}" for s in sources)
    return text.rstrip() + lines


def _resolve_conversation(cid: str | None, user: dict, first_text: str) -> dict:
    if cid:
        convo = store.get_conversation(cid, user["id"])
        if convo is None:
            raise HTTPException(404, "conversation not found")
        return convo
    title = " ".join(first_text.split())[:60] or "chat"
    return store.create_conversation(user["id"], user["role"], title)


def chat_respond(user: dict, b: ChatIn) -> dict:
    """Shared non-streaming chat core (used by /ai/chat AND /voice/chat).

    Voice reuses this verbatim — same auth, RBAC, RAG filtering, citations,
    translation, storage, and audit. No parallel path exists by construction.
    """
    from . import ratelimit
    from .i18n import get_translation_provider, not_found_in, translate_answer
    ratelimit.check("chat", user["id"], user)
    t0 = time.time()
    svc = get_service()
    lang = _resolve_lang(user, b)
    tr = get_translation_provider()
    try:
        convo = _resolve_conversation(b.conversation_id, user, b.messages[-1].content)
        msgs = [{"role": m.role, "content": m.content} for m in b.messages]
        rag_used = bool(b.document_ids) or b.mode == "my_docs"
        extra_ctx, sources = _prepare_rag(user, b)
        img_ctx = _prepare_images(user, b)  # explicit refs only; 403/404 above
        extra_ctx = "\n".join(x for x in (extra_ctx, img_ctx) if x)
        if rag_used and not sources:
            nf = not_found_in(lang)
            store.add_message(convo["id"], "user", b.messages[-1].content, lang=lang)
            asst = store.add_message(convo["id"], "assistant", nf,
                                     provider="none", model="none", tools_used=["rag"],
                                     lang=lang)
            audit_log.append(user["id"], user["role"], "doc_question", resource=convo["id"],
                             detail=f"docs={','.join(b.document_ids or []) or 'my_docs'} "
                                    f"hits=0 mode={b.mode} lang={lang}")
            return {"conversation_id": convo["id"], "message": asst, "provider": "none",
                    "model": "none", "prompt_id": user["role"], "tools_used": ["rag"],
                    "sources": [], "lang": lang, "translated": lang != "en",
                    "elapsed_s": round(time.time() - t0, 3)}
        try:
            res = svc.chat(user, msgs, tools=b.tools, lang=lang, extra_context=extra_ctx)
        except HTTPException:
            raise
        except ProviderUnavailable as e:
            audit_log.append(user["id"], user["role"], "ai_provider_error",
                             resource="chat", decision="deny", detail=str(e)[:200])
            raise HTTPException(502, f"AI provider unavailable: {e}")
        except AIError as e:
            audit_log.append(user["id"], user["role"], "ai_provider_error",
                             resource="chat", decision="deny", detail=str(e)[:200])
            raise HTTPException(502, f"AI provider error: {e}")
        text = _with_sources(res["text"], sources)
        text, translated = translate_answer(text, lang, tr)  # Sources stay English
        store.add_message(convo["id"], "user", b.messages[-1].content, lang=lang)
        tools_used = res["tools_used"] + (["rag"] if sources else [])
        asst = store.add_message(convo["id"], "assistant", text,
                                 provider=res["provider"], model=res["model"],
                                 tools_used=tools_used, lang=lang)
        meta = _audit_meta(user, len(msgs), b.tools, lang)
        audit_log.append(
            user["id"], user["role"], "ai_chat", resource=convo["id"],
            detail=f"provider={res['provider']} model={res['model']} "
                   f"tools={','.join(res['tools_used']) or '-'} lang={lang} "
                   f"translated={translated} "
                   f"latency={time.time()-t0:.2f}s in_chars={sum(len(m['content']) for m in msgs)} "
                   f"out_chars={len(text)}")
        if sources:
            audit_log.append(user["id"], user["role"], "doc_question", resource=convo["id"],
                             detail=f"docs={','.join(sorted({s['doc_id'] for s in sources}))} "
                                    f"hits={len(sources)} mode={b.mode} lang={lang}")
        return {"conversation_id": convo["id"], "message": asst,
                "provider": res["provider"], "model": res["model"],
                "prompt_id": res["prompt_id"], "tools_used": tools_used,
                "sources": sources, "lang": lang, "translated": translated,
                "elapsed_s": res["elapsed_s"]}
    except HTTPException as e:
        if e.status_code == 403:
            audit_log.append(user["id"], user["role"], "ai_chat_denied",
                             resource=b.conversation_id or "new", decision="deny",
                             detail=str(e.detail)[:200])
        raise


@router.post("/chat")
def chat(b: ChatIn, user: dict = Depends(require_perm("AI_CHAT"))):
    return chat_respond(user, b)


@router.post("/chat/stream")
def chat_stream(b: ChatIn, user: dict = Depends(require_perm("AI_CHAT"))):
    from . import ratelimit
    from .i18n import get_translation_provider, not_found_in, translate_answer
    ratelimit.check("chat", user["id"], user)
    svc = get_service()
    lang = _resolve_lang(user, b)
    tr = get_translation_provider()
    try:
        convo = _resolve_conversation(b.conversation_id, user, b.messages[-1].content)
        extra_ctx, sources = _prepare_rag(user, b)
        img_ctx = _prepare_images(user, b)
        extra_ctx = "\n".join(x for x in (extra_ctx, img_ctx) if x)
    except HTTPException as e:
        raise e
    msgs = [{"role": m.role, "content": m.content} for m in b.messages]
    store.add_message(convo["id"], "user", b.messages[-1].content, lang=lang)

    def gen():
        yield f"event: meta\ndata: {convo['id']}\n\n"
        if (bool(b.document_ids) or b.mode == "my_docs") and not sources:
            nf = not_found_in(lang)
            yield f"data: {nf}\n\n"
            store.add_message(convo["id"], "assistant", nf,
                              provider="none", model="none", tools_used=["rag"], lang=lang)
            audit_log.append(user["id"], user["role"], "doc_question", resource=convo["id"],
                             detail=f"docs={','.join(b.document_ids or []) or 'my_docs'} hits=0 lang={lang}")
            yield "event: done\ndata: ok\n\n"
            return
        chunks: list[str] = []
        try:
            for delta in svc.chat_stream(user, msgs, tools=b.tools, lang=lang,
                                         extra_context=extra_ctx):
                chunks.append(delta)
                if lang == "en":
                    yield f"data: {delta}\n\n"
        except HTTPException as e:
            yield f"event: error\ndata: {e.status_code} {e.detail}\n\n"
            audit_log.append(user["id"], user["role"], "ai_chat_denied",
                             resource=convo["id"], decision="deny", detail=str(e.detail)[:200])
            return
        except (AIError, ProviderUnavailable) as e:
            yield f"event: error\ndata: 502 {e}\n\n"
            audit_log.append(user["id"], user["role"], "ai_provider_error",
                             resource=convo["id"], decision="deny", detail=str(e)[:200])
            return
        en_full = _with_sources("".join(chunks), sources)
        full, translated = translate_answer(en_full, lang, tr)
        if translated:  # non-English: emit translated text in chunks after translating
            for w in full.split(" "):
                yield f"data: {w} \n\n"
        else:
            yield f"data: {full[len(''.join(chunks)):]}\n\n"
        prov = svc.provider
        tools_used = (b.tools or []) + (["rag"] if sources else [])
        store.add_message(convo["id"], "assistant", full, provider=prov.name,
                          model=getattr(prov, "model", prov.name), tools_used=tools_used,
                          lang=lang)
        audit_log.append(user["id"], user["role"], "ai_chat", resource=convo["id"],
                         detail=f"provider={prov.name} stream out_chars={len(full)} "
                                f"lang={lang} translated={translated}")
        if sources:
            audit_log.append(user["id"], user["role"], "doc_question", resource=convo["id"],
                             detail=f"docs={','.join(sorted({s['doc_id'] for s in sources}))} "
                                    f"hits={len(sources)} mode={b.mode} lang={lang}")
        yield "event: done\ndata: ok\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.get("/conversations")
def convos(user: dict = Depends(require_perm("AI_CHAT"))):
    return {"conversations": store.list_conversations(user["id"])}


@router.get("/conversations/{cid}")
def convo_detail(cid: str, user: dict = Depends(require_perm("AI_CHAT"))):
    convo = store.get_conversation(cid, user["id"])
    if convo is None:
        raise HTTPException(404, "conversation not found")
    return {"conversation": convo, "messages": store.get_messages(cid, user["id"])}


@router.delete("/conversations/{cid}")
def convo_delete(cid: str, user: dict = Depends(require_perm("AI_CHAT"))):
    if not store.delete_conversation(cid, user["id"]):
        raise HTTPException(404, "conversation not found")
    audit_log.append(user["id"], user["role"], "ai_convo_delete", resource=cid)
    return {"ok": True}


@router.get("/status")
def status(user: dict = Depends(get_current_user)):
    svc = get_service()
    info = svc.provider.health()
    from .embeddings import get_embedding_provider
    from .vectorstore import get_vector_store
    from .i18n import get_translation_provider
    emb, vs = get_embedding_provider(), get_vector_store()
    tr = get_translation_provider()
    return {"provider": info.get("provider"), "model": info.get("model", ""),
            "reachable": info.get("reachable", False), "detail": info.get("detail", ""),
            "role": user["role"], "prompt_id": user["role"],
            "backend": store.backend(),
            "rag": {"embed_provider": emb.name, "embed_dim": emb.dim,
                    "vector_backend": vs.health().get("backend")},
            "i18n": {"translate_provider": tr.name,
                     "translate_available": tr.available()}}


@router.get("/tools")
def tools(user: dict = Depends(require_perm("AI_CHAT"))):
    return {"tools": visible_tools(user["role"])}
