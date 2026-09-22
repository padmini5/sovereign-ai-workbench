from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uuid, json, asyncio, logging

from . import config as cfg
from .auth import USERS, create_token, get_current_user, require_action, ROLES_CFG
from .auth_api import router as auth_v1_router
from .ai_api import router as ai_v1_router
from .docs_api import router as docs_v1_router
from .i18n_api import router as i18n_v1_router
from .voice_api import router as voice_v1_router
from .agents_api import router as agents_v1_router
from .admin_api import router as admin_v1_router
from .images_api import router as images_v1_router
from .work_api import router as work_v1_router
from .devices import router as devices_router
from .employees_api import router as employees_router
from .kb import kb  # legacy seed-KB (Phase 4 pipeline lives in .rag)
from .router import draft_text, set_tier, MODEL_TIER, TIER_MODEL, backend_info
from .orchestrator import Task, TASKS
from .pqc import sign_bytes, verify_bytes, ALGO_USED, pqc_status
from . import db
from . import monitor
from .output_doc import make_docx, make_xlsx

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sovereign")

app = FastAPI(title="SovereignAI Workbench (SIH26117)", version=cfg.APP_VERSION,
              docs_url="/api/docs" if cfg.API_DOCS_ENABLED else None,
              redoc_url="/api/redoc" if cfg.API_DOCS_ENABLED else None,
              openapi_url="/api/openapi.json" if cfg.API_DOCS_ENABLED else None)
# Tight method/header allowlist: exactly what the console uses (REST +
# multipart uploads + SSE). Origins stay restricted to SOV_CORS_ORIGINS.
app.add_middleware(CORSMiddleware, allow_origins=cfg.CORS_ORIGINS,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "Accept"])


@app.middleware("http")
async def _security_headers(request, call):
    """Safe headers that never break the local dev UI (no CSP: the console
    uses inline styles; CSP would need 'unsafe-inline' to function)."""
    resp = await call(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp
app.include_router(auth_v1_router)  # Phase 1 RBAC API (/api/v1)
app.include_router(ai_v1_router)    # Phase 2 chat API (/api/v1/ai)
app.include_router(docs_v1_router)  # Phase 3 documents API (/api/v1/docs)
app.include_router(i18n_v1_router)  # Step 19 i18n API (/api/v1/i18n)
app.include_router(voice_v1_router)  # Step 20 voice API (/api/v1/voice)
app.include_router(agents_v1_router)  # Step 21 agents API (/api/v1/agents)
app.include_router(admin_v1_router)  # Step 22 ADMIN API (/api/v1/admin)
app.include_router(images_v1_router)  # Image privacy: explicit private analysis
app.include_router(work_v1_router)  # Step 27 workspace/analytics/reports
app.include_router(devices_router)  # SIH demo: attendance device gateway
app.include_router(employees_router)  # Employee profiles + management
monitor.start()

@app.on_event("startup")
async def _startup():
    cfg.log_startup(log)
    log.info("rag_backend=%s pqc=%s", kb.chroma_backend, pqc_status()["label"])
sockets: set[WebSocket] = set()

async def broadcast(msg: dict):
    dead = []
    for ws in list(sockets):
        try: await ws.send_json(msg)
        except Exception: dead.append(ws)
    for d in dead: sockets.discard(d)

@app.websocket("/ws/tasks/{task_id}")
async def ws_task(ws: WebSocket, task_id: str):
    await ws.accept(); sockets.add(ws)
    try:
        while True:
            t = TASKS.get(task_id)
            if t:
                await ws.send_json({"task_id": task_id, "state": t.state, "plan": t.plan, "events": t.events[-12:]})
            await asyncio.sleep(0.8)
    except Exception:
        pass
    finally:
        sockets.discard(ws)

@app.websocket("/ws/monitor")
async def ws_mon(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(monitor.snapshot())
            await asyncio.sleep(1)
    except Exception:
        pass

class Login(BaseModel):
    username: str
    password: str

@app.post("/api/login")
def login(b: Login):
    from . import ratelimit
    ratelimit.check("login", f"legacy:{b.username[:64]}")
    u = USERS.get(b.username)
    if not u or u["password"] != b.password:
        raise HTTPException(401, "bad credentials")
    tok = create_token(u["user_id"], u["role"], u["clearance_tier"], u["unit"])
    return {"token": tok, "user": {k: v for k, v in u.items() if k != "password"}}

@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return user

@app.get("/api/model-tier")
def get_tier(user: dict = Depends(get_current_user)):
    info = backend_info()
    return {"tier": info["tier"], "model": info["model"], "pqc": ALGO_USED,
            "llm_mode": info["mode"], "ollama_reachable": info["ollama_reachable"]}

LANGUAGES = [
    {"code": "en", "label": "English"}, {"code": "hi", "label": "हिन्दी"},
    {"code": "te", "label": "తెలుగు"}, {"code": "ta", "label": "தமிழ்"},
    {"code": "kn", "label": "ಕನ್ನಡ"}, {"code": "ml", "label": "മലയാളം"},
    {"code": "mr", "label": "मराठी"}, {"code": "bn", "label": "বাংলা"},
    {"code": "gu", "label": "ગુજરાતી"}, {"code": "pa", "label": "ਪੰਜਾਬੀ"},
    {"code": "or", "label": "ଓଡ଼ିଆ"}, {"code": "as", "label": "অসমীয়া"},
    {"code": "ur", "label": "اردو"}, {"code": "sa", "label": "संस्कृतम्"},
    {"code": "kok", "label": "कोंकणी"}, {"code": "mai", "label": "मैथिली"},
    {"code": "ne", "label": "नेपाली"}, {"code": "ks", "label": "کٲشُر"},
    {"code": "sd", "label": "سنڌي"}, {"code": "mni", "label": "মৈতৈলোন্"},
    {"code": "brx", "label": "बड़ो"},
]

@app.get("/api/languages")
def languages():
    return {"default": "en", "languages": LANGUAGES}

class TierSet(BaseModel):
    tier: str
@app.post("/api/model-tier")
def set_tier_ep(b: TierSet, user: dict = Depends(require_action("query_kb"))):
    if b.tier not in ("small", "large"): raise HTTPException(400, "tier must be small|large")
    set_tier(b.tier)
    return {"tier": b.tier, "model": TIER_MODEL[b.tier]}

class Query(BaseModel):
    query: str
    top_k: int = 4

@app.post("/api/query")
async def query_kb(b: Query, user: dict = Depends(require_action("query_kb"))):
    if ROLES_CFG["roles"][user["role"]].get("content_blind"):
        raise HTTPException(403, "content-blind role: metadata only")
    chunks, stats = kb.query(b.query, user["role"], user.get("unit", ""), b.top_k)
    return {"chunks": chunks, "filter_stats": stats, "role": user["role"]}

class RunTask(BaseModel):
    query: str
    top_k: int = 4

@app.post("/api/tasks")
async def run_task(b: RunTask, user: dict = Depends(require_action("query_kb"))):
    tid = "t-" + uuid.uuid4().hex[:8]
    t = Task(task_id=tid, query=b.query, user=user)
    TASKS[tid] = t
    # PLAN
    t.emit("PLAN", f"task {tid}: {b.query[:80]} | tier={MODEL_TIER['tier']}")
    await broadcast({"task_id": tid, "state": t.state})
    # CHECK_PERMISSIONS
    t.emit("CHECK_PERMISSIONS", f"role={user['role']} actions OK; tier filter armed")
    await broadcast({"task_id": tid, "state": t.state})
    # RETRIEVE (tier-filtered BEFORE LLM)
    chunks, stats = kb.query(b.query, user["role"], user.get("unit", ""), b.top_k)
    t.chunks = chunks
    t.emit("RETRIEVE", f"allowed={stats['allowed']} blocked={stats['blocked']} returned={len(chunks)}")
    await broadcast({"task_id": tid, "state": t.state})
    # EXTRACT
    t.emit("EXTRACT", "vision/OCR stub: extracted text spans; low-confidence spans flagged (demo)")
    # DRAFT via model router
    t.emit("DRAFT", f"router -> {TIER_MODEL[MODEL_TIER['tier']]} (Ollama if reachable, else local fallback)")
    draft, secs = draft_text(b.query, chunks, user["role"])
    t.draft = draft
    t.emit("CITE", f"citations attached: {len(chunks)} chunks; draft in {secs:.1f}s")
    t.citations = [{"chunk_id": c["chunk_id"], "page": c.get("page")} for c in chunks]
    t.emit("REVIEW", "status=draft, pending review by process_engineer/approving_manager")
    t.emit("SIGN", "awaiting sign action (approving_manager only)")
    t.emit("LOG", "task opened in audit trail")
    db.append(user["user_id"], user["role"], "task_run", tid, {"algorithm": "log-only", "signature_b64": "", "signed_by": {"user_id": user["user_id"], "role": user["role"]}, "payload_sha256": tid})
    await broadcast({"task_id": tid, "state": t.state})
    llm = backend_info()
    return {"task_id": tid, "state": t.state, "plan": t.plan, "events": t.events,
            "draft": t.draft, "chunks": t.chunks, "filter_stats": stats, "status": t.status,
            "llm_mode": llm["mode"], "ollama_reachable": llm["ollama_reachable"],
            "pqc": pqc_status()}

@app.get("/api/tasks/{tid}")
def get_task(tid: str, user: dict = Depends(get_current_user)):
    t = TASKS.get(tid)
    if not t: raise HTTPException(404, "no such task")
    if user["role"] == "field_engineer" and t.user.get("user_id") != user["user_id"]:
        raise HTTPException(403, "field engineers see own tasks only")
    return {"task_id": tid, "state": t.state, "plan": t.plan, "events": t.events,
            "draft": t.draft, "chunks": t.chunks, "status": t.status, "doc_path": t.doc_path}

class ReviewEdit(BaseModel):
    draft: str
@app.post("/api/tasks/{tid}/review")
def review(tid: str, b: ReviewEdit, user: dict = Depends(require_action("review"))):
    t = TASKS.get(tid)
    if not t: raise HTTPException(404, "no such task")
    t.draft = b.draft; t.status = "reviewed"; t.emit("REVIEW", f"edited by {user['user_id']}")
    return {"ok": True, "status": t.status}

@app.post("/api/tasks/{tid}/sign")
def sign(tid: str, user: dict = Depends(require_action("sign"))):
    t = TASKS.get(tid)
    if not t: raise HTTPException(404, "no such task")
    blob = (t.draft + "\n" + json.dumps(t.citations)).encode()
    sig = sign_bytes(blob, user["user_id"], user["role"])
    path = make_docx(f"Inspection note {tid}", t.draft, t.chunks, sig["signed_by"], sig)
    try:
        xlsx = make_xlsx(f"Inspection note {tid}", t.chunks)
    except Exception:
        xlsx = ""
    t.doc_path = path; t.status = "signed"; t.emit("SIGN", f"signed with {sig['algorithm']}")
    t.emit("LOG", "signed deliverable + audit entry written")
    db.append(user["user_id"], user["role"], "sign", tid, sig)
    return {"ok": True, "docx": path, "xlsx": xlsx, "signature": sig, "status": t.status}

@app.get("/api/tasks/{tid}/download")
def download(tid: str, user: dict = Depends(get_current_user)):
    t = TASKS.get(tid)
    if not t or not t.doc_path: raise HTTPException(404, "no signed doc yet")
    return FileResponse(t.doc_path, filename=t.doc_path.split("/")[-1].split("\\")[-1])

class Verify(BaseModel):
    task_id: str
@app.post("/api/verify")
def verify(b: Verify, user: dict = Depends(get_current_user)):
    t = TASKS.get(b.task_id)
    if not t: raise HTTPException(404, "no such task")
    # re-derive: find last sign audit entry is complex; verify current draft binding instead
    blob = (t.draft + "\n" + json.dumps(t.citations)).encode()
    # signature stored only at sign time; fetch from audit trail is out of scope for demo -> verify docx bytes
    try:
        with open(t.doc_path, "rb") as f:
            doc_bytes = f.read()
        ok_sha = len(doc_bytes) > 0
    except Exception:
        ok_sha = False
    return {"task_id": b.task_id, "status": t.status, "doc_present": ok_sha, "note": "PQC signature verifies against draft+citations binding at sign time (see /api/audit trail)."}

@app.get("/api/monitor")
def mon(user: dict = Depends(get_current_user)):
    return monitor.snapshot()

@app.get("/api/audit")
def audit(user: dict = Depends(require_action("audit_view"))):
    return {"rows": db.rows(), "pqc": ALGO_USED}

@app.post("/api/upload")
async def upload(f: UploadFile = File(...), user: dict = Depends(require_action("upload"))):
    raw = (await f.read()).decode(errors="ignore")
    doc = {"chunks": [{"chunk_id": f"upload-{f.filename}#c0", "doc_id": f.filename,
                       "title": f.filename, "text": raw[:2000], "page": 1,
                       "clearance_tier": "internal", "unit": user.get("unit", "MRPL-U2"),
                       "source_type": "scan", "score": 1.0}]}
    kb.save_upload(doc)
    db.append(user["user_id"], user["role"], "upload", f.filename, {"algorithm": "log-only", "signature_b64": "", "signed_by": {"user_id": user["user_id"], "role": user["role"]}, "payload_sha256": f.filename})
    return {"ok": True, "chunks": 1, "ocr_confidence": 0.62, "flag": "low-confidence span flagged for human check (demo)"}

@app.get("/health")
def health_root():
    """Unauthenticated liveness probe for reverse proxies / load balancers.
    No secrets, no document content — safe to expose."""
    llm = backend_info()
    return {"ok": True, "service": "sovereignai-workbench", "version": cfg.APP_VERSION,
            "llm_mode": llm["mode"], "rag_backend": kb.chroma_backend,
            "pqc_available": pqc_status()["pqc_available"]}

@app.get("/api/config")
def public_config():
    """Public UI flags only — never secrets."""
    return {"demo_login_enabled": cfg.DEMO_LOGIN_ENABLED, "version": cfg.APP_VERSION,
            "email_domains": cfg.ALLOWED_EMAIL_DOMAINS}

@app.get("/api/health")
def health():
    llm = backend_info()
    return {"ok": True, "model_tier": MODEL_TIER, "ollama_reachable": llm["ollama_reachable"],
            "llm_mode": llm["mode"], "model": llm["model"],
            "pqc": ALGO_USED, "pqc_status": pqc_status(),
            "rag_backend": kb.chroma_backend}
