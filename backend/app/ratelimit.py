"""Step 23 rate limiting: in-memory sliding windows, per-process.

Generous defaults never trip normal use or the test suite; strict values
can be set per deployment (SOV_RATE_<NAME> requests/minute). Exceeding the
limit returns 429 + a metadata-only audit event. No state leaves the host.
"""
from __future__ import annotations

import os
import time

from fastapi import HTTPException

_windows: dict[str, list[float]] = {}

DEFAULTS = {
    "login": 60, "chat": 120, "upload": 60, "analyze": 120,
    "agent_run": 60, "voice": 120, "admin_config": 60,
    "work": 120, "analytics": 120, "reports": 60, "device": 120,
}


def _limit_for(name: str) -> int:
    try:
        return max(1, int(os.getenv(f"SOV_RATE_{name.upper()}", DEFAULTS[name])))
    except Exception:
        return DEFAULTS[name]


def check(name: str, key: str, user: dict | None = None) -> None:
    """Raise 429 when key exceeded its per-minute budget. Audit the denial."""
    limit = _limit_for(name)
    now = time.time()
    bucket = f"{name}:{key}"
    hits = [t for t in _windows.get(bucket, []) if now - t < 60.0]
    if len(hits) >= limit:
        from . import audit_log
        audit_log.append(user["id"] if user else None,
                         user["role"] if user else None,
                         "rate_limited", resource=name, decision="deny",
                         detail=f"limit={limit}/min")
        raise HTTPException(429, f"rate limit exceeded for {name} ({limit}/min)")
    hits.append(now)
    _windows[bucket] = hits


def reset() -> None:
    """Test helper: clear all windows."""
    _windows.clear()
