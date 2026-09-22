"""JWT auth + RBAC dependency. Token payload: user_id, role, clearance_tier, unit."""
import os
from datetime import datetime, timedelta
import jwt
import yaml
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from . import config as cfg

SECRET = cfg.JWT_SECRET
ALGO = cfg.JWT_ALGO
security = HTTPBearer()

def _load_roles():
    here = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    cands = ([cfg.ROLES_FILE] if cfg.ROLES_FILE else []) + [
        os.path.join(here, "roles.yaml"), "roles.yaml",
        os.path.join(os.getcwd(), "roles.yaml"),
        r"C:\Users\mini2\sovereign-ai-workbench\roles.yaml"]
    for cand in cands:
        if cand and os.path.exists(cand):
            with open(cand) as f:
                return yaml.safe_load(f)
    raise RuntimeError("roles.yaml not found")

ROLES_CFG = _load_roles()
TIER_RANK = ROLES_CFG.get("tier_rank", {"public": 0, "internal": 1, "confidential": 2, "restricted": 3})

# Demo users: username -> {password, user_id, role, unit, clearance_tier}
USERS = {
    "field1":   {"password": "field123", "user_id": "u-field-01", "role": "field_engineer",   "unit": "MRPL-U2", "clearance_tier": "internal"},
    "process1": {"password": "proc123",  "user_id": "u-proc-01",  "role": "process_engineer", "unit": "MRPL-U2", "clearance_tier": "confidential"},
    "safety1":  {"password": "safe123",  "user_id": "u-safe-01",  "role": "safety_inspector", "unit": "MRPL-U2", "clearance_tier": "confidential"},
    "manager1": {"password": "mgr123",   "user_id": "u-mgr-01",   "role": "approving_manager","unit": "MRPL-U2", "clearance_tier": "confidential"},
    "admin1":   {"password": "adm123",   "user_id": "u-adm-01",   "role": "security_admin",   "unit": "HQ",     "clearance_tier": "public"},
    "audit1":   {"password": "aud123",   "user_id": "u-aud-01",   "role": "auditor",          "unit": "HQ",     "clearance_tier": "confidential"},
}

def create_token(user_id: str, role: str, clearance_tier: str, unit: str) -> str:
    payload = {"user_id": user_id, "role": role, "clearance_tier": clearance_tier,
               "unit": unit, "exp": datetime.utcnow() + timedelta(hours=cfg.JWT_EXPIRY_HOURS)}
    return jwt.encode(payload, SECRET, algorithm=ALGO)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    return decode_token(creds.credentials)

def tier_allowed(role: str, doc_tier: str) -> bool:
    return TIER_RANK.get(doc_tier, 99) <= TIER_RANK.get(ROLES_CFG["roles"][role]["max_clearance_tier"], -1)

def require_action(action: str):
    def dep(user: dict = Depends(get_current_user)) -> dict:
        role = user["role"]
        allowed = ROLES_CFG["roles"].get(role, {}).get("allowed_actions", [])
        if action not in allowed:
            raise HTTPException(403, f"role '{role}' lacks action '{action}'")
        return user
    return dep
