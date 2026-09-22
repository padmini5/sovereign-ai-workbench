"""Employee profile + management API (additive, same JWT/RBAC architecture).

Privacy model (server-enforced):
- Self: full own profile (including own blood group / DOB).
- ADMIN / security_admin: all fields (need-based admin access, audited).
- MANAGER: team/department-scoped contact fields; blood group + DOB hidden.
- Everyone else: only directory fields (name, employee ID, dept/team/title).
Sensitive fields never enter AI context — AI tools fetch only what the
endpoint returns for the caller's role, and blood group is never included
unless the viewer is self/ADMIN/security_admin.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from . import audit_log
from . import userstore
from .auth_api import require_perm
from .rbac import has_permission

router = APIRouter(prefix="/api/v1", tags=["employees"])

CONTACT_FIELDS = {"phone", "alt_phone", "contact_email", "addr1", "addr2",
                  "city", "state", "country", "postal", "emergency_name",
                  "emergency_phone", "emergency_rel", "dob"}
SENSITIVE_FIELDS = {"blood_group"}


def _dept_of(uid: str) -> str:
    rec = userstore.find_by_id(uid) or {}
    return rec.get("department", "") or ""


def _can_see_contact(viewer: dict, target: dict) -> bool:
    if viewer["id"] == target["id"]:
        return True
    if viewer["role"] in ("ADMIN", "security_admin"):
        return True
    if viewer["role"] in ("MANAGER", "approving_manager"):
        vd, td = _dept_of(viewer["id"]), (target.get("department") or "")
        if vd and vd == td:
            return True
    return False


def _can_see_sensitive(viewer: dict, target: dict) -> bool:
    if viewer["id"] == target["id"]:
        return True
    return viewer["role"] in ("ADMIN", "security_admin")


def filter_profile(viewer: dict, target: dict) -> dict:
    """Privacy-filtered view. Never includes pass_hash (already stripped)."""
    base = {
        "id": target.get("id", ""), "username": target.get("username", ""),
        "role": target.get("role", ""), "active": bool(target.get("active")),
        "department": target.get("department", ""),
        "employee_id": target.get("employee_id", ""),
        "first_name": target.get("first_name", ""),
        "middle_name": target.get("middle_name", ""),
        "last_name": target.get("last_name", ""),
        "full_name": target.get("full_name")
        or " ".join(p for p in [target.get("first_name", ""), target.get("last_name", "")]
                    if p).strip() or target.get("username", ""),
        "designation": target.get("designation", ""),
        "team": target.get("team", ""),
        "emp_status": target.get("emp_status", "ACTIVE"),
        "joining_date": target.get("joining_date", ""),
        "identity_status": target.get("identity_status", ""),
    }
    if _can_see_contact(viewer, target):
        for f in CONTACT_FIELDS:
            base[f] = target.get(f, "")
    if _can_see_sensitive(viewer, target):
        for f in SENSITIVE_FIELDS:
            base[f] = target.get(f, "")
        base["gender"] = target.get("gender", "")
        base["manager_id"] = target.get("manager_id", "")
    return base


class EmployeeCreate(BaseModel):
    username: str
    password: str
    role: str = "EMPLOYEE"
    profile: dict | None = None


class ProfilePatch(BaseModel):
    fields: dict | None = None


class TempPassword(BaseModel):
    password: str


@router.get("/employees/me")
def my_profile(user: dict = Depends(require_perm("WORK_READ"))):
    prof = userstore.get_profile(user["id"])
    if not prof:
        raise HTTPException(404, "profile not found")
    return filter_profile(user, {**prof, "role": user["role"]})


@router.get("/employees")
def list_employees(user: dict = Depends(require_perm("USER_READ"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    out = []
    for u in userstore.list_users():
        prof = userstore.get_profile(u["id"]) or {}
        merged = {**prof, "role": u["role"], "active": u["active"],
                  "department": prof.get("department") or u.get("department", "")}
        out.append(filter_profile(user, merged))
    return {"employees": out}


@router.post("/employees")
def create_employee(b: EmployeeCreate, user: dict = Depends(require_perm("USER_CREATE"))):
    """ADMIN / security_admin only (only roles holding USER_CREATE)."""
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    try:
        created = userstore.create_employee(b.username, b.password, b.role, b.profile or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit_log.append(user["id"], user["role"], "EMPLOYEE_CREATED",
                     resource=created.get("id", ""), detail=b.username)
    return {"ok": True, "message": "Employee account created successfully.",
            **{k: v for k, v in created.items() if k != "profile"},
            "profile": created.get("profile", {})}


@router.get("/employees/{uid}")
def get_employee(uid: str, user: dict = Depends(require_perm("USER_READ"))):
    prof = userstore.get_profile(uid)
    if not prof:
        raise HTTPException(404, "employee not found")
    if user["role"] not in ("ADMIN", "security_admin", "MANAGER", "approving_manager") \
            and uid != user["id"]:
        raise HTTPException(404, "employee not found")
    if user["role"] in ("MANAGER", "approving_manager") and uid != user["id"]:
        vd, td = _dept_of(user["id"]), (prof.get("department") or "")
        if not vd or vd != td:
            raise HTTPException(404, "employee not found")
    return filter_profile(user, {**prof, "role": prof.get("role", "")})


@router.patch("/employees/{uid}")
def patch_employee(uid: str, b: ProfilePatch, user: dict = Depends(require_perm("WORK_READ"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    target = userstore.get_profile(uid)
    if not target:
        raise HTTPException(404, "employee not found")
    staff = has_permission(user["role"], "USER_UPDATE")
    if uid != user["id"] and not staff:
        raise HTTPException(403, "not authorized to edit this profile")
    fields = dict((b.fields or {}))
    if uid == user["id"] and "role" in fields:
        raise HTTPException(403, "you cannot change your own role")
    if "employee_id" in fields and not staff:
        raise HTTPException(403, "employee ID is assigned by authorized staff")
    if "role" in fields and not staff:
        raise HTTPException(403, "role changes require authorized staff")
    try:
        updated = userstore.update_profile(uid, fields, staff=staff)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, "employee not found")
    audit_log.append(user["id"], user["role"], "EMPLOYEE_UPDATED", resource=uid)
    return filter_profile(user, {**updated, "role": updated.get("role", target.get("role", ""))})


@router.post("/employees/{uid}/suspend")
def suspend(uid: str, user: dict = Depends(require_perm("USER_UPDATE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    if uid == user["id"]:
        raise HTTPException(400, "cannot suspend your own account")
    updated = userstore.update_user(uid, active=False)
    if not updated:
        raise HTTPException(404, "employee not found")
    userstore.update_profile(uid, {"emp_status": "SUSPENDED"}, staff=True)
    audit_log.append(user["id"], user["role"], "ACCOUNT_SUSPENDED", resource=uid)
    return {"ok": True}


@router.post("/employees/{uid}/archive")
def archive(uid: str, user: dict = Depends(require_perm("USER_UPDATE"))):
    from . import ratelimit
    ratelimit.check("work", user["id"], user)
    if uid == user["id"]:
        raise HTTPException(400, "cannot archive your own account")
    updated = userstore.update_user(uid, active=False)
    if not updated:
        raise HTTPException(404, "employee not found")
    userstore.update_profile(uid, {"emp_status": "ARCHIVED"}, staff=True)
    audit_log.append(user["id"], user["role"], "ACCOUNT_ARCHIVED", resource=uid)
    return {"ok": True}


@router.post("/employees/{uid}/reset-password")
def admin_reset(uid: str, b: TempPassword, user: dict = Depends(require_perm("USER_UPDATE"))):
    """Generate a temporary password (demo-labeled when no mail/SMS service)."""
    from . import ratelimit
    ratelimit.check("login", user["id"])
    err = userstore.validate_new_password(b.password)
    if err:
        raise HTTPException(400, err)
    if not userstore.find_by_id(uid):
        raise HTTPException(404, "employee not found")
    userstore.set_password(uid, b.password)
    audit_log.append(user["id"], user["role"], "PASSWORD_RESET_COMPLETED", resource=uid)
    return {"ok": True, "message": "Temporary password set. Share it with the employee "
            "through your organization's secure channel (demo: no SMS/email sent)."}


# ---------------- profile photo (private, authorized, validated) ----------------

PHOTO_MAX_BYTES = 5 * 1024 * 1024


@router.post("/employees/me/photo")
async def upload_my_photo(f: UploadFile,
                          user: dict = Depends(require_perm("WORK_READ"))):
    """Secure profile-photo upload: type/size/dimension validated, EXIF
    stripped, stored privately (no public URL). Only the owner (or staff
    via management) can retrieve it."""
    import os as _os
    import uuid as _uuid
    from . import docs_api
    from . import docs_store
    from . import private_images
    from .vision import VisionError, get_vision_provider
    from . import ratelimit
    ratelimit.check("upload", user["id"], user)
    data = await docs_api._read_capped(f)
    if len(data) > PHOTO_MAX_BYTES:
        raise HTTPException(413, "photo exceeds 5 MB")
    kind, ext = docs_api._classify(f.filename or "photo", data)
    if kind != "image":
        raise HTTPException(415, "photo must be a JPG or PNG image")
    try:
        info = get_vision_provider().validate(data)
    except VisionError as e:
        raise HTTPException(415, f"invalid image: {e}")
    if max(info.get("width", 0), info.get("height", 0)) > 4096:
        raise HTTPException(413, "image dimensions too large")
    try:
        data = private_images.sanitize_image(data)
    except Exception:
        pass
    data = private_images.protect(data)
    _os.makedirs(docs_api.UPLOAD_DIR, exist_ok=True)
    stored = f"d-{_uuid.uuid4().hex[:12]}{ext}"
    with open(_os.path.join(docs_api.UPLOAD_DIR, stored), "wb") as fh:
        fh.write(data)
    try:
        _os.chmod(_os.path.join(docs_api.UPLOAD_DIR, stored), 0o600)
    except Exception:
        pass
    doc = docs_store.create(user["id"], user["username"], "profile-photo",
                            stored, "image/png" if ext == ".png" else "image/jpeg",
                            ext, "image", len(data))
    userstore.update_profile(user["id"], {"photo_doc_id": doc["id"]}, staff=True)
    audit_log.append(user["id"], user["role"], "EMPLOYEE_UPDATED",
                     resource=user["id"], detail="profile photo updated")
    return {"ok": True, "doc_id": doc["id"],
            "width": info.get("width", 0), "height": info.get("height", 0)}


@router.get("/employees/me/photo")
def my_photo(user: dict = Depends(require_perm("WORK_READ"))):
    """Owner-only photo bytes (plus ADMIN/security_admin for management).
    No public URL exists for this resource."""
    from fastapi.responses import Response
    from . import docs_store
    from . import private_images
    from . import docs_api
    prof = userstore.get_profile(user["id"]) or {}
    did = prof.get("photo_doc_id", "")
    if not did:
        raise HTTPException(404, "no profile photo")
    doc = docs_store.get(did)
    if doc is None or doc.get("kind") != "image":
        raise HTTPException(404, "no profile photo")
    if doc["owner_id"] != user["id"] and user["role"] not in ("ADMIN", "security_admin"):
        raise HTTPException(404, "no profile photo")
    data = private_images.read_image_bytes(doc, docs_api.UPLOAD_DIR)
    return Response(content=data,
                    media_type="image/png" if doc.get("ext") == ".png" else "image/jpeg")
