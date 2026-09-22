"""Phase 3 secure documents API (mounted under /api/v1/docs).

  POST   /docs/upload        upload + validate + store (DOCUMENT_UPLOAD)
  GET    /docs               list own (ADMIN: all) + search/filter (DOCUMENT_READ)
  GET    /docs/{id}          metadata + text excerpt (owner or ADMIN)
  GET    /docs/{id}/preview  image thumbnail / text snippet (owner or ADMIN)
  GET    /docs/{id}/download original bytes (owner or ADMIN)
  POST   /docs/{id}/analyze  extract text / OCR now (DOCUMENT_ANALYZE, owner or ADMIN)
  DELETE /docs/{id}          remove file + records (DOCUMENT_DELETE, owner or ADMIN)

Trust model: extension, magic bytes, and (for images) a full Pillow decode
are all verified server-side. The client filename is display-only; the
stored path is a uuid. Uploads dir is never static-mounted — bytes leave
only through permission-checked endpoints. RAG and voice are out of scope.
"""
from __future__ import annotations

import io
import json
import os
import zipfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import audit_log
from . import config as cfg
from . import docs_store as store
from .auth_api import get_current_user, require_perm
from .doc_text import ExtractError, extract
from .vision import VisionError, get_vision_provider

router = APIRouter(prefix="/api/v1/docs", tags=["phase3-docs"])

UPLOAD_DIR = os.getenv("SOV_UPLOADS_DIR", "") or cfg.data_path("uploads")

ALLOWED = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".csv": "csv",
           ".xlsx": "xlsx", ".jpg": "image", ".jpeg": "image", ".png": "image"}
MIME = {"pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain", "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image": "image"}


def max_bytes() -> int:
    try:
        return int(float(os.getenv("SOV_MAX_UPLOAD_MB", "10")) * 1024 * 1024)
    except Exception:
        return 10 * 1024 * 1024


def _sniff(data: bytes) -> str:
    """Magic-byte family: pdf|zip|png|jpg|text|bin. Never trusts the client."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    head = data[:4096]
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
            return "text"
        except Exception:
            pass
    return "bin"


def _classify(filename: str, data: bytes) -> tuple[str, str]:
    """Return (kind, ext). 415 on any mismatch — ext, magic, and (zip/image)
    structural checks must all agree."""
    dot = filename.lower().rfind(".")
    ext = filename.lower()[dot:] if dot >= 0 else ""
    if ext not in ALLOWED:
        raise HTTPException(415, f"unsupported extension '{ext or '(none)'}'")
    kind = ALLOWED[ext]
    magic = _sniff(data)
    expect = {"pdf": "pdf", "docx": "zip", "xlsx": "zip", "txt": "text",
              "csv": "text", "image": None}[kind if kind != "image" else "image"]
    if kind == "image":
        if magic not in ("png", "jpg") or (ext == ".png") != (magic == "png"):
            raise HTTPException(415, "file bytes do not match image extension")
        try:
            get_vision_provider().validate(data)
        except VisionError as e:
            raise HTTPException(415, f"invalid image: {e}")
        return kind, ext
    if magic != expect:
        raise HTTPException(415, f"file bytes ({magic}) do not match '{ext}'")
    if kind in ("docx", "xlsx"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except Exception:
            raise HTTPException(415, "corrupt office document")
        if "[Content_Types].xml" not in names:
            raise HTTPException(415, "not a valid office document")
    return kind, ext


async def _read_capped(f: UploadFile) -> bytes:
    limit = max_bytes()
    chunks, total = [], 0
    while True:
        c = await f.read(1024 * 1024)
        if not c:
            break
        total += len(c)
        if total > limit:
            raise HTTPException(413, f"file exceeds {limit // (1024*1024)} MB limit")
        chunks.append(c)
    if total == 0:
        raise HTTPException(400, "empty file")
    return b"".join(chunks)


def _safe_display_name(name: str) -> str:
    """Display-only name: no separators, no control chars, no leading dots."""
    base = (name or "upload").split("/")[-1].split("\\")[-1].strip() or "upload"
    base = "".join(ch for ch in base if ord(ch) >= 0x20).lstrip(".").strip() or "upload"
    return base[:255]


_STORED_RE = __import__("re").compile(
    r"^d-[0-9a-f]{12}\.(pdf|docx|txt|csv|xlsx|jpg|jpeg|png)$")


def _stored_path(doc: dict) -> str:
    """Resolve the stored file, confined to UPLOAD_DIR.

    The name is server-generated, but it is re-validated on every access so
    a poisoned record can never escape the directory (defense in depth).
    """
    stored = doc.get("stored", "")
    if not _STORED_RE.match(stored):
        raise HTTPException(404, "document not found")
    path = os.path.realpath(os.path.join(UPLOAD_DIR, stored))
    if os.path.dirname(path) != os.path.realpath(UPLOAD_DIR):
        raise HTTPException(404, "document not found")
    if not os.path.isfile(path):
        raise HTTPException(404, "stored file missing")
    return path


def _is_admin(user: dict) -> bool:
    return user["role"] == "ADMIN"


def _get_owned(doc_id: str, user: dict) -> dict:
    """Owner-or-ADMIN fetch. 404 for missing AND foreign ids (no probing)."""
    doc = store.get(doc_id)
    if doc is None or (doc["owner_id"] != user["id"] and not _is_admin(user)):
        raise HTTPException(404, "document not found")
    return doc


def _public(doc: dict) -> dict:
    d = {k: doc[k] for k in ("id", "owner_id", "uploader", "filename", "mime",
                             "ext", "kind", "size", "status", "proc_status",
                             "proc_error", "text_len", "created_at") if k in doc}
    try:
        d["vision"] = json.loads(doc.get("vision") or "{}")
    except Exception:
        d["vision"] = {}
    return d


def _run_analysis(doc: dict, user: dict) -> dict:
    """Extract -> chunk -> embed -> store vectors. Status: PROCESSING->READY/FAILED."""
    from . import rag
    return rag.index_document(doc, user)


# ---------------- endpoints ----------------

@router.post("/upload")
async def upload(f: UploadFile, user: dict = Depends(require_perm("DOCUMENT_UPLOAD"))):
    from . import private_images, ratelimit
    ratelimit.check("upload", user["id"], user)
    data = await _read_capped(f)
    kind, ext = _classify(f.filename or "upload", data)
    if kind == "image":
        # Private by default: strip EXIF/GPS metadata at ingest. The upload
        # itself never triggers OCR, indexing, or external calls.
        try:
            data = private_images.sanitize_image(data)
        except Exception:
            pass  # validated above; keep original bytes rather than lose data
        data = private_images.protect(data)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    try:
        os.chmod(UPLOAD_DIR, 0o700)
    except Exception:
        pass
    import uuid as _uuid
    stored = f"d-{_uuid.uuid4().hex[:12]}{ext}"
    with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
        fh.write(data)
    try:
        os.chmod(os.path.join(UPLOAD_DIR, stored), 0o600)
    except Exception:
        pass
    doc = store.create(user["id"], user["username"], _safe_display_name(f.filename or "upload"),
                       stored, MIME[kind if kind != "image" else "image"] if kind != "image"
                       else ("image/png" if ext == ".png" else "image/jpeg"),
                       ext, kind, len(data))
    doc = store.get(doc["id"])  # status=UPLOADED (UPLOADED -> PROCESSING -> READY|FAILED)
    audit_log.append(user["id"], user["role"], "doc_upload", resource=doc["id"],
                     detail=f"{doc['filename']} {kind} {len(data)}B")
    return _public(doc)


@router.get("")
def list_docs(q: str = "", kind: str = "", status: str = "", owner: str = "",
              created_from: float = 0, created_to: float = 0,
              user: dict = Depends(require_perm("DOCUMENT_READ"))):
    if kind and kind not in ("doc", "image", "pdf", "docx", "txt", "csv", "xlsx"):
        raise HTTPException(400, f"unknown kind filter '{kind}'")
    scope = None if _is_admin(user) else user["id"]
    owner_f = (owner or None) if _is_admin(user) else None  # owner filter is ADMIN-only
    if kind in ("pdf", "docx", "txt", "csv", "xlsx"):
        docs = [d for d in store.list_for(scope, q=q, owner=owner_f,
                                          created_from=created_from, created_to=created_to)
                if store.get(d["id"])["ext"] == f".{kind}"]
    else:
        docs = store.list_for(scope, q=q, owner=owner_f,
                              kind=("image" if kind == "image" else ""),
                              status=status, created_from=created_from,
                              created_to=created_to)
        if kind == "doc":
            docs = [d for d in docs if d["kind"] != "image"]
    return {"documents": [_public(d) for d in docs], "backend": store.backend()}


@router.get("/{doc_id}")
def detail(doc_id: str, user: dict = Depends(require_perm("DOCUMENT_READ"))):
    doc = _get_owned(doc_id, user)
    audit_log.append(user["id"], user["role"], "doc_view", resource=doc_id)
    out = _public(doc)
    out["excerpt"] = store.get_text(doc_id)[:4000]
    return out


@router.get("/{doc_id}/preview")
def preview(doc_id: str, user: dict = Depends(require_perm("DOCUMENT_READ"))):
    from . import private_images
    from .vision import VisionError
    doc = _get_owned(doc_id, user)
    audit_log.append(user["id"], user["role"], "doc_view", resource=doc_id + ":preview")
    if doc["kind"] == "image":
        try:
            with open(_stored_path(doc), "rb") as fh:
                raw = private_images.unprotect(fh.read())
            thumb = get_vision_provider().thumbnail(raw)
        except VisionError:
            raise HTTPException(404, "stored file missing")
        return Response(content=thumb, media_type="image/png", headers=_private_headers())
    return {"excerpt": store.get_text(doc_id)[:2000], "proc_status": doc["proc_status"]}


def _private_headers() -> dict:
    """Private bytes must never sit in shared/public caches."""
    return {"Cache-Control": "private, no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN"}


@router.get("/{doc_id}/download")
def download(doc_id: str, user: dict = Depends(require_perm("DOCUMENT_READ"))):
    from . import private_images
    doc = _get_owned(doc_id, user)
    audit_log.append(user["id"], user["role"], "doc_download", resource=doc_id,
                     detail=f"{doc['filename']} {doc['size']}B")
    if doc["kind"] == "image":
        # Decrypt in memory only — never write plaintext back to disk.
        from .vision import VisionError
        try:
            raw = private_images.unprotect(open(_stored_path(doc), "rb").read())
        except VisionError:
            raise HTTPException(404, "stored file missing")
        disp = f'attachment; filename="{doc["filename"]}"'
        return Response(content=raw, media_type=doc.get("mime") or "application/octet-stream",
                        headers={**_private_headers(), "Content-Disposition": disp})
    return FileResponse(_stored_path(doc), filename=doc["filename"],
                        headers=_private_headers())


@router.post("/{doc_id}/analyze")
def analyze(doc_id: str, user: dict = Depends(require_perm("DOCUMENT_ANALYZE"))):
    from . import ratelimit
    ratelimit.check("analyze", user["id"], user)
    doc = _get_owned(doc_id, user)
    doc = _run_analysis(doc, user)
    audit_log.append(user["id"], user["role"], "doc_analyze", resource=doc_id,
                     detail=f"{doc['proc_status']} chars={doc['text_len']} chunks={doc.get('chunks', '?')}")
    out = _public(doc)
    out["excerpt"] = store.get_text(doc_id)[:4000]
    out["chunks"] = doc.get("chunks", 0)
    return out


@router.post("/{doc_id}/reindex")
def reindex(doc_id: str, user: dict = Depends(require_perm("DOCUMENT_ANALYZE"))):
    """Clear vectors and run the pipeline again (same artifact, fresh index)."""
    from . import rag, ratelimit
    ratelimit.check("analyze", user["id"], user)
    doc = _get_owned(doc_id, user)
    dropped = rag.drop_doc_vectors(doc_id)
    doc = _run_analysis(doc, user)
    audit_log.append(user["id"], user["role"], "doc_reindex", resource=doc_id,
                     detail=f"dropped={dropped} chunks={doc.get('chunks', '?')} status={doc['status']}")
    out = _public(doc)
    out["chunks"] = doc.get("chunks", 0)
    return out


@router.delete("/{doc_id}")
def delete(doc_id: str, user: dict = Depends(require_perm("DOCUMENT_DELETE"))):
    from . import rag
    doc = _get_owned(doc_id, user)
    try:
        os.remove(_stored_path(doc))
    except HTTPException:
        pass  # record says missing/odd name: still purge DB + vectors
    except FileNotFoundError:
        pass
    dropped = rag.drop_doc_vectors(doc_id)  # vectors gone -> no future retrieval
    store.delete(doc_id)
    audit_log.append(user["id"], user["role"], "doc_delete", resource=doc_id,
                     detail=f"{doc['filename']} vectors_dropped={dropped}")
    return {"ok": True}
