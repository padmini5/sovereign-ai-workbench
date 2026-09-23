"""Phase 1 auth/RBAC API (mounted under /api/v1, additive — legacy /api/* untouched).

Security model: every protected endpoint resolves the user + permissions
server-side. The JWT carries identity only (sub, username, role, typ, exp);
role->permissions come from rbac.py, and active status is re-checked per
request against the user store (deactivated/deleted users lose access).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from . import audit_log
from . import config as cfg
from . import userstore
from .passwords import verify_password
from .rbac import PERMISSIONS, ROLES, has_permission, role_permissions

router = APIRouter(prefix="/api/v1", tags=["phase1-auth"])

security = HTTPBearer(auto_error=False)

ACCESS_MINUTES = int(__import__("os").getenv("SOV_ACCESS_MINUTES", "60"))
REFRESH_HOURS = int(__import__("os").getenv("SOV_REFRESH_HOURS", "8"))


def _encode(sub: str, username: str, role: str, typ: str, delta: timedelta) -> str:
    now = datetime.utcnow()
    return jwt.encode(
        {"sub": sub, "username": username, "role": role, "typ": typ,
         "iat": now, "exp": now + delta},
        cfg.JWT_SECRET, algorithm=cfg.JWT_ALGO,
    )


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, cfg.JWT_SECRET, algorithms=[cfg.JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    if creds is None:
        raise HTTPException(401, "missing credentials")
    claims = _decode(creds.credentials)
    if claims.get("typ") not in ("access", "legacy"):
        raise HTTPException(401, "wrong token type")
    rec = userstore.find_by_id(claims.get("sub", ""))
    if not rec or not rec["active"]:
        raise HTTPException(401, "account disabled or deleted")
    # Role is authoritative from the store, not the token (role changes apply live).
    return {"id": rec["id"], "username": rec["username"], "role": rec["role"]}


def require_perm(perm: str):
    def dep(user: dict = Depends(get_current_user)) -> dict:
        if not has_permission(user["role"], perm):
            audit_log.append(user["id"], user["role"], "permission_denied",
                             resource=perm, decision="deny",
                             detail=f"role {user['role']} lacks {perm}")
            raise HTTPException(403, f"role '{user['role']}' lacks permission '{perm}'")
        return user
    return dep


def require_role(*roles: str):
    def dep(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            audit_log.append(user["id"], user["role"], "permission_denied",
                             resource="role:" + ",".join(roles), decision="deny")
            raise HTTPException(403, f"requires role one of {list(roles)}")
        return user
    return dep


def _tokens(rec: dict) -> dict:
    access = _encode(rec["id"], rec["username"], rec["role"], "access",
                     timedelta(minutes=ACCESS_MINUTES))
    refresh = _encode(rec["id"], rec["username"], rec["role"], "refresh",
                      timedelta(hours=REFRESH_HOURS))
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


def _public_session(rec: dict) -> dict:
    return {"id": rec["id"], "username": rec["username"], "role": rec["role"],
            "permissions": sorted(role_permissions(rec["role"])),
            "lang": rec.get("lang") or "en"}


# ---------------- models ----------------

class LoginIn(BaseModel):
    username: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str


class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = None
    department: str | None = None  # Step 27: ADMIN-assigned team scope


# ---------------- auth ----------------

@router.post("/auth/login")
def login(b: LoginIn):
    from . import ratelimit
    ratelimit.check("login", f"user:{b.username[:64]}")
    uname = (b.username or "").strip()
    if "@" in uname:
        # Organization email login: domain allowlist enforced server-side.
        # Unknown domain -> 403 (does not reveal whether the user exists).
        from . import config as cfg
        domain = uname.rsplit("@", 1)[1].lower()
        if domain not in cfg.ALLOWED_EMAIL_DOMAINS:
            audit_log.append(None, None, "login_failure", resource=uname,
                             decision="deny", detail="disallowed email domain")
            raise HTTPException(403, "email domain not allowed for this organization")
    rec = userstore.find_by_username(uname)
    if rec and not rec["active"]:
        audit_log.append(rec["id"], rec["role"],
                         "login_failure", resource=b.username, decision="deny",
                         detail="account suspended")
        raise HTTPException(403, "account suspended")
    if not rec or not userstore.password_ok(rec["username"], b.password, rec["pass_hash"]):
        audit_log.append(rec["id"] if rec else None, rec["role"] if rec else None,
                         "login_failure", resource=b.username, decision="deny")
        raise HTTPException(401, "invalid username or password")
    audit_log.append(rec["id"], rec["role"], "login_success", resource=rec["username"])
    return {**_tokens(rec), "user": _public_session(rec)}


@router.post("/auth/refresh")
def refresh(b: RefreshIn):
    claims = _decode(b.refresh_token)
    if claims.get("typ") != "refresh":
        raise HTTPException(401, "not a refresh token")
    rec = userstore.find_by_id(claims.get("sub", ""))
    if not rec or not rec["active"]:
        raise HTTPException(401, "account disabled or deleted")
    audit_log.append(rec["id"], rec["role"], "token_refresh", resource=rec["username"])
    return {**_tokens(rec), "user": _public_session(rec)}


@router.post("/auth/logout")
def logout(user: dict = Depends(get_current_user)):
    audit_log.append(user["id"], user["role"], "logout", resource=user["username"])
    return {"ok": True}  # stateless JWT: client discards tokens


@router.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {**user, "permissions": sorted(role_permissions(user["role"])),
            "lang": userstore.get_lang(user["id"])}


@router.get("/auth/permissions")
def my_permissions(user: dict = Depends(get_current_user)):
    return {"role": user["role"], "permissions": sorted(role_permissions(user["role"]))}


class LangSet(BaseModel):
    lang: str


@router.get("/auth/language")
def get_language(user: dict = Depends(get_current_user)):
    from .i18n import LANGS
    return {"lang": userstore.get_lang(user["id"]),
            "supported": [{"code": c, "label": l} for c, l in LANGS]}


@router.put("/auth/language")
def set_language(b: LangSet, user: dict = Depends(get_current_user)):
    from .i18n import SUPPORTED
    if b.lang not in SUPPORTED:
        raise HTTPException(422, f"unsupported language '{b.lang}'")
    userstore.set_lang(user["id"], b.lang)
    audit_log.append(user["id"], user["role"], "pref_lang", resource=b.lang)
    return {"lang": b.lang}


@router.get("/roles")
def list_roles(user: dict = Depends(require_perm("USER_READ"))):
    from .rbac import ROLE_PERMISSIONS
    return {"roles": ROLES, "permissions": PERMISSIONS,
            "matrix": {r: sorted(ROLE_PERMISSIONS[r]) for r in ROLES}}


# ---------------- admin: users ----------------

@router.get("/admin/users")
def users_list(user: dict = Depends(require_perm("USER_READ"))):
    return {"users": userstore.list_users()}


@router.post("/admin/users")
def users_create(b: UserCreate, user: dict = Depends(require_perm("USER_CREATE"))):
    try:
        created = userstore.create_user(b.username, b.password, b.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit_log.append(user["id"], user["role"], "user_create",
                     resource=created["username"], detail=created["role"])
    return created


@router.patch("/admin/users/{uid}")
def users_update(uid: str, b: UserUpdate, user: dict = Depends(require_perm("USER_UPDATE"))):
    if b.role is None and b.active is None and b.password is None and b.department is None:
        raise HTTPException(400, "nothing to update")
    try:
        updated = userstore.update_user(uid, role=b.role, active=b.active,
                                        password=b.password, department=b.department)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, "user not found")
    audit_log.append(user["id"], user["role"], "user_update", resource=uid)
    return updated


@router.delete("/admin/users/{uid}")
def users_delete(uid: str, user: dict = Depends(require_perm("USER_DELETE"))):
    if uid == user["id"]:
        raise HTTPException(400, "cannot delete your own account")
    if not userstore.delete_user(uid):
        raise HTTPException(404, "user not found")
    audit_log.append(user["id"], user["role"], "user_delete", resource=uid)
    return {"ok": True}


@router.get("/admin/audit")
def admin_audit(user: dict = Depends(require_perm("AUDIT_READ"))):
    return {"rows": audit_log.rows()}


# ---------------- password change + demo OTP recovery ----------------

class PasswordChange(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


@router.post("/auth/password/change")
def password_change(b: PasswordChange, user: dict = Depends(get_current_user)):
    from . import ratelimit
    ratelimit.check("login", user["id"])
    if b.new_password != b.confirm_password:
        raise HTTPException(400, "new passwords do not match")
    err = userstore.validate_new_password(b.new_password)
    if err:
        raise HTTPException(400, err)
    rec = userstore.find_by_id(user["id"])
    if not rec or not userstore.password_ok(rec["username"], b.current_password, rec["pass_hash"]):
        raise HTTPException(401, "current password is incorrect")
    userstore.set_password(user["id"], b.new_password)
    audit_log.append(user["id"], user["role"], "PASSWORD_CHANGED",
                     resource=user["username"])
    return {"ok": True, "message": "Password changed successfully."}


class ForgotRequest(BaseModel):
    email: str
    phone: str


class ForgotConfirm(BaseModel):
    email: str
    otp: str
    new_password: str
    confirm_password: str


def _otp_hash(email: str, otp: str) -> str:
    import hashlib
    return hashlib.sha256(f"{email.lower()}|{otp}|sov-otp".encode()).hexdigest()


@router.post("/auth/password/forgot/request")
def forgot_request(b: ForgotRequest):
    """Generic response always (anti-enumeration). Demo OTP is exposed only
    when demo login is enabled; production just logs the event."""
    from . import ratelimit
    ratelimit.check("login", f"user:{(b.email or '')[:64]}")
    email = (b.email or "").strip().lower()
    phone = (b.phone or "").strip()
    rec = userstore.find_by_username(email)
    prof = userstore.get_profile(rec["id"]) if rec else None
    match = bool(rec and rec["active"] and prof
                 and prof.get("phone") and prof["phone"].strip() == phone)
    if match:
        import secrets
        otp = f"{secrets.randbelow(900000) + 100000:06d}"
        userstore.store_otp(email, _otp_hash(email, otp))
        audit_log.append(rec["id"], rec["role"], "PASSWORD_RESET_REQUESTED",
                         resource=email)
    else:
        audit_log.append(rec["id"] if rec else None, rec["role"] if rec else None,
                         "PASSWORD_RESET_REQUESTED", resource=email, decision="deny",
                         detail="no matching account")
    out = {"ok": True, "message": "If the account exists, verification instructions "
           "have been provided."}
    if match and cfg.DEMO_LOGIN_ENABLED:
        out["demo_otp"] = otp
        out["demo_notice"] = ("DEMO MODE: no SMS was sent. This one-time code is "
                              "shown only because demo login is enabled.")
    return out


@router.post("/auth/password/forgot/confirm")
def forgot_confirm(b: ForgotConfirm):
    from . import ratelimit
    ratelimit.check("login", f"user:{(b.email or '')[:64]}")
    email = (b.email or "").strip().lower()
    if b.new_password != b.confirm_password:
        raise HTTPException(400, "new passwords do not match")
    err = userstore.validate_new_password(b.new_password)
    if err:
        raise HTTPException(400, err)
    if not userstore.check_otp(email, _otp_hash(email, (b.otp or "").strip())):
        audit_log.append(None, None, "PASSWORD_RESET_REQUESTED", resource=email,
                         decision="deny", detail="invalid or expired OTP")
        raise HTTPException(400, "invalid or expired verification code")
    rec = userstore.find_by_username(email)
    if not rec or not rec["active"]:
        raise HTTPException(400, "invalid or expired verification code")
    userstore.set_password(rec["id"], b.new_password)
    audit_log.append(rec["id"], rec["role"], "PASSWORD_RESET_COMPLETED", resource=email)
    return {"ok": True, "message": "Password changed successfully."}


# ---------------- Phase 1 permission probes ----------------
# Lightweight stubs proving backend enforcement for doc/AI/model/system
# perms. Replaced by real implementations in later phases.

@router.get("/demo/documents")
def probe_doc_read(user: dict = Depends(require_perm("DOCUMENT_READ"))):
    return {"ok": True, "perm": "DOCUMENT_READ"}


@router.post("/demo/documents/upload")
def probe_doc_upload(user: dict = Depends(require_perm("DOCUMENT_UPLOAD"))):
    return {"ok": True, "perm": "DOCUMENT_UPLOAD"}


@router.delete("/demo/documents/{doc_id}")
def probe_doc_delete(doc_id: str, user: dict = Depends(require_perm("DOCUMENT_DELETE"))):
    return {"ok": True, "perm": "DOCUMENT_DELETE"}


@router.post("/demo/documents/analyze")
def probe_doc_analyze(user: dict = Depends(require_perm("DOCUMENT_ANALYZE"))):
    return {"ok": True, "perm": "DOCUMENT_ANALYZE"}


@router.get("/demo/models")
def probe_model_read(user: dict = Depends(require_perm("MODEL_READ"))):
    return {"ok": True, "perm": "MODEL_READ"}


@router.post("/demo/models/configure")
def probe_model_configure(user: dict = Depends(require_perm("MODEL_CONFIGURE"))):
    return {"ok": True, "perm": "MODEL_CONFIGURE"}


@router.get("/demo/system")
def probe_system(user: dict = Depends(require_perm("SYSTEM_CONFIGURE"))):
    return {"ok": True, "perm": "SYSTEM_CONFIGURE"}
