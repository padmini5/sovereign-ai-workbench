"""Central production configuration — everything via environment variables.

Local dev/tests work with zero configuration (safe defaults below).
Production sets the SOV_* vars (see / .env.example); no real secrets live in code.
"""
import os

ENV = os.getenv("SOV_ENV", "development")
APP_VERSION = "1.1.0"

# Repo layout: <repo>/backend/{app,seed_docs,...}; runtime data defaults there too.
BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # <repo>/backend
DATA_DIR = os.getenv("SOV_DATA_DIR", "")  # e.g. /var/lib/sov-workbench

def data_path(name: str) -> str:
    base = DATA_DIR or BASE_DIR
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, name)

# --- Auth / JWT ---
JWT_SECRET = os.getenv("SOV_JWT_SECRET", "dev-sovereign-secret-change-me-32B-min")
JWT_ALGO = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("SOV_JWT_EXPIRY_HOURS", "8"))
ROLES_FILE = os.getenv("SOV_ROLES_FILE", "")  # default: <repo>/roles.yaml

# --- Organization email domains (login) ---
# Comma-separated allowlist enforced server-side at login for usernames
# containing '@'. Plain (non-email) usernames are unaffected (legacy compat).
ALLOWED_EMAIL_DOMAINS = [d.strip().lower() for d in
                         os.getenv("ALLOWED_EMAIL_DOMAINS", "company.com").split(",")
                         if d.strip()]

# --- Attendance device gateway ---
# Comma-separated device keys. Empty = gateway disabled (all device calls denied).
DEVICE_KEYS = [k.strip() for k in os.getenv("SOV_DEVICE_KEYS", "").split(",") if k.strip()]

# --- Work schedule (organization default served to clients) ---
# Single org-default shift; per-employee schedules are not stored — the API
# labels this clearly ("org_default") so clients never present it as personal.
SHIFT_NAME = os.getenv("SOV_SHIFT_NAME", "General")
SHIFT_START = os.getenv("SOV_SHIFT_START", "09:00")
SHIFT_END = os.getenv("SOV_SHIFT_END", "17:30")
SHIFT_BREAK = os.getenv("SOV_SHIFT_BREAK", "01:00")
SHIFT_WORKDAYS = [d.strip() for d in
                  os.getenv("SOV_SHIFT_WORKDAYS",
                            "Monday,Tuesday,Wednesday,Thursday,Friday").split(",")
                  if d.strip()]

# --- Network ---
API_HOST = os.getenv("SOV_HOST", "0.0.0.0")
API_PORT = int(os.getenv("SOV_PORT", "8000"))
CORS_ORIGINS = [o.strip() for o in os.getenv("SOV_CORS_ORIGINS", "*").split(",") if o.strip()]

# --- Storage ---
AUDIT_DB = os.getenv("SOV_AUDIT_DB", "") or data_path("audit.db")
KB_STORE = os.getenv("SOV_KB_STORE", "") or data_path("kb_store.json")
CHROMA_DIR = os.getenv("SOV_CHROMA_DIR", "") or data_path("chroma_db")
OUTPUTS_DIR = os.getenv("SOV_OUTPUTS_DIR", "") or data_path("outputs")
KEY_REGISTRY = os.getenv("SOV_KEY_REGISTRY", "") or data_path("key_registry.json")
SEED_DIR = os.path.join(BASE_DIR, "seed_docs")  # ships with code, not runtime data

# --- Optional providers (never mandatory) ---
OLLAMA_URL = os.getenv("SOV_OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL_TIER_DEFAULT = os.getenv("SOV_MODEL_TIER", "large")

# --- Phase 2: AI provider selection ---
# SOV_AI_PROVIDER: auto (default) | mock | mock-fail | ollama.
# OLLAMA_BASE_URL / OLLAMA_MODEL configure the local-model provider.
# No model is assumed installed — health reports presence honestly.
AI_PROVIDER = os.getenv("SOV_AI_PROVIDER", "auto")
OLLAMA_BASE_URL = (os.getenv("OLLAMA_BASE_URL", "") or OLLAMA_URL).rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "") or os.getenv("SOV_OLLAMA_MODEL", "")

# --- Phase 2: chat storage ---
# Default SQLite file; set DATABASE_URL=postgresql://... (with psycopg
# installed) for PostgreSQL. Same schema either way.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- UI ---
# SOV_DEMO_LOGIN=0 hides the one-click demo accounts in the login screen
# (test passwords must not be advertised in production). Manual login still works.
DEMO_LOGIN_ENABLED = os.getenv("SOV_DEMO_LOGIN", "1") == "1"

# --- API documentation ---
# Interactive docs (/api/docs, /api/redoc, /api/openapi.json) are enabled in
# non-production environments. In production they stay disabled unless
# explicitly enabled with SOV_API_DOCS=1.
API_DOCS_ENABLED = (ENV != "production") or (os.getenv("SOV_API_DOCS", "0") == "1")

def is_production() -> bool:
    return ENV == "production"

def log_startup(log) -> None:
    log.info("SovereignAI Workbench v%s env=%s host=%s port=%s",
             APP_VERSION, ENV, API_HOST, API_PORT)
    if not is_production():
        return
    if JWT_SECRET.startswith("dev-"):
        log.warning("PRODUCTION uses the default dev JWT secret — set SOV_JWT_SECRET!")
    if CORS_ORIGINS == ["*"]:
        log.warning("PRODUCTION CORS allows all origins — set SOV_CORS_ORIGINS!")
    if DEMO_LOGIN_ENABLED:
        log.warning("PRODUCTION shows demo quick-login — set SOV_DEMO_LOGIN=0!")
