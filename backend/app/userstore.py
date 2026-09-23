"""Phase 1 user store — SQLite (dev/on-prem single node).

Schema is Postgres-compatible (plain tables, no SQLite-isms in queries)
so migration is a connection-string swap later.
Table: users(id TEXT PK, username UNIQUE, pass_hash, role, active, created_at)
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid

from . import config as cfg
from .passwords import hash_password, verify_password
from .rbac import ROLES

AUTH_DB = os.getenv("SOV_AUTH_DB", "") or cfg.data_path("auth.db")

# Main demo accounts (prototype only — hashed with bcrypt, never stored).
# Strong unique credentials (browser breach-warning safe). Legacy/plain
# non-email seeds below are unchanged for regression compatibility.
SEED_USERS = [
    ("admin", "Admin123!", "ADMIN"),
    ("manager", "Mgr123!", "MANAGER"),
    ("operator", "Op123!", "OPERATOR"),
    ("reviewer", "Rev123!", "REVIEWER"),
    ("user", "User123!", "USER"),
    # Step 18: legacy demo accounts (README) so they can also use the v1 API.
    ("field1", "field123", "field_engineer"),
    ("process1", "proc123", "process_engineer"),
    ("safety1", "safe123", "safety_inspector"),
    ("manager1", "mgr123", "approving_manager"),
    ("admin1", "adm123", "security_admin"),
    ("audit1", "aud123", "auditor"),
    # SIH demo company accounts (organization email login). Passwords are
    # demo-only and hashed with bcrypt like every other seed — never stored.
    ("admin@company.com", "Admin@2026#S9x!", "ADMIN"),
    ("manager@company.com", "Manager@2026#K7p!", "MANAGER"),
    ("employee@company.com", "Employee@2026#R4m!", "EMPLOYEE"),
    ("employee1@company.com", "Employee@2026#R4m!", "EMPLOYEE"),
    ("employee2@company.com", "Employee@2026#R4m!", "EMPLOYEE"),
    ("operator@company.com", "Operator@2026#T8q!", "OPERATOR"),
    ("reviewer@company.com", "Reviewer@2026#V6n!", "REVIEWER"),
]

# Demo seeds whose password hash is re-synced on startup (fixes the stale-hash
# login failure: init_db inserts only missing rows, so old DBs kept the old
# weak password after the seed rotation). Legacy seeds are never touched.
DEMO_PASSWORD_SYNC = {
    "admin@company.com": "Admin@2026#S9x!",
    "manager@company.com": "Manager@2026#K7p!",
    "employee@company.com": "Employee@2026#R4m!",
    "employee1@company.com": "Employee@2026#R4m!",
    "employee2@company.com": "Employee@2026#R4m!",
    "operator@company.com": "Operator@2026#T8q!",
    "reviewer@company.com": "Reviewer@2026#V6n!",
}

# Legacy demo accounts: the login-page cards show the spec demo passwords
# (process123, safety123, ...) while the stored seed hashes above stay
# unchanged (README/verify scripts pin the original passwords). Both are
# accepted at login — additive dual-accept; no stored password is replaced.
LEGACY_PASSWORD_ALIASES = {
    "process1": "process123",
    "safety1": "safety123",
    "manager1": "manager123",
    "admin1": "admin123",
    "audit1": "audit123",
}


def password_ok(username: str, plain: str, pass_hash: str) -> bool:
    """Verify a login password: stored hash first, then the legacy demo
    alias (constant-time compare; the alias map never leaves the server)."""
    if verify_password(plain, pass_hash):
        return True
    alias = LEGACY_PASSWORD_ALIASES.get(username)
    if not alias:
        return False
    import hmac
    return hmac.compare_digest(plain, alias)

# Step 27: seed departments define the default manager team scope.
# (ADMIN is global; security_admin/auditor never see work data.)
SEED_DEPTS = {
    "manager": "Operations", "operator": "Operations", "user": "Operations",
    "reviewer": "Quality", "manager1": "Operations",
    # SIH demo company org: one team so the demo flow connects end-to-end.
    "manager@company.com": "Production",
    "employee@company.com": "Production",
    "employee1@company.com": "Production",
    "employee2@company.com": "Production",
    "operator@company.com": "Production",
    "reviewer@company.com": "Quality",
    "admin@company.com": "HQ",
}

# Demo profile seeds (phone must be unique + valid; employee IDs unique).
SEED_PHONES = {
    "admin@company.com": "+91-9810000001",
    "manager@company.com": "+91-9810000002",
    "employee@company.com": "+91-9810000003",
    "employee1@company.com": "+91-9810000004",
    "employee2@company.com": "+91-9810000005",
    "operator@company.com": "+91-9810000006",
    "reviewer@company.com": "+91-9810000007",
}
SEED_EMP_IDS = {
    "admin@company.com": "EMP007",
    "manager@company.com": "EMP006",
    "employee@company.com": "EMP001",
    "employee1@company.com": "EMP002",
    "employee2@company.com": "EMP003",
    "operator@company.com": "EMP004",
    "reviewer@company.com": "EMP005",
}
SEED_NAMES = {
    "admin@company.com": ("Admin", "", "User"),
    "manager@company.com": ("Demo", "", "Manager"),
    "employee@company.com": ("Demo", "", "Employee"),
    "employee1@company.com": ("Employee", "", "One"),
    "employee2@company.com": ("Employee", "", "Two"),
    "operator@company.com": ("Demo", "", "Operator"),
    "reviewer@company.com": ("Demo", "", "Reviewer"),
}

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
GENDERS = ["male", "female", "other", "prefer_not_to_say"]
EMP_STATUSES = ["ACTIVE", "SUSPENDED", "ARCHIVED"]

# Employee profile columns (all TEXT, additive; enforced in code, not DDL,
# so pre-existing DBs migrate without conflicts).
PROFILE_COLUMNS = [
    "phone", "alt_phone",
    "first_name", "middle_name", "last_name", "contact_email",
    "addr1", "addr2", "city", "state", "country", "postal",
    "employee_id", "designation", "team", "manager_id", "joining_date",
    "emp_status", "dob", "gender", "blood_group",
    "emergency_name", "emergency_phone", "emergency_rel",
    "identity_status", "photo_doc_id",
]

# Fields an employee may edit on their OWN profile (employment control
# stays with authorized staff; role/employee_id/department/manager never).
SELF_EDITABLE = {
    "phone", "alt_phone", "contact_email",
    "addr1", "addr2", "city", "state", "country", "postal",
    "emergency_name", "emergency_phone", "emergency_rel",
    "first_name", "middle_name", "last_name",
}
# Fields only authorized staff (USER_UPDATE) may change.
STAFF_EDITABLE = SELF_EDITABLE | {
    "designation", "team", "manager_id", "joining_date", "emp_status",
    "dob", "gender", "blood_group", "identity_status", "department", "role",
}


def _con() -> sqlite3.Connection:
    # WAL + a generous busy timeout make concurrent readers/writers wait
    # briefly instead of failing with "database is locked" (500s) when the
    # browser fires many parallel requests on page load.
    con = sqlite3.connect(AUTH_DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return con


# init_db() performs heavy writes (DDL, seed inserts, backfills, bcrypt
# re-hash). Running it on EVERY read call serialized all requests on the
# write lock and produced intermittent 500 "database is locked" errors.
# We therefore initialize each database path once per process; re-running
# is still safe/idempotent if the path ever changes.
_initialized_paths: set[str] = set()


def _ensure_profile_columns(con: sqlite3.Connection) -> None:
    existing = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    for col in PROFILE_COLUMNS:
        if col not in existing:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")


def init_db() -> None:
    if AUTH_DB in _initialized_paths:
        return
    con = _con()
    con.execute(
        """CREATE TABLE IF NOT EXISTS users(
             id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
             pass_hash TEXT NOT NULL, role TEXT NOT NULL,
             active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)"""
    )
    try:  # Step 19: language preference (existing DBs gain the column)
        con.execute("ALTER TABLE users ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'")
    except Exception:
        pass
    try:  # Step 27: department drives manager team scope
        con.execute("ALTER TABLE users ADD COLUMN department TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    _ensure_profile_columns(con)
    con.execute(
        """CREATE TABLE IF NOT EXISTS password_resets(
             email TEXT PRIMARY KEY, otp_hash TEXT NOT NULL,
             expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)"""
    )
    con.commit()
    for username, password, role in SEED_USERS:
        row = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            fn, mn, ln = SEED_NAMES.get(username, ("", "", ""))
            con.execute(
                "INSERT INTO users(id,username,pass_hash,role,active,created_at,department,"
                "phone,first_name,middle_name,last_name,employee_id,emp_status)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"u-{username}", username, hash_password(password), role, 1, time.time(),
                 SEED_DEPTS.get(username, ""), SEED_PHONES.get(username, ""),
                 fn, mn, ln, SEED_EMP_IDS.get(username, ""), "ACTIVE"),
            )
    # Backfill departments on pre-Step-27 databases (seed accounts only,
    # only where still empty — never overwrites an admin's assignment).
    for username, dept in SEED_DEPTS.items():
        if dept:
            con.execute("UPDATE users SET department=? WHERE username=? AND department=''",
                        (dept, username))
    # Backfill demo profile fields (only where empty — never overwrites).
    for username, phone in SEED_PHONES.items():
        if phone:
            con.execute("UPDATE users SET phone=? WHERE username=? AND phone=''",
                        (phone, username))
    for username, eid in SEED_EMP_IDS.items():
        if eid:
            con.execute("UPDATE users SET employee_id=? WHERE username=? AND employee_id=''",
                        (eid, username))
    for username, (fn, mn, ln) in SEED_NAMES.items():
        con.execute("UPDATE users SET first_name=?, middle_name=?, last_name=?"
                    " WHERE username=? AND first_name='' AND last_name=''",
                    (fn, mn, ln, username))
    con.execute("UPDATE users SET emp_status='ACTIVE' WHERE emp_status=''")
    # Re-sync demo password hashes after the strong-password rotation.
    # Stale dev DBs kept the old weak hash (init only inserts missing rows),
    # which was the actual "invalid credentials" root cause for
    # employee@company.com. Legacy seeds are never touched here.
    for username, password in DEMO_PASSWORD_SYNC.items():
        r = con.execute("SELECT pass_hash FROM users WHERE username=?", (username,)).fetchone()
        if r and not verify_password(password, r["pass_hash"]):
            con.execute("UPDATE users SET pass_hash=? WHERE username=?",
                        (hash_password(password), username))
    con.commit()
    con.close()
    _initialized_paths.add(AUTH_DB)


def _row_to_user(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "username": r["username"],
        "role": r["role"],
        "active": bool(r["active"]),
        "created_at": r["created_at"],
        "department": r["department"] if "department" in r.keys() else "",
    }


def find_by_username(username: str) -> dict | None:
    """Full record incl. pass_hash (for login only — never return to clients)."""
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    con.close()
    return dict(r) if r else None


def find_by_id(uid: str) -> dict | None:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    return dict(r) if r else None


def public_user(rec: dict) -> dict:
    out = {k: rec[k] for k in ("id", "username", "role", "active", "created_at") if k in rec}
    out["lang"] = rec.get("lang") or "en"
    out["department"] = rec.get("department") or ""
    return out


def list_users() -> list[dict]:
    init_db()
    con = _con()
    rows = con.execute("SELECT * FROM users ORDER BY username").fetchall()
    con.close()
    return [public_user(dict(r)) for r in rows]


def create_user(username: str, password: str, role: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"unknown role '{role}'")
    if len(password) < 6:
        raise ValueError("password must be at least 6 characters")
    init_db()
    con = _con()
    if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        con.close()
        raise ValueError("username already exists")
    rec = {
        "id": "u-" + uuid.uuid4().hex[:8],
        "username": username,
        "pass_hash": hash_password(password),
        "role": role,
        "active": 1,
        "created_at": time.time(),
    }
    con.execute(
        "INSERT INTO users(id,username,pass_hash,role,active,created_at)"
        " VALUES(:id,:username,:pass_hash,:role,:active,:created_at)",
        rec,
    )
    con.commit()
    con.close()
    return public_user(rec)


def update_user(uid: str, role: str | None = None,
                active: bool | None = None, password: str | None = None,
                department: str | None = None) -> dict | None:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        con.close()
        return None
    new_role = r["role"] if role is None else role
    if new_role not in ROLES:
        con.close()
        raise ValueError(f"unknown role '{role}'")
    new_active = r["active"] if active is None else (1 if active else 0)
    new_hash = r["pass_hash"] if password is None else hash_password(password)
    if password is not None and len(password) < 6:
        con.close()
        raise ValueError("password must be at least 6 characters")
    new_dept = r["department"] if department is None else department[:120]
    con.execute("UPDATE users SET role=?, active=?, pass_hash=?, department=? WHERE id=?",
                (new_role, new_active, new_hash, new_dept, uid))
    con.commit()
    r2 = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    return public_user(dict(r2))


# ---------------- employee profile helpers ----------------

def validate_phone(phone: str) -> bool:
    """Accept +, digits, spaces, dashes, parens; 7–15 digits total."""
    import re
    digits = re.sub(r"\D", "", phone or "")
    if not (7 <= len(digits) <= 15):
        return False
    return bool(re.fullmatch(r"[+\d][\d\s\-().]*", (phone or "").strip()))


def validate_new_password(pw: str) -> str | None:
    """Strong policy for change/reset/create flows. Returns error or None."""
    if len(pw) < 8:
        return "password must be at least 8 characters"
    if len(pw) > 128:
        return "password is too long"
    import re
    if not re.search(r"[A-Z]", pw):
        return "password must include an uppercase letter"
    if not re.search(r"[a-z]", pw):
        return "password must include a lowercase letter"
    if not re.search(r"\d", pw):
        return "password must include a digit"
    return None


def full_name_of(rec: dict) -> str:
    parts = [rec.get("first_name", ""), rec.get("middle_name", ""), rec.get("last_name", "")]
    full = " ".join(p for p in parts if p).strip()
    return full or rec.get("username", "")


def get_profile(uid: str) -> dict | None:
    """Full profile record (server-side only — callers must privacy-filter)."""
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    if not r:
        return None
    d = dict(r)
    d.pop("pass_hash", None)
    d["full_name"] = full_name_of(d)
    return d


def find_by_phone(phone: str) -> dict | None:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    con.close()
    return dict(r) if r else None


def find_by_employee_id(eid: str) -> dict | None:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM users WHERE employee_id=?", (eid,)).fetchone()
    con.close()
    return dict(r) if r else None


def generate_employee_id(prefix: str = "EMP") -> str:
    """Next EMPnnn id (server-generated; employees never choose their own)."""
    init_db()
    con = _con()
    rows = con.execute("SELECT employee_id FROM users WHERE employee_id LIKE ?",
                       (f"{prefix}%",)).fetchall()
    con.close()
    mx = 0
    import re
    for (eid,) in rows:
        m = re.fullmatch(rf"{prefix}0*(\d+)", eid or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return f"{prefix}{mx + 1:03d}"


def set_password(uid: str, new_password: str) -> bool:
    init_db()
    con = _con()
    cur = con.execute("UPDATE users SET pass_hash=? WHERE id=?",
                      (hash_password(new_password), uid))
    con.commit()
    n = cur.rowcount
    con.close()
    return n > 0


def update_profile(uid: str, fields: dict, *, staff: bool = False) -> dict | None:
    """Update whitelisted profile fields. Role/employee_id never via self.
    Profile photos are set only through the dedicated photo endpoint (which
    verifies image ownership); photo_doc_id is staff-only here."""
    allowed = (STAFF_EDITABLE | {"photo_doc_id"}) if staff else SELF_EDITABLE
    clean: dict[str, str] = {}
    for k, v in (fields or {}).items():
        if k not in allowed or v is None:
            continue
        clean[k] = str(v)[:500]
    if "phone" in clean and clean["phone"] and not validate_phone(clean["phone"]):
        raise ValueError("invalid phone number")
    if "alt_phone" in clean and clean["alt_phone"] and not validate_phone(clean["alt_phone"]):
        raise ValueError("invalid alternate phone number")
    if "emergency_phone" in clean and clean["emergency_phone"] \
            and not validate_phone(clean["emergency_phone"]):
        raise ValueError("invalid emergency contact phone")
    if "blood_group" in clean and clean["blood_group"] \
            and clean["blood_group"] not in BLOOD_GROUPS:
        raise ValueError("invalid blood group")
    if "gender" in clean and clean["gender"] and clean["gender"] not in GENDERS:
        raise ValueError("invalid gender")
    if "emp_status" in clean and clean["emp_status"] and clean["emp_status"] not in EMP_STATUSES:
        raise ValueError("invalid employment status")
    if "contact_email" in clean and clean["contact_email"] and "@" not in clean["contact_email"]:
        raise ValueError("invalid contact email")
    if not clean:
        return get_profile(uid)
    # Uniqueness on phone (non-empty).
    if clean.get("phone"):
        other = find_by_phone(clean["phone"])
        if other and other["id"] != uid:
            raise ValueError("phone number already in use")
    init_db()
    con = _con()
    if con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is None:
        con.close()
        return None
    sets = ", ".join(f"{k}=?" for k in clean)
    con.execute(f"UPDATE users SET {sets} WHERE id=?", (*clean.values(), uid))
    con.commit()
    con.close()
    return get_profile(uid)


def create_employee(username: str, password: str, role: str, profile: dict | None = None) -> dict:
    """Create login account + employee profile atomically (validated)."""
    from . import config as cfg
    if role not in ROLES:
        raise ValueError(f"unknown role '{role}'")
    if role in ("ADMIN", "security_admin"):
        raise ValueError("employees cannot be created directly as admin roles")
    uname = (username or "").strip()
    if "@" not in uname or "." not in uname.rsplit("@", 1)[1]:
        raise ValueError("organization email is required")
    domain = uname.rsplit("@", 1)[1].lower()
    if domain not in cfg.ALLOWED_EMAIL_DOMAINS:
        raise ValueError("email domain not allowed for this organization")
    pw_err = validate_new_password(password)
    if pw_err:
        raise ValueError(pw_err)
    prof = dict(profile or {})
    phone = (prof.get("phone") or "").strip()
    if not phone or not validate_phone(phone):
        raise ValueError("a valid phone number is required")
    eid = (prof.get("employee_id") or "").strip() or generate_employee_id()
    init_db()
    con = _con()
    if con.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone():
        con.close()
        raise ValueError("email already in use")
    if con.execute("SELECT 1 FROM users WHERE employee_id=? AND employee_id!=''",
                   (eid,)).fetchone():
        con.close()
        raise ValueError("employee ID already in use")
    if con.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone():
        con.close()
        raise ValueError("phone number already in use")
    if prof.get("blood_group") and prof["blood_group"] not in BLOOD_GROUPS:
        con.close()
        raise ValueError("invalid blood group")
    if prof.get("gender") and prof["gender"] not in GENDERS:
        con.close()
        raise ValueError("invalid gender")
    fn, mn, ln = (prof.get("first_name", ""), prof.get("middle_name", ""),
                  prof.get("last_name", ""))
    if not fn.strip() or not ln.strip():
        con.close()
        raise ValueError("first name and last name are required")
    cols = ["id", "username", "pass_hash", "role", "active", "created_at", "department"]
    vals: list = ["u-" + uuid.uuid4().hex[:8], uname, hash_password(password),
                 role, 1, time.time(), (prof.get("department") or "")[:120]]
    for col in PROFILE_COLUMNS:
        if col == "employee_id":
            vals.append(eid)
        elif col == "emp_status":
            vals.append("ACTIVE")
        else:
            vals.append(str(prof.get(col, "") or "")[:500])
        cols.append(col)
    con.execute(f"INSERT INTO users({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
    con.commit()
    con.close()
    rec = find_by_username(uname) or {}
    prof_full = get_profile(rec.get("id", "")) or {}
    out = public_user(rec) if rec else {}
    out["profile"] = {k: prof_full.get(k, "") for k in
                      ["employee_id", "first_name", "last_name", "phone", "department"]}
    return out


# ---------------- demo OTP store (password reset) ----------------

def store_otp(email: str, otp_hash: str, ttl_s: int = 600) -> None:
    init_db()
    con = _con()
    con.execute("INSERT INTO password_resets(email,otp_hash,expires,attempts)"
                " VALUES(?,?,?,0) ON CONFLICT(email) DO UPDATE SET"
                " otp_hash=excluded.otp_hash, expires=excluded.expires, attempts=0",
                (email.lower(), otp_hash, time.time() + ttl_s))
    con.commit()
    con.close()


def check_otp(email: str, otp_hash: str) -> bool:
    init_db()
    con = _con()
    r = con.execute("SELECT * FROM password_resets WHERE email=?", (email.lower(),)).fetchone()
    if not r:
        con.close()
        return False
    import hmac
    ok = r["expires"] > time.time() and r["attempts"] < 5 \
        and hmac.compare_digest(r["otp_hash"], otp_hash)
    con.execute("UPDATE password_resets SET attempts=attempts+1 WHERE email=?",
                (email.lower(),))
    if ok:
        con.execute("DELETE FROM password_resets WHERE email=?", (email.lower(),))
    con.commit()
    con.close()
    return bool(ok)


def delete_user(uid: str) -> bool:
    init_db()
    con = _con()
    cur = con.execute("DELETE FROM users WHERE id=?", (uid,))
    con.commit()
    n = cur.rowcount
    con.close()
    return n > 0


def get_lang(uid: str) -> str:
    from .i18n import DEFAULT
    init_db()
    con = _con()
    r = con.execute("SELECT lang FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    return (r["lang"] if r and r["lang"] else DEFAULT) if r else DEFAULT


def set_lang(uid: str, lang: str) -> bool:
    init_db()
    con = _con()
    cur = con.execute("UPDATE users SET lang=? WHERE id=?", (lang, uid))
    con.commit()
    n = cur.rowcount
    con.close()
    return n > 0
