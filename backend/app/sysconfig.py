"""Step 22 runtime configuration (ADMIN-only reads/writes via admin_api).

Single source of truth stays ENVIRONMENT: this module validates ADMIN input
against CONFIG_SCHEMA, then writes allowlisted keys to os.environ + a JSON
override file (loaded at startup). Provider factories already read env per
call, so changes take effect with no restarts and no duplicate config system.

Secrets can NEVER enter this store: unknown keys are rejected, and every
output/audit payload passes through scrub().
"""
from __future__ import annotations

import json
import os
import re

from . import config as cfg

PATH = os.getenv("SOV_SYSCONFIG_PATH", "") or cfg.data_path("sysconfig.json")

AGENT_NAMES = ["document_analysis", "report_generation", "review",
               "data_analysis", "admin_assistant"]

CONFIG_SCHEMA: dict[str, dict] = {
    "SOV_AI_PROVIDER": {"type": "enum", "choices": ["auto", "mock", "mock-fail", "ollama"],
                        "desc": "Active AI provider"},
    "OLLAMA_MODEL": {"type": "model", "desc": "LLM model selection"},
    "OLLAMA_BASE_URL": {"type": "url", "desc": "Local model daemon URL"},
    "SOV_EMBED_PROVIDER": {"type": "enum", "choices": ["mock", "hash", "ollama", "fail"],
                           "desc": "Embedding provider"},
    "SOV_EMBED_MODEL": {"type": "model", "desc": "Neural embedding model"},
    "SOV_RAG_TOP_K": {"type": "int", "min": 1, "max": 20, "desc": "Retrieved chunks per query"},
    "SOV_RAG_MIN_SCORE": {"type": "float", "min": 0.0, "max": 0.95,
                          "desc": "Relevance floor (below = not present)"},
    "SOV_TRANSLATE_PROVIDER": {"type": "enum", "choices": ["mock", "local", "unavailable"],
                               "desc": "Language provider"},
    "SOV_STT_PROVIDER": {"type": "enum", "choices": ["mock", "local", "unavailable"],
                         "desc": "Speech-to-text provider"},
    "SOV_TTS_PROVIDER": {"type": "enum", "choices": ["mock", "local", "unavailable"],
                         "desc": "Text-to-speech provider"},
    "SOV_MAX_UPLOAD_MB": {"type": "int", "min": 1, "max": 100, "desc": "Upload cap (MB)"},
    "SOV_MAX_AUDIO_MB": {"type": "int", "min": 1, "max": 50, "desc": "Audio cap (MB)"},
    "SOV_VISION_PROVIDER": {"type": "enum", "choices": ["local"], "desc": "Vision provider"},
    "SOV_FEATURE_VOICE": {"type": "bool", "desc": "Voice endpoints enabled"},
    "SOV_FEATURE_AGENTS": {"type": "bool", "desc": "Agent execution enabled"},
    "SOV_FEATURE_TRANSLATE": {"type": "bool", "desc": "Translate endpoint enabled"},
}
for _a in AGENT_NAMES:
    CONFIG_SCHEMA[f"AGENT_{_a.upper()}_ENABLED"] = {
        "type": "bool", "desc": f"Agent '{_a}' enabled"}

SECRET_HINTS = ("SECRET", "PASSWORD", "PASSWD", "TOKEN", "API_KEY", "CREDENTIAL",
                "PRIVATE_KEY", "DB_PASSWORD")

_URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d+)?(/.*)?$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._\-:/]{1,100}$")


class ConfigError(Exception):
    pass


def validate(key: str, value) -> str:
    """Validate + normalize. Raises ConfigError. Unknown keys rejected outright
    (this is what keeps secrets out — only schema keys are ever stored)."""
    spec = CONFIG_SCHEMA.get(key)
    if spec is None:
        raise ConfigError(f"unknown configuration key '{key}'")
    t = spec["type"]
    if t == "enum":
        v = str(value)
        if v not in spec["choices"]:
            raise ConfigError(f"{key} must be one of {spec['choices']}")
        return v
    if t == "bool":
        if isinstance(value, bool):
            return "1" if value else "0"
        v = str(value).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return "1"
        if v in ("0", "false", "no", "off"):
            return "0"
        raise ConfigError(f"{key} must be boolean")
    if t == "int":
        try:
            v = int(value)
        except Exception:
            raise ConfigError(f"{key} must be an integer")
        if not spec["min"] <= v <= spec["max"]:
            raise ConfigError(f"{key} must be {spec['min']}..{spec['max']}")
        return str(v)
    if t == "float":
        try:
            v = float(value)
        except Exception:
            raise ConfigError(f"{key} must be a number")
        if not spec["min"] <= v <= spec["max"]:
            raise ConfigError(f"{key} must be {spec['min']}..{spec['max']}")
        return str(v)
    if t == "url":
        v = str(value).strip().rstrip("/")
        if not _URL_RE.match(v):
            raise ConfigError(f"{key} must be an http(s) URL")
        return v
    if t == "model":
        v = str(value).strip()
        if not _MODEL_RE.match(v):
            raise ConfigError(f"{key} must be a safe model identifier")
        return v
    raise ConfigError(f"bad schema for '{key}'")


def _load_file() -> dict:
    try:
        with open(PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_file(data: dict) -> None:
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, PATH)


def load_overrides() -> None:
    """Apply persisted allowlisted overrides into env (startup)."""
    for k, v in _load_file().items():
        if k in CONFIG_SCHEMA:
            try:
                os.environ[k] = validate(k, v)
            except ConfigError:
                continue


def effective(key: str) -> str:
    return os.getenv(key, "")


def source_of(key: str) -> str:
    if key in _load_file():
        return "override"
    if key in os.environ:
        return "env"
    return "default"


def describe() -> list[dict]:
    return [{"key": k, "value": effective(k), "source": source_of(k),
             "type": s["type"], "desc": s.get("desc", ""),
             "choices": s.get("choices")}
            for k, s in CONFIG_SCHEMA.items()]


def set_override(key: str, value) -> str:
    """Validate, persist, and apply. Returns normalized value."""
    norm = validate(key, value)  # raises ConfigError (stored nowhere on failure)
    data = _load_file()
    data[key] = norm
    _save_file(data)
    os.environ[key] = norm
    return norm


def clear_override(key: str) -> bool:
    if key not in CONFIG_SCHEMA:
        raise ConfigError(f"unknown configuration key '{key}'")
    data = _load_file()
    existed = key in data
    data.pop(key, None)
    _save_file(data)
    os.environ.pop(key, None)  # fall back to process env/default
    return existed


def scrub_text(blob: str, extra: list[str] | None = None) -> str:
    """Redact any secret values that could appear in a payload."""
    secrets = [os.getenv(k, "") for k in os.environ
               if any(h in k for h in SECRET_HINTS) and os.getenv(k)]
    if extra:
        secrets += extra
    for s in sorted(set(secrets), key=len, reverse=True):
        if s and len(s) >= 4:
            blob = blob.replace(s, "***redacted***")
    return blob


def feature_enabled(name: str) -> bool:
    """voice|agents|translate — default ON, SOV_FEATURE_X=0 disables."""
    return os.getenv(f"SOV_FEATURE_{name.upper()}", "1") != "0"


def ensure_feature(name: str, user: dict) -> None:
    if not feature_enabled(name):
        from fastapi import HTTPException
        from . import audit_log
        audit_log.append(user.get("id"), user.get("role"), "feature_denied",
                         resource=name, decision="deny", detail="feature disabled")
        raise HTTPException(503, f"feature '{name}' is disabled")


def agent_enabled(name: str, default: bool = True) -> bool:
    override = os.getenv(f"AGENT_{name.upper()}_ENABLED", "")
    if override != "":
        return override != "0"
    return default


load_overrides()
